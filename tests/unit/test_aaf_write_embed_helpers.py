"""Testes unitarios para helpers de embed de midia no AAF."""

from __future__ import annotations

import struct
from pathlib import Path
from unittest.mock import patch

import aaf2
import pytest

from aafton.aaf_write_embed import (
    EMBED_LIMIT_BYTES,
    check_embed_size,
    embed_assets,
    embed_payload_size,
    embed_wave,
    preflight_embed,
    verify_embedded_aaf,
)
from aafton.audio_split import _iter_riff_chunks
from aafton.ir import ClipIR, MediaAsset, SessionIR, TrackIR
from aafton.report import Report, StrictModeError
from aafton.timecode import TimecodeRate


def _make_wav(path: Path, channels: int = 1, bits: int = 16, frames: int = 100) -> bytes:
    width = bits // 8
    pcm = b"\x00" * (frames * channels * width)
    rate = 48000
    align = channels * width
    fmt = struct.pack("<HHIIHH", 1, channels, rate, rate * align, align, bits)
    body = (
        b"WAVEfmt "
        + struct.pack("<I", len(fmt))
        + fmt
        + b"data"
        + struct.pack("<I", len(pcm))
        + pcm
    )
    raw = b"RIFF" + struct.pack("<I", len(body)) + body
    path.write_bytes(raw)
    return raw


def _make_aiff(path: Path) -> Path:
    comm = struct.pack(">HIH", 1, 100, 16) + bytes.fromhex("400ebb80000000000000")
    pcm = b"\x00" * 200
    body = (
        b"AIFFCOMM"
        + struct.pack(">I", len(comm))
        + comm
        + b"SSND"
        + struct.pack(">III", len(pcm) + 8, 0, 0)
        + pcm
    )
    path.write_bytes(b"FORM" + struct.pack(">I", len(body)) + body)
    return path


def _make_clip(clip_id: str, track_id: str, media_id: str | None) -> ClipIR:
    return ClipIR(
        id=clip_id,
        track_id=track_id,
        channel_index=0,
        event_index=0,
        name=f"Clip_{clip_id}",
        media_id=media_id,
        timeline_start_samples=0,
        timeline_end_samples=1000,
    )


def test_iter_riff_chunks_end_menor_que_12(tmp_path):
    """RIFF com end declarado menor que 12 e recusado imediatamente."""
    corrupt = tmp_path / "end_curto.wav"
    corrupt.write_bytes(b"RIFF\x02\x00\x00\x00WAVE")
    with pytest.raises(ValueError, match="smaller than the header"):
        list(_iter_riff_chunks(corrupt))


def test_iter_riff_chunks_cauda_incompleta_dentro_do_end(tmp_path):
    """WAV com fmt/data validos e cauda incompleta dentro do end falha."""
    path = tmp_path / "cauda.wav"
    valid = _make_wav(path, frames=10)
    extra_tail = b"ABCD"
    new_end = len(valid) - 8 + len(extra_tail)
    tampered = bytearray(valid)
    struct.pack_into("<I", tampered, 4, new_end)
    tampered.extend(extra_tail)
    path.write_bytes(bytes(tampered))

    with pytest.raises(ValueError, match="partial tail"):
        list(_iter_riff_chunks(path))


def test_iter_riff_chunks_end_maior_que_arquivo(tmp_path):
    """RIFF que declara end maior que o arquivo fisico e rejeitado como truncado."""
    path = tmp_path / "end_maior.wav"
    valid = _make_wav(path, frames=10)
    path.write_bytes(valid[: len(valid) - 8])
    with pytest.raises(ValueError, match="truncated"):
        list(_iter_riff_chunks(path))


def test_embed_assets_ordem_multiplicidade_e_exclusao_video():
    """Preserva ordem e duplicatas de assets, excluindo os usados por video."""
    a1 = MediaAsset(id="a1", filename="1.wav")
    a2 = MediaAsset(id="a2", filename="2.wav")
    a3 = MediaAsset(id="a3", filename="1.wav")
    a_vid = MediaAsset(id="a_vid", filename="v.wav")
    c1 = _make_clip("c1", "t1", "a1")
    cv = _make_clip("cv", "tv", "a_vid")
    t1 = TrackIR(id="t1", source_index=0, name="A", kind="audio", channel_count=1, clips=[c1])
    tv = TrackIR(
        id="tv",
        source_index=1,
        name="V",
        kind="video",
        channel_count=1,
        is_video=True,
        clips=[cv],
    )
    session = SessionIR(
        name="s",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1, False),
        start_timecode_samples=0,
        tracks=[t1, tv],
        media=[a1, a2, a3, a_vid],
    )
    assert embed_assets(session) == [a1, a2, a3]


def test_check_embed_size_fronteiras_permissivo():
    """Valida fronteiras limite-1, limite e limite+1 no relatorio permissivo."""
    report = Report(strict=False)
    assert check_embed_size(EMBED_LIMIT_BYTES - 1, report, stage="pre")
    assert check_embed_size(EMBED_LIMIT_BYTES, report, stage="exact")
    assert report.count("error") == 0
    assert not check_embed_size(EMBED_LIMIT_BYTES + 1, report, stage="post")
    assert report.count("error") == 1
    assert report.issues[0].code == "EXPORT_EMBED_EXCEEDS_2GB"
    assert report.issues[0].context["stage"] == "post"
    assert report.issues[0].context["size"] == EMBED_LIMIT_BYTES + 1


def test_check_embed_size_fronteiras_estrito():
    """Modo estrito levanta StrictModeError em limite+1."""
    report = Report(strict=True)
    assert check_embed_size(EMBED_LIMIT_BYTES, report)
    with pytest.raises(StrictModeError):
        check_embed_size(EMBED_LIMIT_BYTES + 1, report)


def test_embed_payload_size_com_e_sem_mono_sources(tmp_path):
    """Soma stats com mono_sources ou resolved_path, preservando repeticoes."""
    w1, w2 = tmp_path / "w1.wav", tmp_path / "w2.wav"
    mL, mR = tmp_path / "w2_L.wav", tmp_path / "w2_R.wav"
    _make_wav(w1, frames=100)
    _make_wav(w2, channels=2, frames=100)
    _make_wav(mL, frames=100)
    _make_wav(mR, frames=100)

    a1 = MediaAsset(id="a1", filename=str(w1), resolved_path=str(w1))
    a2 = MediaAsset(id="a2", filename=str(w2), resolved_path=str(w2))
    a3 = MediaAsset(id="a3", filename=str(w1), resolved_path=str(w1))
    session = SessionIR(
        name="s",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1, False),
        start_timecode_samples=0,
        tracks=[],
        media=[a1, a2, a3],
    )

    assert embed_payload_size(session) == w1.stat().st_size * 2 + w2.stat().st_size
    mono_sources = {"a2": (mL, mR)}
    expected = w1.stat().st_size * 2 + mL.stat().st_size + mR.stat().st_size
    assert embed_payload_size(session, mono_sources) == expected


def test_preflight_embed_sucesso_e_nao_muta_ir(tmp_path):
    """Sessao valida passa no preflight sem mutar a estrutura do IR."""
    w1 = tmp_path / "audio.wav"
    _make_wav(w1, frames=50)
    asset = MediaAsset(id="a1", filename=str(w1), resolved_path=str(w1))
    clips = [_make_clip("c1", "t1", "a1"), _make_clip("c2", "t1", "a1")]
    track = TrackIR(
        id="t1",
        source_index=0,
        name="Audio",
        kind="audio",
        channel_count=1,
        clips=clips,
    )
    session = SessionIR(
        name="s",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1, False),
        start_timecode_samples=0,
        tracks=[track],
        media=[asset],
    )
    report = Report(strict=True)
    assert preflight_embed(session, report)
    assert len(session.media) == 1 and session.media[0].id == "a1"


def test_preflight_embed_arquivo_ausente(tmp_path):
    """Arquivo de audio inexistente falha em modo permissivo e estrito."""
    missing = tmp_path / "fantasma.wav"
    asset = MediaAsset(id="a1", filename=str(missing), resolved_path=str(missing))
    track = TrackIR(
        id="t1",
        source_index=0,
        name="A",
        kind="audio",
        channel_count=1,
        clips=[_make_clip("c1", "t1", "a1")],
    )
    session = SessionIR(
        name="s",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1, False),
        start_timecode_samples=0,
        tracks=[track],
        media=[asset],
    )

    permissive = Report(strict=False)
    assert not preflight_embed(session, permissive)
    assert permissive.count("error") >= 1

    strict = Report(strict=True)
    with pytest.raises(StrictModeError):
        preflight_embed(session, strict)


def test_preflight_embed_formato_e_canais_invalidos(tmp_path):
    """Rejeita formato AIFF e arquivos com contagem de canais fora de 1 e 2."""
    aiff_path = tmp_path / "som.aiff"
    _make_aiff(aiff_path)
    a1 = MediaAsset(id="a1", filename=str(aiff_path), resolved_path=str(aiff_path))
    s1 = SessionIR(
        name="s",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1, False),
        start_timecode_samples=0,
        tracks=[],
        media=[a1],
    )
    rep1 = Report(strict=False)
    assert not preflight_embed(s1, rep1)
    assert any(i.code == "EMBED_FORMAT_UNSUPPORTED" for i in rep1.issues)

    tri_path = tmp_path / "tri.wav"
    _make_wav(tri_path, channels=3)
    a2 = MediaAsset(id="a2", filename=str(tri_path), resolved_path=str(tri_path))
    s2 = SessionIR(
        name="s",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1, False),
        start_timecode_samples=0,
        tracks=[],
        media=[a2],
    )
    rep2 = Report(strict=False)
    assert not preflight_embed(s2, rep2)
    assert any(i.code == "EMBED_CHANNELS_UNSUPPORTED" for i in rep2.issues)


def test_preflight_embed_riff_truncado_e_fmt_gigante(tmp_path):
    """Rejeita RIFF incompleto e fmt suspeito antes de qualquer gravacao."""
    trunc_path = tmp_path / "trunc.wav"
    raw = _make_wav(trunc_path, frames=20)
    trunc_path.write_bytes(raw[: len(raw) - 15])
    m1 = MediaAsset(id="m1", filename=str(trunc_path))
    s1 = SessionIR(
        name="s",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1, False),
        start_timecode_samples=0,
        tracks=[],
        media=[m1],
    )
    rep1 = Report(strict=False)
    assert not preflight_embed(s1, rep1)
    assert any(i.code == "EMBED_RIFF_INVALID" for i in rep1.issues)

    giant_path = tmp_path / "giant.wav"
    giant_raw = bytearray(_make_wav(giant_path, frames=20))
    struct.pack_into("<I", giant_raw, 16, 50000)
    struct.pack_into("<I", giant_raw, 4, len(giant_raw) + 50000)
    giant_path.write_bytes(bytes(giant_raw))
    m2 = MediaAsset(id="m2", filename=str(giant_path))
    s2 = SessionIR(
        name="s",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1, False),
        start_timecode_samples=0,
        tracks=[],
        media=[m2],
    )
    rep2 = Report(strict=False)
    assert not preflight_embed(s2, rep2)
    assert any(i.code in ("EMBED_FMT_INVALID", "EMBED_RIFF_INVALID") for i in rep2.issues)


def test_preflight_embed_clip_sem_asset_elegivel(tmp_path):
    """Clip apontando media_id ausente ou de video reprova o preflight."""
    w = tmp_path / "ok.wav"
    _make_wav(w)
    a1 = MediaAsset(id="a1", filename=str(w))
    vid = MediaAsset(id="vid", filename=str(w))
    c1 = _make_clip("c1", "t1", "id_nao_declarado")
    cv = _make_clip("cv", "tv", "vid")
    t1 = TrackIR(id="t1", source_index=0, name="A", kind="audio", channel_count=1, clips=[c1])
    tv = TrackIR(
        id="tv",
        source_index=1,
        name="V",
        kind="video",
        channel_count=1,
        is_video=True,
        clips=[cv],
    )
    s1 = SessionIR(
        name="s",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1, False),
        start_timecode_samples=0,
        tracks=[t1],
        media=[a1],
    )
    rep1 = Report(strict=False)
    assert not preflight_embed(s1, rep1)
    assert any(i.code == "EMBED_CLIP_NO_ELIGIBLE_ASSET" for i in rep1.issues)

    t1.clips = [_make_clip("c2", "t1", "vid")]
    s2 = SessionIR(
        name="s",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1, False),
        start_timecode_samples=0,
        tracks=[t1, tv],
        media=[a1, vid],
    )
    rep2 = Report(strict=False)
    assert not preflight_embed(s2, rep2)


def test_embed_wave_streaming_e_verificacao_sucesso(tmp_path):
    """Copia essencia em blocos de 64 KiB e valida no AAF reaberto."""
    wav_path = tmp_path / "audio.wav"
    raw = _make_wav(wav_path, frames=2000)
    aaf_path = tmp_path / "teste.aaf"

    with aaf2.open(str(aaf_path), "w") as f:
        comp = f.create.CompositionMob("comp")
        f.content.mobs.append(comp)
        meta = {
            "format": {"filename": str(wav_path), "format_name": "wav"},
            "streams": [
                {
                    "codec_type": "audio",
                    "codec_name": "pcm_s16le",
                    "sample_rate": 48000,
                    "duration_ts": 2000,
                    "channels": 1,
                }
            ],
        }
        _master, source, _tape = f.content.link_external_wav(meta)
        written = embed_wave(f, source, wav_path)
        assert written == len(raw)
        source_mob_id = str(source.mob_id)

    report = Report(strict=True)
    assert verify_embedded_aaf(aaf_path, {source_mob_id: len(raw)}, report)


def test_verify_embedded_aaf_detecta_locator_presente(tmp_path):
    """Se o Locator persistir no WAVEDescriptor, a verificacao acusa erro."""
    wav_path = tmp_path / "audio.wav"
    raw = _make_wav(wav_path, frames=100)
    aaf_path = tmp_path / "com_locator.aaf"

    with aaf2.open(str(aaf_path), "w") as f:
        comp = f.create.CompositionMob("comp")
        f.content.mobs.append(comp)
        meta = {
            "format": {"filename": str(wav_path), "format_name": "wav"},
            "streams": [
                {
                    "codec_type": "audio",
                    "codec_name": "pcm_s16le",
                    "sample_rate": 48000,
                    "duration_ts": 100,
                    "channels": 1,
                }
            ],
        }
        _master, source, _tape = f.content.link_external_wav(meta)
        ed = f.create.EssenceData()
        ed.mob = source
        f.content.essencedata.append(ed)
        st = ed.open("w")
        st.write(raw)
        st.close()
        source_mob_id = str(source.mob_id)

    permissive = Report(strict=False)
    assert not verify_embedded_aaf(aaf_path, {source_mob_id: len(raw)}, permissive)
    assert any(i.code == "VERIFY_EMBED_LOCATOR_PRESENT" for i in permissive.issues)

    strict = Report(strict=True)
    with pytest.raises(StrictModeError):
        verify_embedded_aaf(aaf_path, {source_mob_id: len(raw)}, strict)


def test_verify_embedded_aaf_erros_de_validacao(tmp_path):
    """Detecta divergencia de tamanho, mob ausente e arquivo inexistente."""
    wav_path = tmp_path / "audio.wav"
    raw = _make_wav(wav_path, frames=100)
    aaf_path = tmp_path / "teste_err.aaf"

    with aaf2.open(str(aaf_path), "w") as f:
        comp = f.create.CompositionMob("comp")
        f.content.mobs.append(comp)
        meta = {
            "format": {"filename": str(wav_path), "format_name": "wav"},
            "streams": [
                {
                    "codec_type": "audio",
                    "codec_name": "pcm_s16le",
                    "sample_rate": 48000,
                    "duration_ts": 100,
                    "channels": 1,
                }
            ],
        }
        _master, source, _tape = f.content.link_external_wav(meta)
        embed_wave(f, source, wav_path)
        source_mob_id = str(source.mob_id)

    rep1 = Report(strict=False)
    assert not verify_embedded_aaf(aaf_path, {source_mob_id: len(raw) + 10}, rep1)
    assert any(i.code == "VERIFY_EMBED_SIZE_MISMATCH" for i in rep1.issues)

    rep2 = Report(strict=False)
    assert not verify_embedded_aaf(aaf_path, {"outro_id": len(raw)}, rep2)
    assert any(i.code == "VERIFY_EMBED_MOBS_MISMATCH" for i in rep2.issues)

    rep3 = Report(strict=False)
    assert not verify_embedded_aaf(tmp_path / "nao_existe.aaf", {}, rep3)
    assert any(i.code == "VERIFY_EMBED_FILE_MISSING" for i in rep3.issues)


def test_embed_wave_detecta_truncamento_durante_copia(tmp_path):
    """Detecta inconsistencia caso o arquivo mude de tamanho durante o stream."""
    wav_path = tmp_path / "audio.wav"
    _make_wav(wav_path, frames=100)
    aaf_path = tmp_path / "trunc.aaf"

    with aaf2.open(str(aaf_path), "w") as f:
        meta = {
            "format": {"filename": str(wav_path), "format_name": "wav"},
            "streams": [
                {
                    "codec_type": "audio",
                    "codec_name": "pcm_s16le",
                    "sample_rate": 48000,
                    "duration_ts": 100,
                    "channels": 1,
                }
            ],
        }
        _master, source, _tape = f.content.link_external_wav(meta)
        with patch.object(Path, "stat") as mock_stat:
            mock_stat.return_value.st_size = 999999
            with pytest.raises(ValueError, match="incomplete write"):
                embed_wave(f, source, wav_path)
