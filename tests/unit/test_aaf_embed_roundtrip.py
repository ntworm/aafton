"""Fidelidade do embed de midia e ida e volta do produto.

Este arquivo junta duas perguntas que so fazem sentido juntas:

- O que foi embarcado no AAF continua sendo o que estava no disco? O formato
  do Summary, o codec do link, os metadados do WAV e as amostras batem byte a
  byte com a origem.
- O caminho completo entrega o audio de volta? Export embed, remocao dos
  originais e import produzem um unico AudioClip estereo no ALS, com a
  geometria do clip e as amostras originais.

Os helpers de WAV, de sessao e de leitura de RIFF sao reaproveitados do
arquivo vizinho, para que exista uma unica definicao de fixture na suite.
"""

from __future__ import annotations

import gzip
import json
import struct
import wave
import xml.etree.ElementTree as ET
from pathlib import Path

import aaf2
import pytest

from aafton.aaf_read import read_aaf
from aafton.aaf_write import write_aaf
from aafton.als_read import read_session
from aafton.als_write import WriteOptions, write_session
from aafton.cli import main
from aafton.report import Report

from tests.unit.test_aaf_write_embed import (
    FORMATOS,
    RATE,
    _amostras,
    _asset,
    _essencia,
    _sessao,
    _source_mobs_de_arquivo,
    _wav,
)


def _chunks(dados: bytes) -> list:
    """Chunks (id, offset, tamanho) de um RIFF, lidos sem o codigo do produto."""
    pos, achados = 12, []
    while pos + 8 <= len(dados):
        cid = dados[pos : pos + 4]
        size = struct.unpack_from("<I", dados, pos + 4)[0]
        achados.append((cid, pos + 8, size))
        pos += 8 + size + (size % 2)
    return achados


def _payload(dados: bytes, cid: bytes) -> bytes:
    for chunk_id, offset, size in _chunks(dados):
        if chunk_id == cid:
            return dados[offset : offset + size]
    return b""


def _ids(dados: bytes) -> list:
    return [chunk_id for chunk_id, _, _ in _chunks(dados)]


def _fmt_da_summary(summary: bytes) -> tuple[int, int]:
    """Tag e bits do chunk fmt dentro do Summary gravado pelo pyaaf2."""
    pos = summary.find(b"fmt ")
    assert pos >= 0, "o Summary nao tem chunk fmt"
    inicio = pos + 8
    tag = struct.unpack_from("<H", summary, inicio)[0]
    bits = struct.unpack_from("<H", summary, inicio + 14)[0]
    return tag, bits


def _no_sidecar(dados, chave):
    """Procura uma chave do sidecar em qualquer nivel da estrutura."""
    if isinstance(dados, dict):
        if chave in dados:
            return dados[chave]
        for valor in dados.values():
            achado = _no_sidecar(valor, chave)
            if achado is not None:
                return achado
    elif isinstance(dados, list):
        for valor in dados:
            achado = _no_sidecar(valor, chave)
            if achado is not None:
                return achado
    return None


def _mono(frames: int, canal: int) -> bytes:
    """As amostras de um canal, como a fixture do arquivo vizinho as gera."""
    return bytes((i * 37 + canal * 91 + byte) % 256 for i in range(frames) for byte in range(2))


@pytest.mark.parametrize("formato", sorted(FORMATOS))
def test_export_embed_segue_o_formato_real_do_wav(tmp_path, formato):
    """O Summary do pyaaf2 reflete o WAV real: extensible continua extensible."""
    wav = tmp_path / "fonte.wav"
    _wav(wav, formato=formato, channels=1, frames=32)
    session = _sessao([_asset("m", wav, 1, 32)])
    destino = tmp_path / "saida.aaf"

    write_aaf(session, destino, Report(strict=False), media="embed")

    with aaf2.open(str(destino), "r") as f:
        source = _source_mobs_de_arquivo(f)[0]
        tag, bits = _fmt_da_summary(bytes(source.descriptor["Summary"].value))
    esperado_tag, _sub, esperado_bits, _float = FORMATOS[formato]
    assert (tag, bits) == (esperado_tag, esperado_bits)


def test_embed_manda_o_codec_real_para_o_link(tmp_path, monkeypatch):
    """O dado de link no embed descreve o WAV: IEEE float vira pcm_f32le."""
    with aaf2.open(str(tmp_path / "scratch.aaf"), "w") as scratch:
        fabrica = type(scratch.content)
    original = fabrica.link_external_wav
    metas = []

    def spy(self, metadata):
        metas.append(metadata)
        return original(self, metadata)

    monkeypatch.setattr(fabrica, "link_external_wav", spy)
    wav = tmp_path / "fonte.wav"
    _wav(wav, formato="float32", channels=1, frames=32)
    session = _sessao([_asset("m", wav, 1, 32)])

    write_aaf(session, tmp_path / "saida.aaf", Report(strict=False), media="embed")

    assert len(metas) == 1
    stream = metas[0]["streams"][0]
    assert stream["codec_name"] == "pcm_f32le"
    assert stream["channels"] == 1
    assert stream["sample_rate"] == RATE
    assert stream["duration_ts"] == 32


def test_export_embed_estereo_separa_monos_e_preserva_metadata(tmp_path):
    """O stereo vira dois monos, cada um com bext e iXML e as amostras do canal."""
    bext = b"descricao".ljust(256, b"\x00") + b"origem".ljust(32, b"\x00")
    ixml = b"<BWFXML><PROJECT>p</PROJECT></BWFXML>"
    wav = tmp_path / "estereo.wav"
    _wav(
        wav,
        formato="pcm16",
        channels=2,
        frames=32,
        before=((b"bext", bext),),
        after=((b"iXML", ixml),),
    )
    session = _sessao([_asset("m", wav, 2, 32)], channels=2)
    destino = tmp_path / "saida.aaf"

    write_aaf(session, destino, Report(strict=False), media="embed")

    with aaf2.open(str(destino), "r") as f:
        sources = _source_mobs_de_arquivo(f)
        assert len(sources) == 2
        payloads = [_essencia(source) for source in sources]

    for payload, canal in zip(payloads, (0, 1)):
        assert _ids(payload) == [b"fmt ", b"bext", b"data", b"iXML"]
        assert _payload(payload, b"bext") == bext
        assert _payload(payload, b"iXML") == ixml
        esperado = bytes((i * 37 + canal * 91 + byte) % 256 for i in range(32) for byte in range(2))
        assert _payload(payload, b"data") == esperado
        fmt = _payload(payload, b"fmt ")
        assert struct.unpack_from("<H", fmt, 0)[0] == 1
        assert struct.unpack_from("<H", fmt, 2)[0] == 1


def test_export_embed_ponta_a_ponta_importa_sem_os_arquivos_originais(tmp_path):
    """Export embed, remocao dos originais e import extraem o audio de volta."""
    frames = 1024
    wav = tmp_path / "estereo.wav"
    _wav(wav, formato="pcm16", channels=2, frames=frames)
    session = _sessao(
        [_asset("m", wav, 2, frames)],
        clips=[("m", 7, 512, 99)],
        channels=2,
    )
    als = tmp_path / "projeto.als"
    write_session(
        session,
        Path("templates/live12.als"),
        als,
        WriteOptions(sample_rate=RATE, bpm=60, live_origin_samples=0, stereo="pair"),
        Report(strict=False),
    )
    aaf = tmp_path / "projeto.aaf"

    assert main(["export", str(als), str(aaf), "--media", "embed"]) == 0
    assert aaf.exists()

    # So os arquivos sinteticos de origem saem do disco.
    wav.unlink()

    lido = read_aaf(aaf, Report(strict=False), fades="keep")
    assert lido.tracks[0].channel_count == 2
    assert [
        (c.channel_index, c.timeline_start_samples, c.timeline_end_samples, c.source_offset_samples)
        for c in lido.tracks[0].clips
    ] == [(0, 7, 519, 99), (1, 7, 519, 99)]

    importado = tmp_path / "importado.als"
    assert (
        main(
            [
                "import",
                str(aaf),
                str(importado),
                "--media",
                "extract",
                "--stereo",
                "pair",
                "--fades",
                "keep",
                "--live-origin",
                "00:00:00:00",
            ]
        )
        == 0
    )

    root = ET.fromstring(gzip.decompress(importado.read_bytes()))
    clips = root.findall(".//Tracks/AudioTrack//AudioClip")
    assert len(clips) == 1, "o import deveria gerar um unico AudioClip estereo"
    caminhos = sorted(
        {
            elem.get("Value", "")
            for elem in root.iter("Path")
            if elem.get("Value", "").lower().endswith(".wav")
        }
    )
    assert len(caminhos) == 1, caminhos
    extraido = Path(caminhos[0])
    assert extraido.is_absolute()
    with wave.open(str(extraido), "rb") as handle:
        assert handle.getnchannels() == 2
        assert handle.getframerate() == RATE
        assert handle.getnframes() == frames
        assert handle.readframes(frames) == _amostras(frames, 2, 16, False)

    # A geometria do clip sobrevive no ALS: inicio, fim e offset da fonte.
    lido_als = read_session(importado, Report(strict=False))
    clip_als = lido_als.tracks[0].clips[0]
    assert clip_als.timeline_start_samples == 7
    assert clip_als.timeline_end_samples == 519
    assert clip_als.source_offset_samples == 99

    sidecar = json.loads((tmp_path / "importado.aafton.json").read_text(encoding="utf-8"))
    assert _no_sidecar(sidecar, "track_channels") == {"S": 2}
    assert _no_sidecar(sidecar, "clip_ranges") == [{"track": "S", "ranges": [[7, 519]]}]


def test_export_embed_extrai_as_duas_essencias_mono(tmp_path):
    """Sem os originais no disco, o AAF ainda devolve as duas essencias mono."""
    frames = 1024
    wav = tmp_path / "estereo.wav"
    _wav(wav, formato="pcm16", channels=2, frames=frames)
    session = _sessao(
        [_asset("m", wav, 2, frames)],
        clips=[("m", 7, 512, 99)],
        channels=2,
    )
    destino = tmp_path / "saida.aaf"

    write_aaf(session, destino, Report(strict=False), media="embed")

    wav.unlink()

    lido = read_aaf(destino, Report(strict=False), extract_dir=tmp_path / "essencias")
    clips = {clip.channel_index: clip for clip in lido.tracks[0].clips}
    assert sorted(clips) == [0, 1]
    for canal in (0, 1):
        asset = lido.media_by_id(clips[canal].media_id)
        assert asset is not None
        caminho = Path(asset.resolved_path)
        assert caminho.is_absolute()
        with wave.open(str(caminho), "rb") as handle:
            assert handle.getnchannels() == 1
            assert handle.getframerate() == RATE
            assert handle.getnframes() == frames
            assert handle.readframes(frames) == _mono(frames, canal)
