"""Par L/R ao lado de uma track mono com o mesmo nome base.

O AAF do Premiere de um filme real de teste traz, para cada numero,
``Audio N_L``, ``Audio N_R`` e ``Audio N``. Antes, a track sem sufixo entrava
no grupo de candidatos e o par nunca virava estereo.
"""

from __future__ import annotations

from aafton.ir import SessionIR, TrackIR
from aafton.report import Report
from aafton.stereo import process_stereo_pairing
from aafton.timecode import TimecodeRate


def _track(idx: int, name: str) -> TrackIR:
    return TrackIR(id=f"t{idx}", source_index=idx, name=name, kind="audio", channel_count=1)


def _session(*names: str) -> SessionIR:
    return SessionIR(
        name="homonima",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=0,
        tracks=[_track(i, n) for i, n in enumerate(names)],
    )


def test_par_lr_com_track_mono_homonima_vira_estereo():
    session = _session("Audio 1_L", "Audio 1_R", "Audio 1")

    process_stereo_pairing(session, "pair", Report(strict=False), None, dry_run=True)

    nomes = [t.name for t in session.tracks]
    assert nomes == ["Audio 1 L/R", "Audio 1"]
    par = session.tracks[0]
    assert par.channel_count == 2
    assert par.original_source_indices == [0, 1]
    assert session.tracks[1].original_source_indices == [2]


def test_par_lr_sem_homonima_mantem_o_nome_base():
    session = _session("Audio 1_L", "Audio 1_R", "Audio 2")

    process_stereo_pairing(session, "pair", Report(strict=False), None, dry_run=True)

    assert [t.name for t in session.tracks] == ["Audio 1", "Audio 2"]


def test_tres_tracks_com_sufixo_nao_formam_par():
    session = _session("Audio 1_L", "Audio 1_R", "Audio 1 [L]")

    process_stereo_pairing(session, "pair", Report(strict=False), None, dry_run=True)

    assert len(session.tracks) == 3


def test_sufixos_de_51_continuam_impedindo_o_par():
    session = _session("5.1 [L]", "5.1 [C]", "5.1 [R]", "5.1 [Ls]", "5.1 [Rs]", "5.1 [LFE]")

    process_stereo_pairing(session, "pair", Report(strict=False), None, dry_run=True)

    assert len(session.tracks) == 6
