"""Comprehensive Dialect Matrix Test Suite.

Valida a matriz completa de dialetos AAF e capacidades de exportacao para o Ableton Live:
1. Pro Tools: Combiner stereo multi-canal e rendered fades (absorb e keep).
2. DaVinci Resolve: Slots com frame edit rates (24, 25 fps), descarte de video e transitions.
3. Adobe Premiere: Multiplas CompositionMobs com sequencias auxiliares precedendo a principal,
   desempate deterministico por ranking, NetworkLocators e unwrap de Mono Audio Pan.
4. Audio Embutido (Embedded Essence): RIFF WAV e PCM puro em 44.1 kHz, 48 kHz e 96 kHz.
5. Taxas Fracionarias (Fractional Frame Rates): 23.976, 29.97 NDF/DF e 59.94.
6. Sessao Longa (2h+ / 7200s+) e Timecode Negativo / Pre-roll com ancoragem automatica em Live 12.
"""

from __future__ import annotations

import gzip
from fractions import Fraction
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from aafton.aaf_read import read_aaf, _rank_composition_mob
from aafton.als_write import WriteOptions, write_session, timecode_rate_to_smpte_format
from aafton.media_extract import resolve_asset, read_audio_metadata
from aafton.report import Report
from aafton.timecode import TimecodeRate

from tests.aaf_factory import (
    SAMPLE_RATE,
    wav_bytes,
    build_protools_synthetic_aaf,
    build_resolve_synthetic_aaf,
    build_premiere_synthetic_aaf,
    build_embedded_dialect_aaf,
    build_fractional_rate_synthetic_aaf,
    build_long_session_synthetic_aaf,
)

TEMPLATE = Path("templates/live12.als")


@pytest.fixture
def test_wav(tmp_path: Path) -> Path:
    wav = tmp_path / "test_carrier.wav"
    wav.write_bytes(wav_bytes(SAMPLE_RATE * 10, channels=1, sample_rate=SAMPLE_RATE))
    return wav


# ==============================================================================
# 1. Pro Tools Dialect Tests
# ==============================================================================


@pytest.mark.parametrize("fades_mode", ["absorb", "keep"])
def test_protools_synthetic_combiner_and_rendered_fades(
    tmp_path: Path, test_wav: Path, fades_mode: str
):
    """Testa o dialeto Pro Tools com combiner stereo e rendered fades."""
    aaf_path = tmp_path / f"protools_{fades_mode}.aaf"
    build_protools_synthetic_aaf(
        aaf_path,
        test_wav,
        track_name="PT_STEREO",
        start_timecode_frames=90000,
        fps=25,
        use_combiner=True,
        rendered_fades=True,
    )

    report = Report(strict=False)
    session = read_aaf(aaf_path, report, fades=fades_mode)

    assert len(session.tracks) == 1
    track = session.tracks[0]
    assert track.name == "PT_STEREO"
    assert track.channel_count == 2, "Audio Channel Combiner deve ser detectado com 2 canais"

    if fades_mode == "absorb":
        # No modo absorb, o fade renderizado e absorvido como crossfade nativo
        # 2 eventos de timeline * 2 canais do combiner = 4 clips
        assert len(track.clips) == 4
        assert len(set(c.event_index for c in track.clips)) == 2
        clip_a = [c for c in track.clips if c.channel_index == 0][0]
        assert clip_a.fade_out is not None
        assert clip_a.fade_out.duration_samples == SAMPLE_RATE // 20
    else:
        # No modo keep, o fade e mantido como clip proprio (2 clips combiner + 1 fade mono + 2 clips combiner = 5 clips)
        assert len(track.clips) == 5
        assert len(set(c.event_index for c in track.clips)) == 3

    # Verifica escrita em ALS
    for asset in session.media:
        resolve_asset(asset, [tmp_path], report)

    als_path = tmp_path / f"protools_{fades_mode}.als"
    write_session(
        session, TEMPLATE, als_path, WriteOptions(sample_rate=SAMPLE_RATE, fades=fades_mode), report
    )
    assert als_path.exists()

    with gzip.open(als_path, "rt", encoding="utf-8") as f:
        root = ET.fromstring(f.read())
    tracks = root.findall(".//AudioTrack")
    assert len(tracks) == 1
    assert tracks[0].find("Name/UserName").get("Value") == "PT_STEREO"


# ==============================================================================
# 2. DaVinci Resolve Dialect Tests
# ==============================================================================


@pytest.mark.parametrize("edit_rate", [Fraction(24, 1), Fraction(25, 1)])
def test_resolve_synthetic_frame_edit_rates_and_video_skip(
    tmp_path: Path, test_wav: Path, edit_rate: Fraction
):
    """Testa DaVinci Resolve com slots em frame edit rates e descarte de video."""
    aaf_path = tmp_path / f"resolve_{edit_rate.numerator}.aaf"
    build_resolve_synthetic_aaf(
        aaf_path,
        test_wav,
        track_name="RESOLVE_TRACK",
        edit_rate=edit_rate,
        start_timecode_frames=round(edit_rate) * 3600,
        include_video_slot=True,
        include_transition=True,
    )

    report = Report(strict=False)
    session = read_aaf(aaf_path, report)

    # Verifica que o slot de video foi ignorado
    assert any(i.code == "VIDEO_TRACK_SKIPPED_ON_IMPORT" for i in report.issues)
    assert len(session.tracks) == 1
    track = session.tracks[0]
    assert track.name == "RESOLVE_TRACK"

    # Exercita _edit_units_to_samples(): clip de 2s = 2 * edit_rate frames -> exatos 96000 samples @ 48kHz
    clips = track.clips
    assert len(clips) >= 2
    for c in clips:
        # A duracao deve estar em samples exatos
        assert c.duration_samples == SAMPLE_RATE * 2, (
            f"Esperado {SAMPLE_RATE * 2} samples para 2s na taxa {edit_rate}, obtido {c.duration_samples}"
        )

    # Escreve para ALS
    for asset in session.media:
        resolve_asset(asset, [tmp_path], report)

    als_path = tmp_path / f"resolve_{edit_rate.numerator}.als"
    write_session(session, TEMPLATE, als_path, WriteOptions(sample_rate=SAMPLE_RATE), report)
    assert als_path.exists()


# ==============================================================================
# 3. Adobe Premiere Dialect Tests (Multi-Comp Ranking & NetworkLocators)
# ==============================================================================


def test_premiere_synthetic_multiple_comps_deterministic_ranking(tmp_path: Path, test_wav: Path):
    """Testa que o ranking deterministico escolhe a Main Sequence sobre sequencias auxiliares."""
    aaf_path = tmp_path / "premiere_multicomp.aaf"
    build_premiere_synthetic_aaf(
        aaf_path,
        test_wav,
        track_name="PR_DIALOG",
        start_timecode_frames=90000,
        fps=25,
        auxiliary_sequences_count=3,
    )

    report = Report(strict=False)
    session = read_aaf(aaf_path, report)

    # Verifica que a sequencia principal foi selecionada
    assert session.name == "Main_Sequence", (
        f"Deveria ter selecionado Main_Sequence, mas selecionou {session.name}"
    )
    assert len(session.tracks) == 2, f"Main_Sequence contem 2 tracks, mas leu {len(session.tracks)}"
    assert session.tracks[0].name == "PR_DIALOG"
    assert session.tracks[1].name == "Audio 2"

    # Verifica que NetworkLocator foi processado e resolveu declared_location
    assert len(session.media) == 1
    media = session.media[0]
    assert media.declared_location is not None
    assert Path(media.declared_location).name == test_wav.name

    # Escreve em ALS
    for asset in session.media:
        resolve_asset(asset, [tmp_path], report)

    als_path = tmp_path / "premiere_multicomp.als"
    write_session(session, TEMPLATE, als_path, WriteOptions(sample_rate=SAMPLE_RATE), report)
    assert als_path.exists()


def test_rank_composition_mob_hierarchy():
    """Valida formalmente a hierarquia de 3 ranks para CompositionMob."""
    from types import SimpleNamespace

    # Mock de CompositionMob
    def make_mock_mob(usage=None, slots=None):
        return SimpleNamespace(usage=usage, slots=slots or [])

    class TimelineMobSlot:
        def __init__(self, length: int, edit_rate: int = 48000):
            self.segment = SimpleNamespace(
                media_kind="Sound", length=length, datadef=SimpleNamespace(name="Sound")
            )
            self.media_kind = "Sound"
            self.datadef = SimpleNamespace(name="Sound")
            self.length = length
            self.edit_rate = edit_rate

    def make_sound_slot(length: int, edit_rate: int = 48000):
        return TimelineMobSlot(length, edit_rate)

    # Caso 1: Rank 1 - TopLevel vence mesmo com menos slots
    mob_top = make_mock_mob(usage="Usage_TopLevel", slots=[make_sound_slot(1000)])
    mob_sub = make_mock_mob(
        usage="Usage_SubClip", slots=[make_sound_slot(10000), make_sound_slot(10000)]
    )
    assert _rank_composition_mob(mob_top, 48000) > _rank_composition_mob(mob_sub, 48000)

    # Caso 2: Rank 2 - Sem TopLevel, mais slots de audio vence
    mob_2slots = make_mock_mob(usage=None, slots=[make_sound_slot(1000), make_sound_slot(1000)])
    mob_1slot = make_mock_mob(usage=None, slots=[make_sound_slot(50000)])
    assert _rank_composition_mob(mob_2slots, 48000) > _rank_composition_mob(mob_1slot, 48000)

    # Caso 3: Rank 3 - Mesmo numero de slots, maior duracao total vence
    mob_long = make_mock_mob(usage=None, slots=[make_sound_slot(96000)])
    mob_short = make_mock_mob(usage=None, slots=[make_sound_slot(48000)])
    assert _rank_composition_mob(mob_long, 48000) > _rank_composition_mob(mob_short, 48000)


# ==============================================================================
# 4. Embedded Audio Dialect Tests (RIFF and PCM at 44.1, 48, 96 kHz)
# ==============================================================================


@pytest.mark.parametrize("sample_rate", [44100, 48000, 96000])
@pytest.mark.parametrize("embed_as_riff", [True, False])
def test_embedded_audio_matrix(tmp_path: Path, sample_rate: int, embed_as_riff: bool):
    """Testa extracao e processamento de audio embutido em diferentes taxas e formatos."""
    format_tag = "riff" if embed_as_riff else "pcm"
    aaf_path = tmp_path / f"embedded_{sample_rate}_{format_tag}.aaf"
    extract_dir = tmp_path / f"extract_{sample_rate}_{format_tag}"

    build_embedded_dialect_aaf(
        aaf_path,
        sample_rate=sample_rate,
        embed_as_riff=embed_as_riff,
        duration_frames=sample_rate * 2,
        track_name="EMBED_TRK",
    )

    report = Report(strict=False)
    session = read_aaf(aaf_path, report, extract_dir=extract_dir)

    assert session.sample_rate == sample_rate
    assert len(session.media) == 1
    asset = session.media[0]
    assert asset.status == "resolved"
    assert asset.resolved_path is not None

    extracted_file = Path(asset.resolved_path)
    assert extracted_file.exists()
    assert extracted_file.stat().st_size > 0

    # Valida metadados do WAV extraido
    meta = read_audio_metadata(extracted_file)
    assert meta["sample_rate"] == sample_rate
    assert meta["channels"] == 1
    assert meta["duration_samples"] == sample_rate * 2

    # Escreve em ALS com WriteOptions
    als_path = tmp_path / f"embedded_{sample_rate}_{format_tag}.als"
    write_session(session, TEMPLATE, als_path, WriteOptions(sample_rate=sample_rate), report)
    assert als_path.exists()


# ==============================================================================
# 5. Fractional Frame Rates Tests
# ==============================================================================


@pytest.mark.parametrize(
    "fps_num,fps_den,drop_frame,expected_smpte",
    [
        (24000, 1001, False, "1"),  # 23.976 fps -> SmpteFormat 1 (24 fps)
        (30000, 1001, False, "5"),  # 29.97 NDF -> SmpteFormat 5 (29.97 NDF)
        (30000, 1001, True, "4"),  # 29.97 DF -> SmpteFormat 4 (29.97 DF)
        (60000, 1001, False, "0"),  # 59.94 NDF -> Fallback 0
        (60000, 1001, True, "0"),  # 59.94 DF -> Fallback 0
    ],
)
def test_fractional_frame_rates_matrix(
    tmp_path: Path,
    test_wav: Path,
    fps_num: int,
    fps_den: int,
    drop_frame: bool,
    expected_smpte: str,
):
    """Testa taxas fracionarias SMPTE, preservacao de TimecodeRate e mapeamento SmpteFormat."""
    aaf_path = tmp_path / f"frac_{fps_num}_{fps_den}_{drop_frame}.aaf"
    build_fractional_rate_synthetic_aaf(
        aaf_path,
        test_wav,
        fps_num=fps_num,
        fps_den=fps_den,
        drop_frame=drop_frame,
        start_timecode_frames=86400,
    )

    report = Report(strict=False)
    session = read_aaf(aaf_path, report)

    assert session.timecode_rate == TimecodeRate(fps_num, fps_den, drop_frame)
    assert timecode_rate_to_smpte_format(session.timecode_rate) == expected_smpte

    # Escreve em ALS e confere SmpteFormat gravado
    for asset in session.media:
        resolve_asset(asset, [tmp_path], report)

    als_path = tmp_path / f"frac_{fps_num}_{fps_den}_{drop_frame}.als"
    write_session(session, TEMPLATE, als_path, WriteOptions(sample_rate=SAMPLE_RATE), report)
    assert als_path.exists()

    with gzip.open(als_path, "rt", encoding="utf-8") as f:
        root = ET.fromstring(f.read())
    smpte_node = root.find(".//LiveSet/SmpteFormat")
    assert smpte_node is not None
    assert smpte_node.get("Value") == expected_smpte


# ==============================================================================
# 6. Long Session (2h+) & Pre-roll / Negative Timecode Tests
# ==============================================================================


def test_long_session_2h_plus(tmp_path: Path, test_wav: Path):
    """Testa sessao de longa duracao (2h+ / 7200s+) sem transbordo de amostras."""
    aaf_path = tmp_path / "long_session.aaf"
    build_long_session_synthetic_aaf(
        aaf_path,
        test_wav,
        duration_seconds=7300,  # 2 horas, 1 minuto e 40 segundos
        start_timecode_frames=90000,
        fps=25,
    )

    report = Report(strict=False)
    session = read_aaf(aaf_path, report)

    assert len(session.tracks) == 1
    clips = session.tracks[0].clips
    assert len(clips) == 2
    # Clip 2 esta em 7300 segundos (350.400.000 samples @ 48k)
    expected_offset = session.start_timecode_samples + 7300 * SAMPLE_RATE
    assert clips[1].timeline_start_samples == expected_offset

    for asset in session.media:
        resolve_asset(asset, [tmp_path], report)

    als_path = tmp_path / "long_session.als"
    write_session(session, TEMPLATE, als_path, WriteOptions(sample_rate=SAMPLE_RATE), report)
    assert als_path.exists()

    with gzip.open(als_path, "rt", encoding="utf-8") as f:
        root = ET.fromstring(f.read())
    clips_als = root.findall(".//AudioClip")
    assert len(clips_als) == 2


def test_negative_timecode_and_automated_origin_alignment(tmp_path: Path, test_wav: Path):
    """Testa timecode negativo e ancoragem automatica (live_origin_samples=None) garantindo live_samples >= 0."""
    aaf_path = tmp_path / "negative_tc.aaf"
    # Timecode negativo: -250 frames (-10s em 25fps = -480000 samples)
    build_long_session_synthetic_aaf(
        aaf_path,
        test_wav,
        duration_seconds=100,
        start_timecode_frames=-250,
        fps=25,
    )

    report = Report(strict=False)
    session = read_aaf(aaf_path, report)

    assert session.start_timecode_samples == -480000
    assert any(c.timeline_start_samples == -480000 for c in session.all_clips())

    for asset in session.media:
        resolve_asset(asset, [tmp_path], report)

    # Com live_origin_samples=None, a origem deve ser resolvida para garantir live_samples >= 0
    als_path = tmp_path / "negative_tc.als"
    write_session(
        session,
        TEMPLATE,
        als_path,
        WriteOptions(sample_rate=SAMPLE_RATE, live_origin_samples=None),
        report,
    )
    assert als_path.exists()

    with gzip.open(als_path, "rt", encoding="utf-8") as f:
        root = ET.fromstring(f.read())
    clips_als = root.findall(".//AudioClip")
    for clip in clips_als:
        time_val = float(clip.get("Time"))
        start_val = float(clip.find("CurrentStart").get("Value"))
        assert time_val >= 0.0, f"Tempo no Live nao pode ser negativo: {time_val}"
        assert start_val >= 0.0, f"CurrentStart no Live nao pode ser negativo: {start_val}"


def test_preroll_timecode_clips_before_nominal_start(tmp_path: Path, test_wav: Path):
    """Testa pre-roll onde clips comecam antes do inicio nominal da sessao."""
    from aafton.ir import ClipIR, TrackIR, SessionIR, MediaAsset

    # Sessao com timecode em 01:00:00:00 (172.800.000 samples)
    # Mas um clip de pre-roll em 00:59:50:00 (172.320.000 samples)
    session_start = 172800000
    preroll_start = 172320000

    asset = MediaAsset(
        id="m1",
        filename=test_wav.name,
        resolved_path=str(test_wav),
        status="resolved",
        sample_rate=SAMPLE_RATE,
    )
    track = TrackIR(id="t1", name="PREROLL_TRK", kind="audio", channel_count=1, source_index=0)
    # Clip de pre-roll
    track.clips.append(
        ClipIR(
            id="c1",
            track_id="t1",
            channel_index=0,
            event_index=0,
            name="preroll_clip",
            media_id="m1",
            timeline_start_samples=preroll_start,
            timeline_end_samples=preroll_start + 48000,
            source_offset_samples=0,
        )
    )
    # Clip nominal
    track.clips.append(
        ClipIR(
            id="c2",
            track_id="t1",
            channel_index=0,
            event_index=1,
            name="nominal_clip",
            media_id="m1",
            timeline_start_samples=session_start,
            timeline_end_samples=session_start + 48000,
            source_offset_samples=0,
        )
    )

    session = SessionIR(
        name="Preroll_Session",
        sample_rate=SAMPLE_RATE,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=session_start,
        tracks=[track],
        media=[asset],
    )

    als_path = tmp_path / "preroll.als"
    report = Report(strict=False)
    # Com live_origin_samples=None, a ancoragem deve ajustar origin para cobrir o pre-roll
    write_session(
        session,
        TEMPLATE,
        als_path,
        WriteOptions(sample_rate=SAMPLE_RATE, live_origin_samples=None),
        report,
    )
    assert als_path.exists()

    with gzip.open(als_path, "rt", encoding="utf-8") as f:
        root = ET.fromstring(f.read())
    clips_als = root.findall(".//AudioClip")
    assert len(clips_als) == 2
    for clip in clips_als:
        time_val = float(clip.get("Time"))
        assert time_val >= 0.0, f"Clip de pre-roll nao pode ter tempo negativo no Live: {time_val}"
