"""Tracks do editor que a organizacao esvaziou (P4).

A organizacao move os clips do editor para as tracks de destino. A track do
editor que ficou sem clip nenhum nao tem mais o que levar para o set do Live:
ela sai da sessao, mesmo que tenha envelope proprio, porque o envelope foi com
os clips para as tracks de destino. A track que ja estava vazia antes de
organizar fica como esta.
"""

from __future__ import annotations

import pytest

from aafton.cli_organize import SINAIS, _sinais
from aafton.ir import ClipIR, MediaAsset, SessionIR, TrackIR
from aafton.organize_apply import annotate_origin_keys, apply_proposal
from aafton.organize_plan import build_proposal
from aafton.report import Report
from aafton.stereo import process_stereo_pairing
from aafton.studio import app_convert, app_organize
from aafton.timecode import TimecodeRate

SINAIS_COM_COMPACT = ("file", "meta", "name", "family", "color", "compact")
SINAIS_SEM_COMPACT = ("file", "meta", "name", "family", "color")


def _clip(
    clip_id: str, track_id: str, nome: str, inicio: int, fim: int
) -> tuple[ClipIR, MediaAsset]:
    """Clip de teste com a midia resolvida."""
    media_id = f"m_{clip_id}"
    asset = MediaAsset(
        id=media_id,
        filename=nome,
        channels=1,
        sample_rate=48000,
        duration_samples=max(48000, fim - inicio),
        status="resolved",
        resolved_path=f"/fake/{media_id}.wav",
    )
    clip = ClipIR(
        id=clip_id,
        track_id=track_id,
        channel_index=0,
        event_index=0,
        name=nome,
        media_id=media_id,
        timeline_start_samples=inicio,
        timeline_end_samples=fim,
    )
    return clip, asset


def _add_track(
    session: SessionIR,
    source_index: int,
    name: str,
    specs: list[tuple[str, int, int]],
) -> TrackIR:
    """Track do editor com os clips dados."""
    track_id = f"trk_{source_index}"
    clips: list[ClipIR] = []
    for ev_idx, (clip_name, inicio, fim) in enumerate(specs):
        clip, asset = _clip(f"c_{source_index}_{ev_idx}", track_id, clip_name, inicio, fim)
        clips.append(clip)
        session.media.append(asset)
    track = TrackIR(
        id=track_id,
        source_index=source_index,
        name=name,
        kind="audio",
        channel_count=1,
        clips=clips,
    )
    session.tracks.append(track)
    return track


def _sessao(com_envelope: bool = True) -> SessionIR:
    """Tres tracks de producao, uma track vazia e uma track com envelope."""
    session = SessionIR(
        name="vazias",
        sample_rate=48000,
        timecode_rate=TimecodeRate(24, 1),
        start_timecode_samples=0,
    )
    _add_track(session, 0, "Audio 1", [("Take 1", 0, 1000)])
    _add_track(session, 1, "Audio 2", [("Take 2", 2000, 3000)])
    _add_track(session, 2, "Audio 3", [])
    _add_track(session, 3, "Audio 4", [("Take 4", 4000, 5000)])
    if com_envelope:
        # A automacao de ganho que o editor gravou na track fica com ela.
        session.tracks[3].gain_points = [(0, 1.0), (48000, 0.5)]
    return session


def _proposta(session: SessionIR, sinais: tuple[str, ...]):
    """A proposta da sessao com os sinais dados."""
    annotate_origin_keys(session)
    return build_proposal(
        session,
        stereo="split",
        level="permissive",
        signals=sinais,
        media_meta=lambda _: None,
    )


def test_track_esvaziada_sai_da_sessao() -> None:
    """A track do editor que ficou sem clip nao vai para o set do Live."""
    session = _sessao(com_envelope=False)
    proposta = _proposta(session, SINAIS_COM_COMPACT)
    # A compactacao junta as tres tracks de producao num bloco so.
    assert proposta.compactacao == {"PRODUCTION": (3, 1)}
    assert apply_proposal(session, proposta, colors=True) == []
    # A Audio 2 perdeu o clip para a Audio 1 e nao fica no set.
    assert [t.name for t in session.tracks] == ["PROD 1", "Audio 3"]


def test_track_esvaziada_com_envelope_sai() -> None:
    """A track esvaziada sai mesmo com envelope proprio: ele foi com os clips."""
    session = _sessao(com_envelope=True)
    proposta = _proposta(session, SINAIS_COM_COMPACT)
    report = Report(strict=False)
    apply_proposal(session, proposta, colors=True, report=report)
    # A Audio 4 perdeu o clip para a Audio 1 e nao fica no set.
    assert [t.name for t in session.tracks] == ["PROD 1", "Audio 3"]
    assert not [i for i in report.issues if i.code == "ORGANIZE_EMPTY_TRACK_KEPT"]


def test_track_vazia_do_comeco_fica() -> None:
    """A track que ja estava vazia antes de organizar fica como esta."""
    session = _sessao(com_envelope=False)
    proposta = _proposta(session, SINAIS_COM_COMPACT)
    apply_proposal(session, proposta, colors=True)
    assert "Audio 3" in [t.name for t in session.tracks]


def _sessao_par() -> SessionIR:
    """Par L/R que o editor gravou em duas tracks separadas."""
    session = SessionIR(
        name="par",
        sample_rate=48000,
        timecode_rate=TimecodeRate(24, 1),
        start_timecode_samples=0,
    )
    _add_track(session, 0, "Audio 1_L", [("Take 1_L", 0, 1000)])
    _add_track(session, 1, "Audio 1_R", [("Take 1_R", 0, 1000)])
    return session


def test_destino_sem_clip_nao_e_escrito(monkeypatch: pytest.MonkeyPatch) -> None:
    """A track de destino que nao recebeu clip nao vai para o set do Live."""
    monkeypatch.setattr(
        "aafton.stereo.read_audio_metadata",
        lambda path: {
            "channels": 1,
            "sample_rate": 48000,
            "duration_samples": 48000,
            "bits": 24,
        },
    )
    session = _sessao_par()
    annotate_origin_keys(session)
    proposta = build_proposal(
        session,
        stereo="pair",
        level="permissive",
        signals=SINAIS_COM_COMPACT,
        media_meta=lambda _: None,
    )
    # O pareamento nao juntou os dois lados: a unidade do par nao vira um clip so.
    process_stereo_pairing(session, "split", Report(strict=False), None, dry_run=True)
    unmatched = apply_proposal(session, proposta, colors=True)
    assert len(unmatched) == 1
    # Nenhum destino novo no set e nenhuma track vazia.
    assert [t.name for t in session.tracks] == ["Audio 1_L", "Audio 1_R"]
    assert all(t.clips for t in session.tracks)


def test_app_soma_compact_aos_sinais_da_pagina() -> None:
    """A pagina nao tem controle da compactacao: ela entra em toda proposta."""
    _, _, sinais, _ = app_organize._parametros({"signals": ["file", "meta", "name"]})
    assert sinais == ("file", "meta", "name", "compact")
    _, _, sem_sinais, _ = app_organize._parametros({})
    assert "compact" in sem_sinais
    opcoes = app_convert._opcoes_de_organizacao({"organize": "type", "signals": ["file", "meta"]})
    assert opcoes["organize_signals"] == ("file", "meta", "compact")


def test_cli_sem_compact_da_a_proposta_de_hoje() -> None:
    """Sem o sinal compact a proposta sai com uma track por track do editor."""
    session = _sessao(com_envelope=False)
    sem = _proposta(session, SINAIS_SEM_COMPACT)
    com = _proposta(session, SINAIS_COM_COMPACT)
    assert sem.compactacao == {}
    assert [t.source_track for t in sem.groups[0].tracks] == ["Audio 1", "Audio 2", "Audio 4"]
    assert [t.source_track for t in com.groups[0].tracks] == ["Audio 1"]
    # O CLI aceita o sinal novo e o mostra na ajuda.
    assert "compact" in SINAIS
    assert _sinais("file,compact") == ("file", "compact")
