"""Inspect de um AAF com a midia resolvida: chaves, extracao e erros de saida.

O fixture phaseslip traz o AAF ligado, o JSON das edicoes e o gabarito das
chaves do inspect. O AAF embutido e a midia da sonda ficam na pasta dela:
sem a variavel AAFTON_SONDA_DIR os testes que dependem dela pulam.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import wave
from pathlib import Path

import pytest

from aafton.aaf_write import write_aaf
from aafton.cli import run_cli
from aafton.ir import ClipIR, MediaAsset, SessionIR, TrackIR
from aafton.report import Report
from aafton.timecode import TimecodeRate

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "phaseslip"
LIGADO = FIXTURES / "SONDA_P0_ligado.aaf"
GABARITO = json.loads((FIXTURES / "inspect-chaves.json").read_text(encoding="utf-8"))

SEGUNDOS = 48000


def _cli(argv: list[str]) -> tuple[int, str, str]:
    """Roda a linha de comando no processo e devolve o codigo e as duas saidas."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = run_cli(argv)
    return code, out.getvalue(), err.getvalue()


def _sonda() -> Path:
    """A pasta da sonda, com o AAF embutido e a midia ligada."""
    bruto = os.environ.get("AAFTON_SONDA_DIR")
    if not bruto:
        pytest.skip("AAFTON_SONDA_DIR nao esta no ambiente: a sonda traz o AAF embutido e a midia")
    pasta = Path(bruto)
    if not (pasta / "SONDA_P0_embutido.aaf").is_file():
        pytest.skip(f"AAFTON_SONDA_DIR nao tem o AAF embutido: {pasta}")
    return pasta


def _confere_gabarito(dados: dict) -> None:
    """Confere as chaves do gabarito no JSON do inspect."""
    for chave in GABARITO["session"]:
        assert chave in dados, f"a sessao do inspect nao tem a chave {chave!r}"
    for media in dados["media"]:
        for chave in GABARITO["media"]:
            assert chave in media, f"a midia {media['id']!r} nao tem a chave {chave!r}"
    for track in dados["tracks"]:
        for chave in GABARITO["track"]:
            assert chave in track, f"a track {track['id']!r} nao tem a chave {chave!r}"
        for clip in track["clips"]:
            for chave in GABARITO["clip"]:
                assert chave in clip, f"o clip {clip['id']!r} nao tem a chave {chave!r}"


def _aaf_com_midia_que_sumiu(destino: Path) -> Path:
    """Escreve um AAF e apaga o arquivo de midia que ele aponta.

    O AAF fica valido e linka um arquivo que nao existe mais: e o caso do
    set cuja midia foi movida depois do export.
    """
    wav = destino.parent / "sumiu.wav"
    with wave.open(str(wav), "wb") as handle:
        handle.setparams((1, 2, SEGUNDOS, 0, "NONE", "not compressed"))
        handle.writeframes(b"\0\0" * SEGUNDOS)
    asset = MediaAsset(
        id="m0",
        filename=wav.name,
        channels=1,
        sample_rate=SEGUNDOS,
        duration_samples=SEGUNDOS,
        status="resolved",
        declared_location=str(wav),
        resolved_path=str(wav),
    )
    clip = ClipIR(
        id="c0",
        track_id="t0",
        channel_index=0,
        event_index=0,
        name="c0",
        media_id="m0",
        timeline_start_samples=0,
        timeline_end_samples=SEGUNDOS,
        source_offset_samples=0,
    )
    track = TrackIR(
        id="t0",
        source_index=0,
        name="Track 0",
        kind="audio",
        channel_count=1,
        clips=[clip],
    )
    session = SessionIR(
        name="Sem midia",
        sample_rate=SEGUNDOS,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=0,
        tracks=[track],
        media=[asset],
    )
    write_aaf(session, destino, Report(strict=False), media="link", composition_name="Sem midia")
    wav.unlink()
    return destino


def test_inspect_do_ligado_tem_as_chaves_do_gabarito() -> None:
    """O JSON do AAF ligado tem as chaves que o PhaseSlip usa."""
    code, out, err = _cli(["inspect", str(LIGADO)])
    assert code == 0, err
    dados = json.loads(out)
    _confere_gabarito(dados)
    assert dados["name"] == "SONDA_P0"
    assert dados["sample_rate"] == 48000
    assert dados["timecode_rate"] == {"num": 30000, "den": 1001, "drop_frame": True}
    assert dados["errors"] == []


def test_inspect_do_embutido_tem_as_chaves_do_gabarito() -> None:
    """O JSON do AAF embutido tem as mesmas chaves do AAF ligado."""
    sonda = _sonda()
    code, out, err = _cli(["inspect", str(sonda / "SONDA_P0_embutido.aaf")])
    assert code == 0, err
    dados = json.loads(out)
    _confere_gabarito(dados)
    assert dados["name"] == "SONDA_P0"
    assert dados["timecode_rate"] == {"num": 30000, "den": 1001, "drop_frame": True}


def test_inspect_do_ligado_diz_que_a_midia_esta_fora() -> None:
    """No AAF ligado as cinco midias sao links, nenhuma carrega essencia."""
    code, out, err = _cli(["inspect", str(LIGADO)])
    assert code == 0, err
    dados = json.loads(out)
    assert len(dados["media"]) == 5
    assert [media["embedded"] for media in dados["media"]] == [False] * 5


def test_inspect_do_embutido_diz_que_a_midia_esta_dentro() -> None:
    """No AAF embutido as nove midias carregam a essencia dentro do arquivo."""
    sonda = _sonda()
    code, out, err = _cli(["inspect", str(sonda / "SONDA_P0_embutido.aaf")])
    assert code == 0, err
    dados = json.loads(out)
    assert len(dados["media"]) == 9
    assert [media["embedded"] for media in dados["media"]] == [True] * 9


def test_inspect_com_resolve_media_resolve_toda_a_midia() -> None:
    """Com --resolve-media o AAF ligado diz onde esta cada arquivo e seu formato."""
    sonda = _sonda()
    code, out, err = _cli(
        [
            "inspect",
            "--resolve-media",
            "--audio-dir",
            str(sonda / "midia"),
            str(sonda / "SONDA_P0_ligado.aaf"),
        ]
    )
    assert code == 0, err
    dados = json.loads(out)
    assert len(dados["media"]) == 5
    for media in dados["media"]:
        assert media["status"] == "resolved", media
        assert media["resolved_path"], media
        assert media["sample_rate"] == 48000, media
        assert media["duration_samples"] == 1401400, media
        assert media["channels"] in (1, 2, 4), media
    assert dados["errors"] == []


def test_inspect_com_resolve_media_extrai_a_midia_embutida(tmp_path: Path) -> None:
    """Com --extract-dir o AAF embutido deixa os nove WAVs no disco."""
    sonda = _sonda()
    extracao = tmp_path / "extraidos"
    code, out, err = _cli(
        [
            "inspect",
            "--resolve-media",
            "--extract-dir",
            str(extracao),
            str(sonda / "SONDA_P0_embutido.aaf"),
        ]
    )
    assert code == 0, err
    dados = json.loads(out)
    assert len(list(extracao.glob("*.wav"))) == 9
    assert [media["status"] for media in dados["media"]] == ["resolved"] * 9
    assert [media["embedded"] for media in dados["media"]] == [True] * 9


def test_inspect_cria_a_pasta_de_extracao_no_ligado(tmp_path: Path) -> None:
    """A pasta de extracao e criada mesmo quando o AAF nao tem essencia dentro."""
    extracao = tmp_path / "vazia"
    code, out, err = _cli(
        ["inspect", "--resolve-media", "--extract-dir", str(extracao), str(LIGADO)]
    )
    assert code == 0, err
    assert extracao.is_dir()
    assert list(extracao.glob("*.wav")) == []


def test_inspect_aceita_a_opcao_json() -> None:
    """A opcao --json e aceita e o inspect continua imprimindo o JSON."""
    code, out, err = _cli(["inspect", "--json", str(LIGADO)])
    assert code == 0, err
    dados = json.loads(out)
    assert dados["name"] == "SONDA_P0"


def test_inspect_de_aaf_quebrado_sai_com_erro(tmp_path: Path) -> None:
    """Um AAF que nao abre sai com 1 e diz o erro no JSON."""
    quebrado = tmp_path / "quebrado.aaf"
    quebrado.write_bytes(b"isto nao e um aaf" * 64)
    code, out, err = _cli(["inspect", str(quebrado)])
    assert code == 1
    dados = json.loads(out)
    assert dados["errors"], "o inspect de um AAF quebrado tem de dizer o erro"
    assert dados["errors"][0]["code"] == "AAF_UNREADABLE"
    assert dados["errors"][0]["stage"] == "read"
    assert "AAF_UNREADABLE" in err


def test_inspect_de_aaf_com_midia_ausente_sai_com_zero(tmp_path: Path) -> None:
    """A midia que nao resolve e um erro da etapa da midia: o inspect sai com 0."""
    aaf = _aaf_com_midia_que_sumiu(tmp_path / "sem_midia.aaf")
    code, out, err = _cli(["inspect", "--resolve-media", str(aaf)])
    assert code == 0, err
    dados = json.loads(out)
    assert dados["media"][0]["status"] == "missing"
    erros = dados["errors"]
    assert erros, "o inspect tem de dizer que a midia nao foi encontrada"
    assert [erro["stage"] for erro in erros] == ["media"]
    assert erros[0]["code"] == "MEDIA_MISSING"


def test_inspect_da_dois_leituras_com_os_mesmos_ids_de_clip() -> None:
    """Duas leituras do mesmo AAF dao os mesmos ids de clip."""
    primeira = json.loads(_cli(["inspect", str(LIGADO)])[1])
    segunda = json.loads(_cli(["inspect", str(LIGADO)])[1])
    ids_primeira = [clip["id"] for track in primeira["tracks"] for clip in track["clips"]]
    ids_segunda = [clip["id"] for track in segunda["tracks"] for clip in track["clips"]]
    assert ids_primeira == ids_segunda
    assert len(ids_primeira) == 9


def test_inspect_do_ligado_le_o_timecode_de_29_97_drop_frame() -> None:
    """A sonda declara 30000/1001 com drop frame; o inspect devolve o que o AAF diz."""
    code, out, err = _cli(["inspect", str(LIGADO), "--json"])
    assert code == 0, err
    assert json.loads(out)["timecode_rate"] == {"num": 30000, "den": 1001, "drop_frame": True}
