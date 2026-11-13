import pytest
from aafton.ir import ClipIR, FadeIR, MarkerIR, MediaAsset, SessionIR, TrackIR
from aafton.timecode import TimecodeRate


def _clip(**kwargs):
    base = dict(
        id="c1",
        track_id="t1",
        channel_index=0,
        event_index=0,
        name="cena12_tk3.wav",
        media_id="m1",
        timeline_start_samples=172800000,
        timeline_end_samples=172848000,
        source_offset_samples=0,
    )
    base.update(kwargs)
    return ClipIR(**base)


def test_duracao_e_derivada_e_nao_armazenada():
    clip = _clip()
    assert clip.duration_samples == 48000


def test_clip_com_fim_antes_do_inicio_e_recusado():
    with pytest.raises(ValueError, match="ends before it starts"):
        _clip(timeline_end_samples=1, timeline_start_samples=2)


def test_offset_negativo_na_fonte_e_recusado():
    with pytest.raises(ValueError, match="offset"):
        _clip(source_offset_samples=-1)


def test_tempo_desconhecido_fica_none_e_nao_zero():
    clip = _clip(source_offset_samples=None)
    assert clip.source_offset_samples is None
    assert clip.duration_samples == 48000


def test_session_expoe_todos_os_clips_de_todas_as_tracks():
    track = TrackIR(id="t1", source_index=0, name="DIALOGO 1", kind="audio", channel_count=1)
    track.clips.append(_clip())
    track.clips.append(
        _clip(id="c2", timeline_start_samples=172848000, timeline_end_samples=172900000)
    )
    session = SessionIR(
        name="cena12",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=172800000,
        tracks=[track],
    )
    assert [c.id for c in session.all_clips()] == ["c1", "c2"]


def test_fade_com_duracao_negativa_e_recusado():
    with pytest.raises(ValueError, match="duration"):
        FadeIR(kind="in", duration_samples=-1)


def test_marker_e_media_guardam_o_que_o_nome_diz():
    marker = MarkerIR(id="k1", name="corte seco", position_samples=172800000)
    assert marker.position_samples == 172800000
    asset = MediaAsset(id="m1", filename="cena12_tk3.wav", channels=1, sample_rate=48000)
    assert asset.resolved_path is None
