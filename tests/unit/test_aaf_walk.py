import pytest

from aafton.aaf_read import read_aaf
from aafton.report import Report
from tests.aaf_factory import build_aaf_with_transition, build_linked_aaf, wav_bytes


@pytest.fixture
def wav(tmp_path):
    caminho = tmp_path / "cena12_tk3.wav"
    caminho.write_bytes(wav_bytes(48000 * 60))
    return caminho


def test_um_clip_na_origem(tmp_path, wav):
    aaf = build_linked_aaf(tmp_path / "cena.aaf", [(0, 96000, 0)], wav, start_timecode_frames=None)
    session = read_aaf(aaf, Report(strict=False))
    clip = session.tracks[0].clips[0]
    assert clip.timeline_start_samples == 0
    assert clip.duration_samples == 96000


def test_filler_avanca_o_cursor_e_nao_gera_clip(tmp_path, wav):
    aaf = build_linked_aaf(
        tmp_path / "cena.aaf", [(0, 96000, 0), (192000, 48000, 0)], wav, start_timecode_frames=None
    )
    session = read_aaf(aaf, Report(strict=False))
    clips = session.tracks[0].clips
    assert len(clips) == 2
    assert clips[1].timeline_start_samples == 192000


def test_offset_na_fonte_vem_do_start_do_source_clip(tmp_path, wav):
    aaf = build_linked_aaf(
        tmp_path / "cena.aaf", [(0, 96000, 24000)], wav, start_timecode_frames=None
    )
    clip = read_aaf(aaf, Report(strict=False)).tracks[0].clips[0]
    assert clip.source_offset_samples == 24000


def test_transition_recua_o_cursor_criando_sobreposicao(tmp_path, wav):
    aaf = build_aaf_with_transition(
        tmp_path / "cross.aaf",
        wav,
        first_duration=96000,
        second_duration=96000,
        transition_length=24000,
    )
    clips = read_aaf(aaf, Report(strict=False)).tracks[0].clips
    assert len(clips) == 2
    assert clips[0].timeline_end_samples == 96000
    # o segundo clip comeca 24000 samples antes do fim do primeiro
    assert clips[1].timeline_start_samples == 96000 - 24000


def test_transition_gera_fades_do_comprimento_da_sobreposicao(tmp_path, wav):
    aaf = build_aaf_with_transition(tmp_path / "cross.aaf", wav, transition_length=24000)
    clips = read_aaf(aaf, Report(strict=False)).tracks[0].clips
    assert clips[0].fade_out is not None
    assert clips[0].fade_out.duration_samples == 24000
    assert clips[1].fade_in is not None
    assert clips[1].fade_in.duration_samples == 24000


def test_nome_da_track_vem_do_slot(tmp_path, wav):
    aaf = build_linked_aaf(
        tmp_path / "cena.aaf",
        [(0, 96000, 0)],
        wav,
        track_name="AMBIENTE PRAIA",
        start_timecode_frames=None,
    )
    session = read_aaf(aaf, Report(strict=False))
    assert session.tracks[0].name == "AMBIENTE PRAIA"


def test_sem_timecode_slot_gera_aviso_e_assume_zero(tmp_path, wav):
    aaf = build_linked_aaf(tmp_path / "cena.aaf", [(0, 96000, 0)], wav, start_timecode_frames=None)
    report = Report(strict=False)
    session = read_aaf(aaf, report)
    assert session.start_timecode_samples == 0
    assert "AAF_NO_TIMECODE" in [i.code for i in report.issues]


def test_todos_os_clips_apontam_para_um_media_asset(tmp_path, wav):
    aaf = build_linked_aaf(
        tmp_path / "cena.aaf", [(0, 96000, 0), (192000, 48000, 0)], wav, start_timecode_frames=None
    )
    session = read_aaf(aaf, Report(strict=False))
    for clip in session.all_clips():
        assert session.media_by_id(clip.media_id) is not None
