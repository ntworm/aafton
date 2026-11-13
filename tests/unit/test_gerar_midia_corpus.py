"""Testes do gerador de midia sintetica que o AAF referencia e nao traz.

O AAF de teste vem de ``tests/aaf_factory.py``: um WAV externo ligado a uma
track de audio. O WAV some antes do import, entao a midia so existe depois
que o gerador escreve o arquivo que o AAF pede.

O caso de MXF usa uma sessao de mentira com um asset .mxf, porque o ``aaf2``
so consegue ligar um MXF que existe de verdade; nenhum teste depende de
arquivos fora do Git.
"""

from __future__ import annotations

import array
import json
import shutil
import subprocess
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

from aafton.cli import main as importar
from aafton.media_extract import read_audio_metadata
from scripts import gerar_midia_corpus as gerador
from scripts.gerar_midia_corpus import _amostras_do_canal
from scripts.gerar_midia_corpus import main as gerar
from tests.aaf_factory import build_linked_aaf, wav_bytes

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "templates" / "live12.als"
SAMPLE_RATE = 48000
CLIQUE_AMOSTRAS = SAMPLE_RATE * 5 // 1000
CLIPS = [(0, 96000, 0), (192000, 48000, 24000)]
# O clip mais longo comeca em 0 e dura 96000 amostras; a folga e de um segundo.
FRAMES_ESPERADOS = 96000 + SAMPLE_RATE


def _cena(tmp_path: Path, nome: str = "cena12_tk3.wav") -> Path:
    """Um AAF ligado a um WAV que nao existe mais: a midia tem de ser gerada."""
    origem = tmp_path / "origem"
    origem.mkdir(parents=True, exist_ok=True)
    wav = origem / nome
    wav.write_bytes(wav_bytes(SAMPLE_RATE * 60))
    aaf = build_linked_aaf(origem / "cena.aaf", CLIPS, wav)
    wav.unlink()
    return aaf


def _relatorio(caminho: Path) -> dict:
    """O relatorio JSON da rodada."""
    return json.loads(caminho.read_text(encoding="utf-8"))


def _primeiro_nao_zero(amostras: array.array) -> int:
    """A posicao do primeiro valor diferente de zero; -1 quando o canal e silencio."""
    for posicao, valor in enumerate(amostras):
        if valor != 0:
            return posicao
    return -1


def test_wav_gerado_tem_nome_taxa_canais_e_duracao_esperados(tmp_path):
    """O WAV sai com o nome que o AAF pede, na taxa dele, com a duracao dos clips."""
    aaf = _cena(tmp_path)
    destino = tmp_path / "midia"

    assert gerar([str(aaf), str(destino)]) == 0

    arquivo = destino / "cena12_tk3.wav"
    assert arquivo.is_file()
    with wave.open(str(arquivo), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getframerate() == SAMPLE_RATE
        assert handle.getsampwidth() == 2
        assert handle.getnframes() == FRAMES_ESPERADOS


def test_clique_tem_5_ms_no_comeco_de_cada_segundo(tmp_path):
    """O clique de 5 ms abre cada segundo; o resto do segundo e silencio."""
    aaf = _cena(tmp_path)
    destino = tmp_path / "midia"

    assert gerar([str(aaf), str(destino)]) == 0

    with wave.open(str(destino / "cena12_tk3.wav"), "rb") as handle:
        amostras = array.array("h", handle.readframes(handle.getnframes()))
    assert any(amostras[:CLIQUE_AMOSTRAS])
    assert not any(amostras[CLIQUE_AMOSTRAS:SAMPLE_RATE])
    assert any(amostras[SAMPLE_RATE : SAMPLE_RATE + CLIQUE_AMOSTRAS])


def test_canal_n_recebe_o_clique_deslocado_n_ms():
    """Cada canal tem o clique deslocado de um ms por canal, para se distinguir."""
    milissegundos = SAMPLE_RATE // 1000
    inicio_0 = _primeiro_nao_zero(_amostras_do_canal(SAMPLE_RATE, 0))
    inicio_1 = _primeiro_nao_zero(_amostras_do_canal(SAMPLE_RATE, 1))
    inicio_2 = _primeiro_nao_zero(_amostras_do_canal(SAMPLE_RATE, 2))

    assert 0 <= inicio_0 < CLIQUE_AMOSTRAS
    assert milissegundos <= inicio_1 < milissegundos + CLIQUE_AMOSTRAS
    assert 2 * milissegundos <= inicio_2 < 2 * milissegundos + CLIQUE_AMOSTRAS


def test_import_estrito_com_a_midia_gerada_termina_com_codigo_zero(tmp_path):
    """O import estrito da CLI roda de ponta a ponta com a midia gerada."""
    aaf = _cena(tmp_path)
    destino = tmp_path / "midia"
    als = tmp_path / "saida.als"

    assert gerar([str(aaf), str(destino)]) == 0

    codigo = importar(
        [
            "import",
            str(aaf),
            str(als),
            "--template",
            str(TEMPLATE),
            "--audio-dir",
            str(destino),
            "--media",
            "link",
        ]
    )
    assert codigo == 0
    assert als.is_file()


def test_nome_aif_gera_aiff_lido_pelo_leitor_de_midia(tmp_path):
    """O sufixo .aif pede AIFF, e o leitor de midia do produto o reconhece."""
    aaf = _cena(tmp_path, nome="portadora.aif")
    destino = tmp_path / "midia"

    assert gerar([str(aaf), str(destino)]) == 0

    arquivo = destino / "portadora.aif"
    assert arquivo.is_file()
    meta = read_audio_metadata(arquivo)
    assert meta["format"] == "aiff"
    assert meta["channels"] == 1
    assert meta["sample_rate"] == SAMPLE_RATE
    assert meta["duration_samples"] == FRAMES_ESPERADOS


def test_arquivo_existente_nao_e_sobrescrito(tmp_path):
    """Um arquivo que ja existe na pasta fica como esta."""
    aaf = _cena(tmp_path)
    destino = tmp_path / "midia"
    destino.mkdir()
    (destino / "cena12_tk3.wav").write_bytes(b"conteudo antigo")
    relatorio = tmp_path / "relatorio.json"

    codigo = gerar([str(aaf), str(destino), "--json", str(relatorio)])

    assert codigo == 0
    assert (destino / "cena12_tk3.wav").read_bytes() == b"conteudo antigo"
    linha = _relatorio(relatorio)["assets"][0]
    assert linha["status"] == "existente"
    assert linha["filename"] == "cena12_tk3.wav"


def test_mxf_sem_ffmpeg_vira_pulado(tmp_path, monkeypatch):
    """Sem ffmpeg no PATH o asset de MXF e pulado, com o motivo no relatorio."""
    monkeypatch.setattr(shutil, "which", lambda comando: None)

    codigo, destino, relatorio = _rodada(tmp_path, monkeypatch, "cena.mxf", [0])

    assert codigo == 1
    assert not list(destino.glob("*.mxf"))
    linhas = relatorio["assets"]
    assert linhas
    for linha in linhas:
        assert linha["status"] == "pulado"
        assert linha["formato"] == "mxf"
        assert "ffmpeg" in linha["motivo"]


def test_relatorio_tem_uma_linha_por_asset(tmp_path):
    """O relatorio JSON traz os campos do asset e o desfecho do arquivo."""
    aaf = _cena(tmp_path)
    destino = tmp_path / "midia"
    relatorio = tmp_path / "relatorio.json"

    assert gerar([str(aaf), str(destino), "--json", str(relatorio)]) == 0

    dados = _relatorio(relatorio)
    assert len(dados["assets"]) == 1
    linha = dados["assets"][0]
    assert linha["filename"] == "cena12_tk3.wav"
    assert linha["sample_rate"] == SAMPLE_RATE
    assert linha["channels"] == 1
    assert linha["duration_frames"] == FRAMES_ESPERADOS
    assert linha["formato"] == "wav"
    assert linha["status"] == "gerado"
    assert linha["motivo"] == ""


def test_aaf_inexistente_e_erro_de_uso(tmp_path):
    """AAF que nao existe e erro de uso: codigo 2."""
    assert gerar([str(tmp_path / "nao_existe.aaf"), str(tmp_path / "midia")]) == 2


def test_linha_de_comando_gera_a_midia_e_o_relatorio(tmp_path):
    """O uso documentado roda: python scripts/gerar_midia_corpus.py AAF PASTA --json REL."""
    aaf = _cena(tmp_path)
    destino = tmp_path / "midia"
    relatorio = tmp_path / "relatorio.json"

    resultado = subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts" / "gerar_midia_corpus.py"),
            str(aaf),
            str(destino),
            "--json",
            str(relatorio),
        ],
        capture_output=True,
        text=True,
        cwd=REPO,
    )

    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
    assert (destino / "cena12_tk3.wav").is_file()
    assert _relatorio(relatorio)["resumo"]["gerado"] == 1


def _rodada(tmp_path, monkeypatch, nome: str, canais: list[int]):
    """Roda o gerador com uma sessao de mentira: um asset, um clip por canal."""
    asset = SimpleNamespace(
        id="m1", filename=nome, channels=None, sample_rate=SAMPLE_RATE, embedded_mob_id=None
    )
    clips = [
        SimpleNamespace(
            media_id="m1",
            channel_index=canal,
            duration_samples=SAMPLE_RATE,
            source_offset_samples=0,
        )
        for canal in canais
    ]
    sessao = SimpleNamespace(media=[asset], sample_rate=SAMPLE_RATE, all_clips=lambda: clips)
    aaf = _cena(tmp_path)
    monkeypatch.setattr(gerador, "read_aaf", lambda caminho, report: sessao)
    destino = tmp_path / "midia"
    relatorio = tmp_path / "relatorio.json"
    codigo = gerar([str(aaf), str(destino), "--json", str(relatorio)])
    return codigo, destino, _relatorio(relatorio)


def test_arquivo_usado_so_pelo_canal_direito_sai_mono(tmp_path, monkeypatch):
    """Um arquivo so usado pelo canal 1 da track e mono, nao stereo."""
    codigo, destino, relatorio = _rodada(tmp_path, monkeypatch, "voz.wav", [1])

    assert codigo == 0
    assert relatorio["assets"][0]["channels"] == 1
    with wave.open(str(destino / "voz.wav"), "rb") as handle:
        assert handle.getnchannels() == 1


def test_arquivo_usado_por_dois_canais_da_track_sai_estereo(tmp_path, monkeypatch):
    """Um arquivo usado pelos canais 0 e 1 da track sai stereo."""
    codigo, destino, relatorio = _rodada(tmp_path, monkeypatch, "par.wav", [0, 1])

    assert codigo == 0
    assert relatorio["assets"][0]["channels"] == 2
    with wave.open(str(destino / "par.wav"), "rb") as handle:
        assert handle.getnchannels() == 2


def test_mxf_usado_por_dois_canais_sai_mono(tmp_path, monkeypatch):
    """O MXF e sempre mono: o ffmpeg recusa MXF OP-Atom estereo."""
    _, _, relatorio = _rodada(tmp_path, monkeypatch, "midia.mxf", [0, 1])

    linha = relatorio["assets"][0]
    assert linha["formato"] == "mxf"
    assert linha["channels"] == 1
