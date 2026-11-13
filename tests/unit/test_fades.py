# tests/unit/test_fades.py
"""Testes unitarios para a classificacao e absorcao de fades renderizados do Pro Tools (R3)."""

import pytest

from aafton.aaf_read import read_aaf
from aafton.report import Report
from tests.aaf_factory import build_aaf_with_rendered_fades, wav_bytes
from tests.support.paths import fixture_aaf

PT_AAF = "TESTE_AFF_PROTOOLS.aaf"


@pytest.fixture
def wav(tmp_path):
    caminho = tmp_path / "som.wav"
    caminho.write_bytes(wav_bytes(48000 * 120))
    return caminho


def test_synthetic_fade_in_absorbs_into_following_clip(tmp_path, wav):
    """Filler -> Fade(24000) -> Clip(96000, offset=48000) -> Filler.

    Fade-in de 24000 samples deve recuar o inicio do clip em 24000, aumentar a duracao
    em 24000, recuar o offset na fonte em 24000 e configurar fade_in = 24000.
    """
    aaf = build_aaf_with_rendered_fades(
        tmp_path / "fade_in.aaf",
        wav,
        [
            ("filler", 48000),
            ("fade", 24000, "Fade 1"),
            ("clip", 96000, 48000),
            ("filler", 48000),
        ],
        start_timecode_frames=0,
    )
    report = Report(strict=False)
    session = read_aaf(aaf, report, fades="absorb")
    assert len(session.tracks[0].clips) == 1
    clip = session.tracks[0].clips[0]

    assert clip.timeline_start_samples == 48000  # 48000 filler + 0
    assert clip.timeline_end_samples == 48000 + 24000 + 96000  # 168000
    assert clip.duration_samples == 120000
    assert clip.source_offset_samples == 24000  # 48000 - 24000
    assert clip.fade_in is not None
    assert clip.fade_in.duration_samples == 24000
    assert clip.fade_out is None


def test_synthetic_fade_out_absorbs_into_preceding_clip(tmp_path, wav):
    """Filler -> Clip(96000, offset=0) -> Fade(24000) -> Filler.

    Fade-out de 24000 samples deve aumentar a duracao em 24000, manter o offset
    e configurar fade_out = 24000.
    """
    aaf = build_aaf_with_rendered_fades(
        tmp_path / "fade_out.aaf",
        wav,
        [
            ("filler", 48000),
            ("clip", 96000, 0),
            ("fade", 24000, "Fade 2"),
            ("filler", 48000),
        ],
        start_timecode_frames=0,
    )
    report = Report(strict=False)
    session = read_aaf(aaf, report, fades="absorb")
    assert len(session.tracks[0].clips) == 1
    clip = session.tracks[0].clips[0]

    assert clip.timeline_start_samples == 48000
    assert clip.timeline_end_samples == 48000 + 96000 + 24000  # 168000
    assert clip.duration_samples == 120000
    assert clip.source_offset_samples == 0
    assert clip.fade_out is not None
    assert clip.fade_out.duration_samples == 24000
    assert clip.fade_in is None


def test_synthetic_crossfade(tmp_path, wav):
    """ClipA(96000, offset=0) -> Fade(24000) -> ClipB(96000, offset=48000).

    O clip A termina em T1, recebendo fade_out = L.
    O clip B nao se move. fade_in = 0 e CrossfadeInState = 1.
    """
    aaf = build_aaf_with_rendered_fades(
        tmp_path / "crossfade.aaf",
        wav,
        [
            ("clip", 96000, 0),
            ("fade", 24000, "Fade 3"),
            ("clip", 96000, 48000),
        ],
        start_timecode_frames=0,
    )
    report = Report(strict=False)
    session = read_aaf(aaf, report, fades="absorb")
    assert len(session.tracks[0].clips) == 2
    clip_a, clip_b = session.tracks[0].clips

    assert clip_a.timeline_start_samples == 0
    assert clip_a.timeline_end_samples == 120000
    assert clip_a.fade_out is not None
    assert clip_a.fade_out.duration_samples == 24000

    assert clip_b.timeline_start_samples == 120000  # Original position (96000 + 24000)
    assert clip_b.timeline_end_samples == 216000
    assert clip_b.source_offset_samples == 48000  # Original offset
    assert getattr(clip_b, "fade_in", None) is None
    assert clip_b.crossfade_in_state is True

    # Continuidade exata: A.end == B.start
    assert clip_a.timeline_end_samples == clip_b.timeline_start_samples


def test_crossfade_insufficient_offset(tmp_path, wav):
    """Se B nao tem offset suficiente, ambas as pontas do crossfade sao encurtadas."""
    aaf = build_aaf_with_rendered_fades(
        tmp_path / "crossfade_clamp.aaf",
        wav,
        [
            ("clip", 96000, 0),
            ("fade", 24000, "Fade 3"),
            ("clip", 96000, 10000),
        ],
        start_timecode_frames=0,
    )
    report = Report(strict=False)
    session = read_aaf(aaf, report, fades="absorb")
    assert len(session.tracks[0].clips) == 2
    clip_a, clip_b = session.tracks[0].clips

    assert clip_a.timeline_end_samples == 106000
    assert clip_a.fade_out.duration_samples == 10000
    assert clip_b.crossfade_in_state is True

    issues = [i for i in report.issues if i.code == "FADE_CLAMPED_START"]
    assert len(issues) == 1


def test_insufficient_offset_clamps_fade_in_and_emits_warning(tmp_path, wav):
    """Clip com offset de 5000 samples e fade-in de 15000 samples.

    Deve limitar fade_in a 5000, recuar inicio em 5000, zerar offset e emitir FADE_CLAMPED_START.
    """
    aaf = build_aaf_with_rendered_fades(
        tmp_path / "clamp_start.aaf",
        wav,
        [
            ("filler", 48000),
            ("fade", 15000, "Fade 5"),
            ("clip", 96000, 5000),
        ],
        start_timecode_frames=0,
    )
    report = Report(strict=False)
    session = read_aaf(aaf, report, fades="absorb")
    clip = session.tracks[0].clips[0]

    # Original start was 48000 + 15000 = 63000. Recedes by clamped 5000 -> 58000
    assert clip.timeline_start_samples == 58000
    assert clip.source_offset_samples == 0
    assert clip.fade_in is not None
    assert clip.fade_in.duration_samples == 5000

    issues = [i for i in report.issues if i.code == "FADE_CLAMPED_START"]
    assert len(issues) == 1
    assert issues[0].severity == "warning"
    assert issues[0].context["requested"] == 15000
    assert issues[0].context["applied"] == 5000


def test_adjacent_fades_pair(tmp_path, wav):
    """ClipA(48000) -> FadeOut(10000) -> FadeIn(15000) -> ClipB(48000, offset=20000).

    O primeiro fade e associado como fade-out ao Clip A.
    O segundo fade e associado como fade-in ao Clip B.
    """
    aaf = build_aaf_with_rendered_fades(
        tmp_path / "adjacent_fades.aaf",
        wav,
        [
            ("clip", 48000, 0),
            ("fade", 10000, "Fade 6"),
            ("fade", 15000, "Fade 7"),
            ("clip", 48000, 20000),
        ],
        start_timecode_frames=0,
    )
    report = Report(strict=False)
    session = read_aaf(aaf, report, fades="absorb")
    assert len(session.tracks[0].clips) == 2
    clip_a, clip_b = session.tracks[0].clips

    assert clip_a.fade_out is not None
    assert clip_a.fade_out.duration_samples == 10000
    assert clip_a.duration_samples == 58000

    assert clip_b.fade_in is not None
    assert clip_b.fade_in.duration_samples == 15000
    assert clip_b.source_offset_samples == 5000  # 20000 - 15000
    assert clip_b.duration_samples == 63000


def test_zero_length_components_are_skipped(tmp_path, wav):
    """Componentes com length == 0 nao devem interferir na busca de vizinhos."""
    aaf = build_aaf_with_rendered_fades(
        tmp_path / "zero_len.aaf",
        wav,
        [
            ("clip", 48000, 0),
            ("filler", 0),
            ("fade", 12000, "Fade 8"),
            ("filler", 0),
            ("clip", 48000, 20000),
        ],
        start_timecode_frames=0,
    )
    report = Report(strict=False)
    session = read_aaf(aaf, report, fades="absorb")
    assert len(session.tracks[0].clips) == 2
    clip_a, clip_b = session.tracks[0].clips

    # Crossfade correctly absorbed between A and B
    assert clip_a.fade_out.duration_samples == 12000
    assert clip_b.crossfade_in_state is True
    assert clip_a.timeline_end_samples == clip_b.timeline_start_samples


def test_short_clip_warning_emitted(tmp_path, wav):
    """Clip com duracao < 1000 samples deve emitir CLIP_VERY_SHORT."""
    aaf = build_aaf_with_rendered_fades(
        tmp_path / "short_clip.aaf",
        wav,
        [
            ("clip", 500, 0),
        ],
        start_timecode_frames=0,
    )
    report = Report(strict=False)
    read_aaf(aaf, report, fades="absorb")
    issues = [i for i in report.issues if i.code == "CLIP_VERY_SHORT"]
    assert len(issues) == 1
    assert issues[0].severity == "warning"
    assert issues[0].context["duration_samples"] == 500


def test_fade_render_discarded_info_emitted_once_per_conversion(tmp_path, wav):
    """Emissao global de FADE_RENDER_DISCARDED ocorre uma vez com a contagem total."""
    aaf = build_aaf_with_rendered_fades(
        tmp_path / "discarded_notice.aaf",
        wav,
        [
            ("fade", 10000, "Fade 9"),
            ("clip", 48000, 20000),
            ("fade", 10000, "Fade 10"),
        ],
        start_timecode_frames=0,
    )
    report = Report(strict=False)
    read_aaf(aaf, report, fades="absorb")
    issues = [i for i in report.issues if i.code == "FADE_RENDER_DISCARDED"]
    assert len(issues) == 1
    assert issues[0].severity == "info"
    assert issues[0].context["fades_absorbed"] == 2
    assert "--fades keep" in issues[0].message


def test_keep_mode_preserves_all_segments_as_clips(tmp_path, wav):
    """Modo keep mantem todos os segmentos como clips individuais sem absorcao."""
    aaf = build_aaf_with_rendered_fades(
        tmp_path / "keep_mode.aaf",
        wav,
        [
            ("filler", 48000),
            ("fade", 10000, "Fade 11"),
            ("clip", 48000, 20000),
            ("fade", 10000, "Fade 12"),
        ],
        start_timecode_frames=0,
    )
    report = Report(strict=False)
    session = read_aaf(aaf, report, fades="keep")
    assert len(session.tracks[0].clips) == 3
    # Nenhum fade nativo atribuido
    for clip in session.tracks[0].clips:
        assert clip.fade_in is None
        assert clip.fade_out is None
    # Nenhum FADE_RENDER_DISCARDED emitido
    assert not any(i.code == "FADE_RENDER_DISCARDED" for i in report.issues)


def test_real_protools_fixture_absorb_mode_has_84_clips_and_valid_fades():
    """No fixture real TESTE_AFF_PROTOOLS.aaf em modo absorb:
    - Exatamente 90 clips (84 de midia + 6 fades que a fonte nao cobre)
    - Varios clips com fade_in > 0 e fade_out > 0
    - Nenhum clip com fade_in + fade_out > duracao
    - Emite FADE_RENDER_DISCARDED com 95 fades absorvidos (101 menos os 6
      mantidos como clip porque a fonte nao os cobre).
    """
    pt_aaf = fixture_aaf(PT_AAF)

    report = Report(strict=False)
    session = read_aaf(pt_aaf, report, fades="absorb")

    all_clips = list(session.all_clips())
    assert len(all_clips) == 90

    fade_in_count = sum(1 for c in all_clips if c.fade_in and c.fade_in.duration_samples > 0)
    fade_out_count = sum(1 for c in all_clips if c.fade_out and c.fade_out.duration_samples > 0)

    assert fade_in_count > 0
    assert fade_out_count > 0

    # Invariante de duracao de fade
    for clip in all_clips:
        fi = clip.fade_in.duration_samples if clip.fade_in else 0
        fo = clip.fade_out.duration_samples if clip.fade_out else 0
        assert fi + fo <= clip.duration_samples, f"Clip {clip.name} excedeu duracao"

    discard_issues = [i for i in report.issues if i.code == "FADE_RENDER_DISCARDED"]
    assert len(discard_issues) == 1
    assert discard_issues[0].context["fades_absorbed"] == 95

    mantidos = [i for i in report.issues if i.code == "FADE_KEPT_SUMMARY"]
    assert len(mantidos) == 1
    assert mantidos[0].context["fades_kept"] == 6
    # 95 + 6 = 101: nenhum fade sumiu, eles so mudaram de destino.
    assert discard_issues[0].context["fades_absorbed"] + mantidos[0].context["fades_kept"] == 101


def test_real_protools_fixture_keep_mode_has_185_clips():
    """No fixture real TESTE_AFF_PROTOOLS.aaf em modo keep:
    - Exatamente 185 clips
    - Sem fades absorvidos.
    """
    pt_aaf = fixture_aaf(PT_AAF)

    report = Report(strict=False)
    session = read_aaf(pt_aaf, report, fades="keep")

    all_clips = list(session.all_clips())
    assert len(all_clips) == 185
