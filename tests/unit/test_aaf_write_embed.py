"""Wiring do embed de midia dentro do AAF exportado.

Os testes exercitam o caminho real do escritor (write_aaf -> link_media ->
embed_wave) com WAVs sinteticos no disco: o que o AAF carrega, o que ele
recusa e o que ele nao deixa para tras. A gravacao da essencia nao e
mockada: o AAF exportado e reaberto e conferido byte a byte.

A fidelidade do formato embarcado e a ida e volta pelo CLI moram no
arquivo vizinho, test_aaf_embed_roundtrip.py.
"""

from __future__ import annotations

import struct
from pathlib import Path

import aaf2
import pytest

from aafton import aaf_write
from aafton.aaf_write import write_aaf
from aafton.formats.destino import write_destination
from aafton.ir import ClipIR, MediaAsset, SessionIR, TrackIR
from aafton.report import Report, StrictModeError
from aafton.timecode import TimecodeRate

RATE = 48000
GUID_PCM = bytes.fromhex("0100000000001000800000aa00389b71")
GUID_FLOAT = bytes.fromhex("0300000000001000800000aa00389b71")

# tag do fmt, subformato do extensivel, bits por amostra e float ou nao
FORMATOS = {
    "pcm16": (1, None, 16, False),
    "pcm24": (1, None, 24, False),
    "float32": (3, None, 32, True),
    "pcm24_ext": (0xFFFE, 1, 24, False),
    "float32_ext": (0xFFFE, 3, 32, True),
}


def _chunk(cid: bytes, payload: bytes) -> bytes:
    return cid + struct.pack("<I", len(payload)) + payload + b"\x00" * (len(payload) % 2)


def _fmt_payload(tag: int, subformat: int | None, channels: int, bits: int) -> bytes:
    width = bits // 8
    align = channels * width
    base = struct.pack("<HHIIHH", tag, channels, RATE, RATE * align, align, bits)
    if tag != 0xFFFE:
        return base
    guid = GUID_FLOAT if subformat == 3 else GUID_PCM
    return base + struct.pack("<HHI", 22, bits, 0b11) + guid


def _amostras(frames: int, channels: int, bits: int, is_float: bool) -> bytes:
    """Amostras deterministicas, distintas por canal."""
    if is_float:
        return b"".join(
            struct.pack("<f", ((i % 17) - 8) / 16.0 + canal)
            for i in range(frames)
            for canal in range(channels)
        )
    width = bits // 8
    return bytes(
        (i * 37 + canal * 91 + byte) % 256
        for i in range(frames)
        for canal in range(channels)
        for byte in range(width)
    )


def _wav(
    path: Path,
    *,
    formato: str = "pcm16",
    channels: int = 1,
    frames: int = 32,
    before: tuple = (),
    after: tuple = (),
) -> bytes:
    """WAV valido no disco e devolve os bytes escritos."""
    tag, subformat, bits, is_float = FORMATOS[formato]
    body = b"WAVE" + _chunk(b"fmt ", _fmt_payload(tag, subformat, channels, bits))
    for cid, payload in before:
        body += _chunk(cid, payload)
    body += _chunk(b"data", _amostras(frames, channels, bits, is_float))
    for cid, payload in after:
        body += _chunk(cid, payload)
    raw = b"RIFF" + struct.pack("<I", len(body)) + body
    path.write_bytes(raw)
    return raw


def _asset(asset_id: str, path: Path, channels: int, frames: int) -> MediaAsset:
    return MediaAsset(
        id=asset_id,
        filename=path.name,
        channels=channels,
        sample_rate=RATE,
        duration_samples=frames,
        status="resolved",
        resolved_path=str(path),
    )


def _sessao(assets, clips=None, *, channels: int = 1) -> SessionIR:
    """Sessao minima: uma track de audio com os clips pedidos."""
    if clips is None:
        clips = [(assets[0].id, 0, 16, 0)]
    track_clips = [
        ClipIR(
            id=f"c{index}",
            track_id="t1",
            channel_index=0,
            event_index=index,
            name="cena",
            media_id=media_id,
            timeline_start_samples=start,
            timeline_end_samples=start + duration,
            source_offset_samples=offset,
        )
        for index, (media_id, start, duration, offset) in enumerate(clips)
    ]
    track = TrackIR(
        id="t1",
        source_index=0,
        name="S",
        kind="audio",
        channel_count=channels,
        clips=track_clips,
    )
    return SessionIR(
        name="s",
        sample_rate=RATE,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=0,
        tracks=[track],
        media=list(assets),
    )


def _source_mobs_de_arquivo(f) -> list:
    """SourceMobs com WAVEDescriptor: os que carregam o arquivo de audio."""
    return [
        mob
        for mob in f.content.mobs
        if type(mob).__name__ == "SourceMob"
        and type(getattr(mob, "descriptor", None)).__name__ in ("WAVEDescriptor", "PCMDescriptor")
    ]


def _essencia(source) -> bytes:
    stream = source.essence.open()
    try:
        return stream.read()
    finally:
        stream.close()


def test_export_embed_mono_grava_essencia_e_remove_locator(tmp_path):
    """O AAF embarcado carrega o WAV inteiro e nao guarda Locator."""
    wav = tmp_path / "fonte.wav"
    raw = _wav(wav, formato="pcm16", channels=1, frames=32)
    session = _sessao([_asset("m", wav, 1, 32)])
    destino = tmp_path / "saida.aaf"

    report = Report(strict=False)
    write_aaf(session, destino, report, media="embed")

    assert destino.exists()
    assert not report.incomplete, [i.code for i in report.issues]
    with aaf2.open(str(destino), "r") as f:
        sources = _source_mobs_de_arquivo(f)
        assert len(sources) == 1
        source = sources[0]
        assert source.essence is not None
        assert _essencia(source) == raw
        assert [p.name for p in source.descriptor.properties()] != ["Locator"]
        assert all(prop.name != "Locator" for prop in source.descriptor.properties())
        assert len(list(f.content.essencedata)) == 1


def test_export_embed_nao_cria_pasta_de_media(tmp_path):
    """O embed nao deixa a pasta <stem> Media do lado do destino."""
    wav = tmp_path / "fonte.wav"
    _wav(wav, channels=1, frames=32)
    session = _sessao([_asset("m", wav, 1, 32)])
    destino = tmp_path / "saida.aaf"

    write_aaf(session, destino, Report(strict=False), media="embed")

    assert destino.exists()
    assert not (tmp_path / "saida Media").exists()


def test_export_link_preserva_locator_e_nao_cria_essencia(tmp_path):
    """O caminho link continuaReferenciando o arquivo e sem essencia."""
    wav = tmp_path / "fonte.wav"
    _wav(wav, channels=1, frames=32)
    session = _sessao([_asset("m", wav, 1, 32)])
    destino = tmp_path / "saida.aaf"

    write_aaf(session, destino, Report(strict=False))

    with aaf2.open(str(destino), "r") as f:
        source = _source_mobs_de_arquivo(f)[0]
        assert source.essence is None
        assert len(source.descriptor["Locator"].value) == 1
        assert not list(f.content.essencedata)


def test_write_aaf_recusa_modo_de_midia_desconhecido(tmp_path):
    """Um modo fora de link/embed e erro de programador, nao comportamento novo."""
    wav = tmp_path / "fonte.wav"
    _wav(wav, channels=1, frames=32)
    session = _sessao([_asset("m", wav, 1, 32)])

    with pytest.raises(ValueError, match="invalid media mode"):
        write_aaf(session, tmp_path / "saida.aaf", Report(strict=False), media="copiar")


def test_write_destination_ignora_media_fora_do_aaf(tmp_path):
    """Nos demais formatos a opcao media nao muda nada."""
    wav = tmp_path / "fonte.wav"
    _wav(wav, channels=1, frames=32)
    session = _sessao([_asset("m", wav, 1, 32)])
    destino = tmp_path / "saida.edl"

    write_destination(session, destino, Report(strict=False), media="embed")

    assert destino.exists()


def test_export_embed_recusa_midia_ausente_e_nao_cria_destino(tmp_path):
    """Preflight reprova antes de qualquer gravacao, tambem no permissivo."""
    ausente = tmp_path / "ausente.wav"
    asset = MediaAsset(
        id="m",
        filename="ausente.wav",
        channels=1,
        sample_rate=RATE,
        duration_samples=32,
        status="missing",
        resolved_path=str(ausente),
    )
    session = _sessao([asset])
    destino = tmp_path / "saida.aaf"

    with pytest.raises(StrictModeError):
        write_aaf(session, destino, Report(strict=True), media="embed")
    assert not destino.exists()

    report = Report(strict=False)
    write_aaf(session, destino, report, media="embed")
    assert report.incomplete
    assert not destino.exists()
    assert any(issue.code == "EMBED_MEDIA_MISSING" for issue in report.issues)


def test_export_embed_recusa_arquivo_que_nao_e_wav(tmp_path):
    """Um arquivo fora de RIFF/WAVE nao vira essencia."""
    invalido = tmp_path / "invalido.wav"
    invalido.write_bytes(b"NAO E UM RIFF" + b"\x00" * 64)
    asset = MediaAsset(
        id="m",
        filename=invalido.name,
        channels=1,
        sample_rate=RATE,
        duration_samples=32,
        status="resolved",
        resolved_path=str(invalido),
    )
    session = _sessao([asset])
    destino = tmp_path / "saida.aaf"

    report = Report(strict=False)
    write_aaf(session, destino, report, media="embed")

    assert report.incomplete
    assert not destino.exists()


def test_export_embed_recusa_clip_sem_asset_declarado(tmp_path):
    """Clip apontando um asset inexistente nao vira fallback no embed."""
    wav = tmp_path / "fonte.wav"
    _wav(wav, channels=1, frames=32)
    asset = _asset("m", wav, 1, 32)
    session = _sessao([asset], clips=[("nao_declarado", 0, 512, 0)])
    destino = tmp_path / "saida.aaf"

    report = Report(strict=False)
    write_aaf(session, destino, report, media="embed")

    assert report.incomplete
    assert not destino.exists()
    assert any(issue.code == "EMBED_CLIP_NO_ELIGIBLE_ASSET" for issue in report.issues)


def test_embed_limite_de_payload_impede_a_publicacao(tmp_path, monkeypatch):
    """O teto do payload e conferido antes de escrever essencia."""
    monkeypatch.setattr("aafton.aaf_write_embed.EMBED_LIMIT_BYTES", 16)
    wav = tmp_path / "fonte.wav"
    _wav(wav, channels=1, frames=32)
    session = _sessao([_asset("m", wav, 1, 32)])
    destino = tmp_path / "saida.aaf"

    with pytest.raises(StrictModeError):
        write_aaf(session, destino, Report(strict=True), media="embed")
    assert not destino.exists()

    report = Report(strict=False)
    write_aaf(session, destino, report, media="embed")
    assert report.incomplete
    assert not destino.exists()
    assert any(issue.code == "EXPORT_EMBED_EXCEEDS_2GB" for issue in report.issues)


def test_embed_limite_do_arquivo_impede_a_publicacao(tmp_path, monkeypatch):
    """O teto do AAF final tambem impede a publicacao, em ambos os modos."""
    real = aaf_write.check_embed_size

    def fake(size, report, *, stage="payload"):
        if stage == "file":
            report.error("EXPORT_EMBED_EXCEEDS_2GB", "limite final", {"stage": stage})
            return False
        return real(size, report, stage=stage)

    monkeypatch.setattr(aaf_write, "check_embed_size", fake)
    wav = tmp_path / "fonte.wav"
    _wav(wav, channels=1, frames=32)
    session = _sessao([_asset("m", wav, 1, 32)])
    destino = tmp_path / "saida.aaf"
    destino.write_bytes(b"original")

    with pytest.raises(StrictModeError):
        write_aaf(session, destino, Report(strict=True), media="embed")
    assert destino.read_bytes() == b"original"

    report = Report(strict=False)
    write_aaf(session, destino, report, media="embed")
    assert report.incomplete
    assert destino.read_bytes() == b"original"
    assert not list(tmp_path.glob(".saida-*.aaf"))


def test_embed_guarda_essencia_de_cada_alias(tmp_path):
    """Dois assets que apontam o mesmo arquivo viram duas essencias."""
    wav = tmp_path / "fonte.wav"
    _wav(wav, channels=1, frames=32)
    session = _sessao(
        [_asset("m1", wav, 1, 32), _asset("m2", wav, 1, 32)],
        clips=[("m1", 0, 16, 0), ("m2", 16, 16, 0)],
    )
    destino = tmp_path / "saida.aaf"

    write_aaf(session, destino, Report(strict=False), media="embed")

    with aaf2.open(str(destino), "r") as f:
        sources = _source_mobs_de_arquivo(f)
        assert len(sources) == 2
        assert len({str(source.mob_id) for source in sources}) == 2
        assert all(source.essence is not None for source in sources)


def test_embed_inclui_asset_estereo_declarado_e_nao_usado(tmp_path):
    """Asset stereo declarado e nao usado tambem entra no AAF, separado em mono."""
    usado = tmp_path / "usado.wav"
    _wav(usado, channels=1, frames=32)
    nao_usado = tmp_path / "nao_usado.wav"
    _wav(nao_usado, channels=2, frames=32)
    session = _sessao(
        [_asset("m1", usado, 1, 32), _asset("m2", nao_usado, 2, 32)],
        clips=[("m1", 0, 16, 0)],
    )
    destino = tmp_path / "saida.aaf"

    write_aaf(session, destino, Report(strict=False), media="embed")

    with aaf2.open(str(destino), "r") as f:
        sources = _source_mobs_de_arquivo(f)
        assert len(sources) == 3
        assert all(source.essence is not None for source in sources)


def test_embed_falha_na_copia_limpa_o_staging_e_preserva_o_destino(tmp_path, monkeypatch):
    """Falha ao copiar a essencia nao deixa arquivo parcial nem troca o destino."""
    from aafton import audio_split

    diretorios = []
    original_split = audio_split.split_stereo_source

    def spy_split(source, directory, **kwargs):
        diretorios.append(Path(directory))
        return original_split(source, directory, **kwargs)

    def boom(*args, **kwargs):
        raise OSError("falha de disco")

    monkeypatch.setattr(audio_split, "split_stereo_source", spy_split)
    monkeypatch.setattr("aafton.aaf_write_media.embed_wave", boom)
    wav = tmp_path / "fonte.wav"
    _wav(wav, channels=1, frames=32)
    session = _sessao([_asset("m", wav, 1, 32)])
    destino = tmp_path / "saida.aaf"
    destino.write_bytes(b"original")

    with pytest.raises(OSError, match="falha de disco"):
        write_aaf(session, destino, Report(strict=False), media="embed")

    assert destino.read_bytes() == b"original"
    assert not list(tmp_path.glob(".saida-*.aaf"))
    assert all(not Path(diretorio).exists() for diretorio in diretorios)


def test_embed_falha_no_selfcheck_nao_publica_em_permissivo(tmp_path, monkeypatch):
    """Falha na conferencia impede a publicacao mesmo com destino novo."""

    def fake_check(path, report):
        report.error("EXPORT_SELF_CHECK_FAILED", "falha no self-check", {})

    monkeypatch.setattr(aaf_write, "check_exported_aaf", fake_check)
    wav = tmp_path / "fonte.wav"
    _wav(wav, channels=1, frames=32)
    session = _sessao([_asset("m", wav, 1, 32)])
    destino = tmp_path / "saida.aaf"

    report = Report(strict=False)
    write_aaf(session, destino, report, media="embed")

    assert report.incomplete
    assert not destino.exists()
    assert not list(tmp_path.glob(".saida-*.aaf"))
