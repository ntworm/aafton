# tests/unit/test_verify_integrity.py
from __future__ import annotations

import gzip
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from aafton.aaf_write import write_aaf
from aafton.als_write import WriteOptions, write_session
from aafton.ir import ClipIR, MediaAsset, SessionIR, TrackIR
from aafton.report import Report
from aafton.timecode import TimecodeRate
from aafton.verify import VerifyResult, VerifyRow, verify_conversion
from tests.aaf_factory import wav_bytes

TEMPLATE = Path("templates/live12.als")

pytestmark = pytest.mark.skipif(
    not TEMPLATE.exists(), reason="Portao B: templates/live12.als ainda nao foi autorado"
)

UMA_HORA = 172800000


@pytest.fixture
def wav(tmp_path):
    caminho = tmp_path / "som.wav"
    caminho.write_bytes(wav_bytes(48000 * 10))
    return caminho


def _sessao_minima(wav, num_clips=1):
    asset = MediaAsset(
        id="m1",
        filename=str(wav),
        channels=1,
        sample_rate=48000,
        duration_samples=48000 * 10,
        status="resolved",
        resolved_path=str(wav),
        raw={},
    )
    track = TrackIR(
        id="t0",
        source_index=0,
        name="AUDIO",
        kind="audio",
        channel_count=1,
        clips=[],
        plugins=[],
    )
    for idx in range(num_clips):
        start = UMA_HORA + idx * 48000
        track.clips.append(
            ClipIR(
                id=f"c{idx}",
                track_id="t0",
                channel_index=0,
                event_index=idx,
                name="som.wav",
                media_id="m1",
                timeline_start_samples=start,
                timeline_end_samples=start + 24000,
                source_offset_samples=0,
                raw={},
            )
        )
    return SessionIR(
        name="minima",
        sample_rate=48000,
        timecode_rate=TimecodeRate(24, 1, False),
        start_timecode_samples=UMA_HORA,
        tracks=[track],
        media=[asset],
        markers=[],
        source_path=None,
        source_application=None,
    )


def _par_minimo(tmp_path, wav, num_clips=1):
    aaf = tmp_path / "test.aaf"
    als = tmp_path / "test.als"
    session = _sessao_minima(wav, num_clips=num_clips)
    write_aaf(session, aaf, Report(strict=False))
    write_session(
        session,
        TEMPLATE,
        als,
        WriteOptions(sample_rate=48000, bpm=60, live_origin_samples=UMA_HORA),
        Report(strict=False),
    )
    return aaf, als


def test_empty_is_not_verified():
    """Sessao vazia nao pode ser considerada verificada."""
    assert VerifyResult().ok is False


def test_negative_tolerance_raises_value_error(tmp_path, wav):
    """Tolerancia negativa deve ser rejeitada com ValueError."""
    aaf, als = _par_minimo(tmp_path, wav, 1)
    with pytest.raises(ValueError, match="[Nn]egativ"):
        verify_conversion(aaf, als, override_tolerance_samples=-1)


def test_delta_end_detected_and_fails(tmp_path, wav):
    """Fim alterado em 1 sample deve reprovar com tolerancia 0 e registrar delta_end."""
    aaf, als = _par_minimo(tmp_path, wav, 1)

    # Modificar CurrentEnd no XML do ALS em 1 sample (1/48000 beats @ 60 BPM)
    with gzip.open(als, "rb") as gz_in:
        tree = ET.parse(gz_in)
    root = tree.getroot()
    current_end_node = root.find(".//AudioClip/CurrentEnd")
    assert current_end_node is not None
    current_val = float(current_end_node.attrib["Value"])
    # 1 sample a 48kHz e 60bpm = 1 / 48000 beats
    novo_fim = current_val + (1.0 / 48000.0)
    current_end_node.attrib["Value"] = f"{novo_fim:.8f}"
    with gzip.open(als, "wb") as gz_out:
        tree.write(gz_out)

    res0 = verify_conversion(aaf, als, override_tolerance_samples=0)
    assert res0.ok is False
    assert any(r.delta_end_samples != 0 for r in res0.rows if r.is_audio)
    assert "failed" in res0.to_markdown()

    # Com tolerancia 1 sample, o gate temporal deve aceitar
    res1 = verify_conversion(aaf, als, override_tolerance_samples=1)
    assert res1.ok is True


def test_destination_extra_clip_fails(tmp_path, wav):
    """Clip extra no ALS deve reprovar, gerando audio_extra=1 e linha extra no ALS."""
    aaf = tmp_path / "extra_test.aaf"
    als = tmp_path / "extra_test.als"

    session_origem = _sessao_minima(wav, num_clips=1)
    write_aaf(session_origem, aaf, Report(strict=False))

    session_destino = _sessao_minima(wav, num_clips=2)
    write_session(
        session_destino,
        TEMPLATE,
        als,
        WriteOptions(sample_rate=48000, bpm=60, live_origin_samples=UMA_HORA),
        Report(strict=False),
    )

    res = verify_conversion(aaf, als)
    assert res.ok is False
    assert res.audio_expected == 1
    assert res.audio_compared == 1
    assert res.audio_extra == 1
    assert any(r.status == "extra in the ALS" for r in res.rows)


def test_destination_missing_clip_fails(tmp_path, wav):
    """Clip ausente no ALS deve reprovar, gerando audio_missing=1 e linha faltando no ALS."""
    aaf = tmp_path / "missing_test.aaf"
    als = tmp_path / "missing_test.als"

    session_origem = _sessao_minima(wav, num_clips=2)
    write_aaf(session_origem, aaf, Report(strict=False))

    session_destino = _sessao_minima(wav, num_clips=1)
    write_session(
        session_destino,
        TEMPLATE,
        als,
        WriteOptions(sample_rate=48000, bpm=60, live_origin_samples=UMA_HORA),
        Report(strict=False),
    )

    res = verify_conversion(aaf, als)
    assert res.ok is False
    assert res.audio_expected == 2
    assert res.audio_compared == 1
    assert res.audio_missing == 1
    assert any(r.status == "missing in the ALS" for r in res.rows)


def test_only_video_presumida_fails():
    """Apenas linhas presumidas/video sem audio comprovado nao satisfaz o gate."""
    res = VerifyResult(
        rows=[
            VerifyRow(
                track="VIDEO",
                clip="video.mov",
                aaf_in="01:00:00:00",
                aaf_out="01:00:10:00",
                als_in="01:00:00:00",
                als_out="01:00:10:00",
                delta_samples=0,
                delta_frames=0.0,
                status="presumed",
                anchor_source="VIDEO_ANCHOR_EXPLICIT",
                is_audio=False,
            )
        ],
        audio_expected=0,
        audio_compared=0,
    )
    assert res.ok is False


def test_identical_audio_pair_succeeds(tmp_path, wav):
    """Par identico de 1 clipe resulta em expected=compared=1, missing=extra=0, ok."""
    aaf, als = _par_minimo(tmp_path, wav, 1)
    res = verify_conversion(aaf, als, override_tolerance_samples=0)
    assert res.ok is True
    assert res.audio_expected == 1
    assert res.audio_compared == 1
    assert res.audio_missing == 0
    assert res.audio_extra == 0
    assert len(res.errors) == 0
    assert all(r.delta_end_samples == 0 for r in res.rows if r.is_audio)


def test_stereo_pair_valid_covers_both_slots(tmp_path):
    """Stereo pair valido cobrindo dois slots conta ambos os eventos sem falso extra."""
    import wave

    wav_path = tmp_path / "stereo.wav"
    with wave.open(str(wav_path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(48000)
        handle.writeframes(b"\0" * 192000)

    media = MediaAsset(
        id="stereo",
        filename=wav_path.name,
        channels=2,
        sample_rate=48000,
        duration_samples=48000,
        status="resolved",
        resolved_path=str(wav_path),
    )
    clip = ClipIR(
        id="c1",
        track_id="t1",
        channel_index=0,
        event_index=0,
        name="stereo",
        media_id=media.id,
        timeline_start_samples=UMA_HORA + 48000,
        timeline_end_samples=UMA_HORA + 96000,
        source_offset_samples=0,
    )
    session = SessionIR(
        name="stereo",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=UMA_HORA,
        tracks=[
            TrackIR(
                id="t1",
                source_index=0,
                original_source_indices=[0, 1],
                name="STEREO",
                kind="audio",
                channel_count=2,
                clips=[clip],
            )
        ],
        media=[media],
    )
    aaf_path = tmp_path / "stereo.aaf"
    als_path = tmp_path / "stereo.als"
    write_aaf(session, aaf_path, Report(strict=False))
    write_session(
        session,
        TEMPLATE,
        als_path,
        WriteOptions(
            sample_rate=48000,
            bpm=60,
            live_origin_samples=UMA_HORA,
            stereo="pair",
        ),
        Report(strict=False),
    )

    result = verify_conversion(aaf_path, als_path, override_tolerance_samples=0)
    assert result.ok is True
    assert result.audio_expected == 2
    assert result.audio_compared == 2
    assert result.audio_missing == 0
    assert result.audio_extra == 0
    assert len(result.rows) == 2


def test_markdown_and_table_contents(tmp_path, wav):
    """Markdown e tabela incluem contagens de audio, deltas e campos fora de escopo."""
    aaf, als = _par_minimo(tmp_path, wav, 1)
    res = verify_conversion(aaf, als, override_tolerance_samples=0)
    md = res.to_markdown()
    table = res.to_table()

    assert "- **Audio events**: 1 compared of 1 expected" in md
    assert "- **Out of scope**:" in md
    assert "End deviation" in md or "D END" in table
    assert "Fields out of scope:" in table


def test_destination_unmapped_track_reproves_verification(tmp_path, wav):
    """Faixa no ALS sem mapeamento de origem (ou vazia no mapa) deve reprovar a verificacao."""
    import json
    from aafton.sidecar import sidecar_path_for

    aaf = tmp_path / "unmapped_orig.aaf"
    als = tmp_path / "unmapped_dest.als"

    session_origem = _sessao_minima(wav, num_clips=1)
    write_aaf(session_origem, aaf, Report(strict=False))

    c_extra = ClipIR(
        id="c_extra",
        track_id="t1",
        channel_index=0,
        event_index=0,
        name="clip_extra",
        media_id="m1",
        source_offset_samples=0,
        timeline_start_samples=UMA_HORA + 48000,
        timeline_end_samples=UMA_HORA + 96000,
    )
    t1 = TrackIR(
        id="t1",
        source_index=1,
        name="EXTRA_TRACK",
        kind="audio",
        channel_count=1,
        clips=[c_extra],
    )
    asset = session_origem.media[0]
    session_destino = SessionIR(
        name="s_dest",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1, False),
        start_timecode_samples=UMA_HORA,
        tracks=[session_origem.tracks[0], t1],
        media=[asset],
    )
    write_session(
        session_destino,
        TEMPLATE,
        als,
        WriteOptions(sample_rate=48000, bpm=60, live_origin_samples=UMA_HORA),
        Report(strict=False),
    )

    sc = sidecar_path_for(als)
    data = json.loads(sc.read_text(encoding="utf-8"))
    data["audio_source_indices"] = [[0], []]
    sc.write_text(json.dumps(data), encoding="utf-8")

    res = verify_conversion(aaf, als, override_tolerance_samples=0)
    assert res.ok is False
    assert res.audio_expected == 1
    assert res.audio_compared == 1
    assert res.audio_extra == 1
    assert res.audio_missing == 0
    assert any(r.track == "EXTRA_TRACK" and r.status == "extra in the ALS" for r in res.rows)


def test_destination_unmapped_empty_track_reproves_verification(tmp_path, wav):
    """Faixa no ALS sem mapeamento de origem mesmo sem clips deve reprovar a verificacao."""
    import json
    from aafton.sidecar import sidecar_path_for

    aaf = tmp_path / "unmapped_empty_orig.aaf"
    als = tmp_path / "unmapped_empty_dest.als"

    session_origem = _sessao_minima(wav, num_clips=1)
    write_aaf(session_origem, aaf, Report(strict=False))

    t1 = TrackIR(
        id="t1",
        source_index=1,
        name="EMPTY_EXTRA_TRACK",
        kind="audio",
        channel_count=1,
        clips=[],
    )
    asset = session_origem.media[0]
    session_destino = SessionIR(
        name="s_dest",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1, False),
        start_timecode_samples=UMA_HORA,
        tracks=[session_origem.tracks[0], t1],
        media=[asset],
    )
    write_session(
        session_destino,
        TEMPLATE,
        als,
        WriteOptions(sample_rate=48000, bpm=60, live_origin_samples=UMA_HORA),
        Report(strict=False),
    )

    sc = sidecar_path_for(als)
    data = json.loads(sc.read_text(encoding="utf-8"))
    data["audio_source_indices"] = [[0], []]
    sc.write_text(json.dumps(data), encoding="utf-8")

    res = verify_conversion(aaf, als, override_tolerance_samples=0)
    assert res.ok is False
    assert any("EMPTY_EXTRA_TRACK" in err for err in res.errors)
