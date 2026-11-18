"""Testes das tracks mudas fora da organizacao.

A track muda do editor fica como esta: os clips dela nao mudam de track, a
track nao vira track de destino e a envelope dela nao e mexida. O motivo esta
no .als: o escritor nao grava o mudo do clip (als_write_clips.py e
als_paths.AUDIO_CLIP_FIELDS), e um clip que sai de uma track muda para uma
track audivel passaria a tocar.
"""

from __future__ import annotations

from aafton.ir import ClipIR, SessionIR, TrackIR
from aafton.organize_apply import annotate_origin_keys, apply_proposal
from aafton.organize_plan import Proposal, build_proposal
from aafton.organize_units import origin_keys_for_track
from aafton.report import Report
from aafton.timecode import TimecodeRate

CLIP_DX = "025CENA37APL3TK01-025.WAV"
CLIP_DX_MUDO = "026CENA38APL3TK01-026.WAV"
CLIP_CAM = "A024_A021_0519VG_001.r3d"


def _sessao() -> SessionIR:
    """Sessao de teste: uma track audivel e uma muda, do mesmo tipo."""
    return SessionIR(
        name="mudas",
        sample_rate=48000,
        timecode_rate=TimecodeRate(24, 1),
        start_timecode_samples=0,
    )


def _track(
    session: SessionIR,
    source_index: int,
    name: str,
    trechos: list[tuple[str, int, int]],
    *,
    muted: bool = False,
    envelope: list[tuple[int, float]] | None = None,
) -> TrackIR:
    """Track do editor com um clip por trecho dado, sem midia."""
    track_id = f"trk_{source_index}"
    clips = [
        ClipIR(
            id=f"c_{source_index}_{pos}",
            track_id=track_id,
            channel_index=0,
            event_index=pos,
            name=clip_name,
            media_id=None,
            timeline_start_samples=inicio,
            timeline_end_samples=fim,
        )
        for pos, (clip_name, inicio, fim) in enumerate(trechos)
    ]
    track = TrackIR(
        id=track_id,
        source_index=source_index,
        name=name,
        kind="audio",
        channel_count=1,
        clips=clips,
        muted=muted,
    )
    if envelope is not None:
        track.gain_points = list(envelope)
    session.tracks.append(track)
    return track


def _proposta(session: SessionIR) -> Proposal:
    """A proposta da organizacao, com o sinal de nome so."""
    return build_proposal(
        session,
        stereo="split",
        level="permissive",
        signals=("name",),
        media_meta=lambda media_id: None,
    )


def test_track_muda_fica_fora_de_todo_grupo() -> None:
    """A track muta nao entra em nenhum grupo da proposta."""
    session = _sessao()
    _track(session, 0, "Audio 1", [(CLIP_DX, 0, 1000)])
    mudo = _track(session, 1, "Audio 2", [(CLIP_DX_MUDO, 0, 1000)], muted=True)
    proposta = _proposta(session)

    chaves_mudas = set(origin_keys_for_track(mudo).values())
    assert chaves_mudas
    for grupo in proposta.groups:
        for destino in grupo.tracks:
            for unidade in destino.units:
                assert not chaves_mudas.intersection(unidade.origin_keys)


def test_apply_proposal_deixa_a_track_muda_como_estava() -> None:
    """A track muta fica com os clips, o mudo e a envelope; a audivel e organizada."""
    session = _sessao()
    audivel = _track(session, 0, "Audio 1", [(CLIP_DX, 0, 1000), (CLIP_CAM, 2000, 3000)])
    mudo = _track(
        session,
        1,
        "Audio 2",
        [(CLIP_DX_MUDO, 0, 1000)],
        muted=True,
        envelope=[(0, 0.5)],
    )
    _track(session, 2, "Audio 3", [], muted=True)
    proposta = _proposta(session)
    report = Report(strict=False)
    annotate_origin_keys(session)
    apply_proposal(session, proposta, colors=False, report=report)

    # A track muta ficou como estava: nome, mudo, clips e envelope.
    assert mudo.name == "Audio 2"
    assert mudo.muted is True
    assert [c.name for c in mudo.clips] == [CLIP_DX_MUDO]
    assert mudo.gain_points == [(0, 0.5)]

    # A track audivel virou track de destino e o clip de camera mudou de track.
    assert audivel.name == "PROD 1"
    assert [c.name for c in audivel.clips] == [CLIP_DX]
    cam = next(t for t in session.tracks if t.name == "CAM 1")
    assert [c.name for c in cam.clips] == [CLIP_CAM]

    # O relatorio avisa uma vez a track muta que tem clip.
    issues = [i for i in report.issues if i.code == "ORGANIZE_MUTED_TRACK_KEPT"]
    assert len(issues) == 1
    assert issues[0].severity == "info"
    assert issues[0].context == {"track": "Audio 2"}
