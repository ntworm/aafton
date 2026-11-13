"""Testes da opcao --ltc do import (fase 2 do LTC).

A opcao ainda nao existe no produto: todo teste deste arquivo deve falhar
ate que src/aafton/ltc_track.py e a opcao --ltc existam. As regras
verificadas aqui sao as congeladas da fase 2:

- a track gerada se chama LTC, tem um clip sem warp e nasce no WAV de LTC;
- o WAV cobre do primeiro frame depois da origem ate 10 s depois do ultimo
  clip, com o time_reference BWF dois samples antes do inicio do frame;
- o sidecar marca a track com "generated": "ltc" e nada mais;
- o relatorio do import avisa LTC_TRACK_WRITTEN;
- ler o set de volta descarta a track gerada (LTC_TRACK_SKIPPED).
"""

import json
import math
from fractions import Fraction
from pathlib import Path

import pytest

from aafton.aaf_read import read_aaf
from aafton.als_read import read_session
from aafton.als_xml import load_als
from aafton.cli import main
from aafton.media_extract import parse_bext
from aafton.report import Report
from aafton.sidecar import sidecar_path_for
from aafton.timecode import TimecodeRate, frames_to_label, frames_to_samples_exact
from tests.aaf_factory import build_linked_aaf, wav_bytes
from tests.unit.test_ltc import _decodificar, _ler_wav

TEMPLATE = Path("templates/live12.als")
SR = 48000
PRE_ROLL_SEGUNDOS = 10
# Textos que o comando ltc aceita, por taxa real (ver parse_ltc_rate).
TEXTOS_DA_TAXA = {
    Fraction(24000, 1001): "23.976",
    Fraction(24, 1): "24",
    Fraction(25, 1): "25",
    Fraction(30000, 1001): "29.97",
    Fraction(30, 1): "30",
}


@pytest.fixture
def wav(tmp_path):
    caminho = tmp_path / "cena12_tk3.wav"
    caminho.write_bytes(wav_bytes(48000 * 120))
    return caminho


@pytest.fixture
def aaf(tmp_path, wav):
    return build_linked_aaf(tmp_path / "cena12.aaf", [(0, 96000, 24000), (192000, 96000, 0)], wav)


def _codigo_cli(argumentos):
    """Codigo de saida do CLI, aceitando o SystemExit que o argparse levanta."""
    try:
        return main(argumentos)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1


def _importar(aaf, saida, relatorio=None, opcoes=()):
    """Roda o import com o template do projeto e devolve o codigo de saida."""
    argumentos = [
        "import",
        str(aaf),
        str(saida),
        "--template",
        str(TEMPLATE),
        "--media",
        "link",
    ]
    if relatorio is not None:
        argumentos += ["--report", str(relatorio)]
    return _codigo_cli(argumentos + list(opcoes))


def _sidecar(saida):
    """Le o sidecar JSON que o import gravou ao lado do .als."""
    return json.loads(sidecar_path_for(saida).read_text(encoding="utf-8"))


def _tracks_do_live(saida):
    """Todas as AudioTrack do set gerado."""
    return load_als(saida).findall("./LiveSet/Tracks/AudioTrack")


def _nome_da_track(node):
    """Nome de uma track do Live, lido do EffectiveName."""
    alvo = node.find("Name/EffectiveName")
    return alvo.get("Value", "") if alvo is not None else ""


def _taxa(sidecar):
    """Taxa de frames da sessao, lida do sidecar."""
    dados = sidecar["timecode_rate"]
    return TimecodeRate(dados["num"], dados["den"], dados["drop_frame"])


def _texto_da_taxa(rate):
    """Texto da taxa no formato que o parse_ltc_rate entende."""
    if rate.drop_frame:
        return "29.97df"
    return TEXTOS_DA_TAXA[rate.fps]


def _primeiro_frame(alvo, rate, sample_rate):
    """Menor indice de frame cujo inicio exato alcanca o alvo, em samples."""
    por_frame = Fraction(sample_rate * rate.den, rate.num)
    return math.ceil(Fraction(alvo) / por_frame)


def _maior_fim_de_clip(aaf, origem):
    """Maior timeline_end_samples de todos os clips do AAF de origem."""
    session = read_aaf(aaf, Report(strict=False))
    fins = [clip.timeline_end_samples for clip in session.all_clips()]
    return max(fins) if fins else origem


def _limites(aaf, sidecar, rate):
    """F0 e F1 do LTC, pelas regras congeladas da fase 2."""
    origem = sidecar["live_origin_samples"]
    maior_fim = _maior_fim_de_clip(aaf, origem)
    f0 = _primeiro_frame(origem + 2, rate, SR)
    f1 = _primeiro_frame(maior_fim + PRE_ROLL_SEGUNDOS * SR, rate, SR)
    return f0, f1


def _nome_do_wav(f0, rate):
    """Nome do WAV de LTC gerado pelo import."""
    rotulo = frames_to_label(f0, rate).replace(":", "-").replace(";", "-")
    return f"LTC_{rotulo}_{_texto_da_taxa(rate)}.wav"


def _pasta_de_midia(saida):
    """Pasta de midia que o import usa ao lado do .als."""
    return saida.parent / f"{saida.stem} Project" / "Samples" / "Imported"


@pytest.mark.skipif(not TEMPLATE.exists(), reason="Portao B: template ainda nao autorado")
def test_import_com_ltc_cria_a_track_de_timecode(aaf, tmp_path):
    saida = tmp_path / "saida.als"
    assert _importar(aaf, saida, opcoes=["--ltc"]) == 0

    tracks = [t for t in _tracks_do_live(saida) if _nome_da_track(t) == "LTC"]
    assert len(tracks) == 1
    assert len(tracks[0].findall(".//AudioClip")) == 1

    sidecar = _sidecar(saida)
    gerados = [b for b in sidecar["track_bindings"] if "generated" in b]
    assert len(gerados) == 1
    assert gerados[0]["generated"] == "ltc"
    assert gerados[0]["track_name"] == "LTC"

    rate = _taxa(sidecar)
    f0, f1 = _limites(aaf, sidecar, rate)
    wav = _pasta_de_midia(saida) / _nome_do_wav(f0, rate)
    assert wav.exists()

    bext = parse_bext(wav)
    assert bext is not None
    assert bext["time_reference"] == math.floor(frames_to_samples_exact(f0, rate, SR)) - 2

    taxa, amostras = _ler_wav(wav)
    assert taxa == SR
    decodificados = _decodificar(amostras, rate, taxa)
    assert decodificados[0][1] == frames_to_label(f0, rate)
    assert len(decodificados) == f1 - f0


@pytest.mark.skipif(not TEMPLATE.exists(), reason="Portao B: template ainda nao autorado")
def test_import_com_ltc_avisa_no_relatorio(aaf, tmp_path):
    saida = tmp_path / "saida.als"
    relatorio = tmp_path / "relatorio.json"
    assert _importar(aaf, saida, relatorio=relatorio, opcoes=["--ltc"]) == 0

    dados = json.loads(relatorio.read_text(encoding="utf-8"))
    entradas = [i for i in dados["issues"] if i["code"] == "LTC_TRACK_WRITTEN"]
    assert len(entradas) == 1
    assert entradas[0]["severity"] == "info"

    contexto = entradas[0]["context"]
    assert {"start", "rate", "level_dbfs", "path"} <= set(contexto)
    sidecar = _sidecar(saida)
    rate = _taxa(sidecar)
    f0 = _limites(aaf, sidecar, rate)[0]
    assert contexto["start"] == frames_to_label(f0, rate)
    assert "25" in str(contexto["rate"])
    assert float(contexto["level_dbfs"]) == pytest.approx(-18.0)
    assert contexto["path"].endswith(_nome_do_wav(f0, rate))


@pytest.mark.skipif(not TEMPLATE.exists(), reason="Portao B: template ainda nao autorado")
def test_import_sem_ltc_nao_deixa_nada_gerado(aaf, tmp_path):
    saida = tmp_path / "saida.als"
    assert _importar(aaf, saida) == 0

    nomes = [_nome_da_track(t) for t in _tracks_do_live(saida)]
    assert "LTC" not in nomes

    bindings = _sidecar(saida).get("track_bindings", [])
    assert all("generated" not in b for b in bindings)


@pytest.mark.skipif(not TEMPLATE.exists(), reason="Portao B: template ainda nao autorado")
def test_export_de_set_com_ltc_deixa_a_track_gerada_de_fora(aaf, tmp_path):
    com_ltc = tmp_path / "com_ltc.als"
    sem_ltc = tmp_path / "sem_ltc.als"
    assert _importar(aaf, com_ltc, opcoes=["--ltc"]) == 0
    assert _importar(aaf, sem_ltc) == 0

    relatorio = tmp_path / "export.json"
    aaf_com = tmp_path / "com_ltc.aaf"
    aaf_sem = tmp_path / "sem_ltc.aaf"
    assert _codigo_cli(["export", str(com_ltc), str(aaf_com), "--report", str(relatorio)]) == 0
    assert _codigo_cli(["export", str(sem_ltc), str(aaf_sem)]) == 0

    dados = json.loads(relatorio.read_text(encoding="utf-8"))
    assert any(i["code"] == "LTC_TRACK_SKIPPED" for i in dados["issues"])

    sessao_com = read_aaf(aaf_com, Report(strict=False))
    sessao_sem = read_aaf(aaf_sem, Report(strict=False))
    assert len(sessao_com.tracks) == len(sessao_sem.tracks)
    assert all(t.name != "LTC" for t in sessao_com.tracks)


@pytest.mark.skipif(not TEMPLATE.exists(), reason="Portao B: template ainda nao autorado")
def test_verify_do_aaf_de_origem_aprova_o_set_com_ltc(aaf, tmp_path):
    com_ltc = tmp_path / "com_ltc.als"
    sem_ltc = tmp_path / "sem_ltc.als"
    assert _importar(aaf, com_ltc, opcoes=["--ltc"]) == 0
    assert _importar(aaf, sem_ltc) == 0
    assert _codigo_cli(["verify", str(aaf), str(com_ltc)]) == 0
    assert _codigo_cli(["verify", str(aaf), str(sem_ltc)]) == 0


@pytest.mark.skipif(not TEMPLATE.exists(), reason="Portao B: template ainda nao autorado")
def test_read_session_descarta_a_track_gerada(aaf, tmp_path):
    saida = tmp_path / "saida.als"
    assert _importar(aaf, saida, opcoes=["--ltc"]) == 0

    report = Report(strict=False)
    session = read_session(saida, report)
    assert all(t.name != "LTC" for t in session.tracks)
    assert any(i.code == "LTC_TRACK_SKIPPED" for i in report.issues)
