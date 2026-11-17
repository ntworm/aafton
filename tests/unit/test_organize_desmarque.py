"""Testes do desmarque por chave de track na revisao da organizacao.

Desmarcar uma track de destino pela chave faz a proposta ser montada de novo:
a compactacao reempacota o bloco e as chaves mudam. Uma chave que so aparece
na disposicao reempacotada tambem desmarca, e uma chave que volta com outros
clips desmarca de novo.
"""

from __future__ import annotations

from aafton.ir import ClipIR, MediaAsset, SessionIR, TrackIR
from aafton.organize_plan import Edits, Proposal, build_proposal
from aafton.organize_units import OriginKey
from aafton.timecode import TimecodeRate

SINAIS_COM_COMPACT = frozenset({"file", "meta", "name", "compact"})

# Cinco tracks de producao: os clips de 1, 2 e 5 se sobrepoem, o de 3 e o de 4
# nao. A compactacao junta os que nao se atrapalham em tres tracks de destino.
SESSAO_CINCO = (
    (0, "Audio 1", "001CENA01PL1TK1-001.WAV", 0, 1000),
    (1, "Audio 2", "002CENA02PL1TK1-002.WAV", 0, 1000),
    (2, "Audio 3", "003CENA03PL1TK1-003.WAV", 2000, 3000),
    (3, "Audio 4", "004CENA04PL1TK1-004.WAV", 4000, 5000),
    (4, "Audio 5", "005CENA05PL1TK1-005.WAV", 0, 1000),
)

# Tres tracks: o clip de 1 comeca no meio do de 2, entao a compactacao empacota
# o de 3 na primeira track, o de 2 na segunda e o de 1 na terceira. A chave
# "Audio 3" da primeira disposicao carrega o clip de 3 e, depois do desmarque,
# volta com o clip de 2.
SESSAO_TROCA = (
    (0, "Audio 1", "001CENA01PL1TK1-001.WAV", 500, 1500),
    (1, "Audio 2", "002CENA02PL1TK1-002.WAV", 0, 1000),
    (2, "Audio 3", "003CENA03PL1TK1-003.WAV", 0, 1000),
)


def _sessao(clipes: tuple[tuple[int, str, str, int, int], ...]) -> SessionIR:
    """Sessao sintetica: cada track do editor recebe o clip informado."""
    session = SessionIR(
        name="Desmarque",
        sample_rate=48000,
        timecode_rate=TimecodeRate(24, 1),
        start_timecode_samples=0,
    )
    for indice, nome, arquivo, inicio, fim in clipes:
        media_id = f"m_{indice}"
        session.media.append(MediaAsset(id=media_id, filename=arquivo, channels=1))
        clip = ClipIR(
            id=f"c_{indice}",
            track_id=f"trk_{indice}",
            channel_index=0,
            event_index=0,
            name=arquivo,
            media_id=media_id,
            timeline_start_samples=inicio,
            timeline_end_samples=fim,
        )
        session.tracks.append(
            TrackIR(
                id=f"trk_{indice}",
                source_index=indice,
                name=nome,
                kind="audio",
                channel_count=1,
                clips=[clip],
            )
        )
    return session


def _proposta(session: SessionIR, edits: Edits | None = None) -> Proposal:
    """Proposta de organizacao da sessao com a compactacao ligada."""
    return build_proposal(
        session,
        stereo="split",
        level="permissive",
        signals=SINAIS_COM_COMPACT,
        edits=edits,
        accepted_pairs=set(),
        media_meta=lambda _media_id: None,
    )


def _chaves(proposta: Proposal) -> list[str]:
    """Chaves das tracks de destino, na ordem da proposta."""
    return [track.key for grupo in proposta.groups for track in grupo.tracks]


def _aceitos(proposta: Proposal) -> set[OriginKey]:
    """Chaves de origem dos clips aceitos nas tracks de destino."""
    return {
        origem
        for grupo in proposta.groups
        for track in grupo.tracks
        for unidade in track.units
        if unidade.accepted
        for origem in unidade.origin_keys
    }


def test_chave_vista_somente_na_segunda_disposicao_desmarca() -> None:
    """Uma chave que so aparece na disposicao reempacotada tambem desmarca."""
    session = _sessao(SESSAO_CINCO)
    base = _proposta(session)
    assert _chaves(base) == ["PRODUCTION:Audio 1", "PRODUCTION:Audio 2", "PRODUCTION:Audio 3"]

    # "PRODUCTION:Audio 5" nao existe na primeira disposicao: ela so nasce
    # depois que o bloco e reempacotado pelo desmarque da track 1.
    edits = Edits(tracks={"PRODUCTION:Audio 1": False, "PRODUCTION:Audio 5": False})
    proposta = _proposta(session, edits=edits)
    assert _chaves(proposta) == ["PRODUCTION:Audio 2"]
    assert _aceitos(proposta) == {(1, 0)}


def test_chave_que_reaparece_com_outras_origens_desmarca_de_novo() -> None:
    """A chave que volta com outros clips desmarca de novo, com as novas origens."""
    session = _sessao(SESSAO_TROCA)
    base = _proposta(session)
    # A primeira disposicao empacota os clips fora da ordem do editor.
    assert _chaves(base) == ["PRODUCTION:Audio 1", "PRODUCTION:Audio 2", "PRODUCTION:Audio 3"]

    edits = Edits(tracks={"PRODUCTION:Audio 3": False})
    proposta = _proposta(session, edits=edits)
    # A chave "Audio 3" voltou com o clip 2: ele tambem foi desmarcado.
    assert _chaves(proposta) == ["PRODUCTION:Audio 2"]
    assert _aceitos(proposta) == {(1, 0)}


def test_chave_sem_correspondencia_e_ignorada() -> None:
    """Uma chave de edits.tracks que nao existe em nenhuma disposicao e ignorada."""
    session = _sessao(SESSAO_CINCO)
    base = _proposta(session)
    edits = Edits(tracks={"PRODUCTION:Audio 9": False})
    proposta = _proposta(session, edits=edits)
    assert _chaves(proposta) == _chaves(base)
    assert _aceitos(proposta) == _aceitos(base)


def test_desmarcar_por_chave_e_por_origem_dao_o_mesmo_resultado() -> None:
    """Desmarcada a track pela chave ou pelos clips dela, o resultado e o mesmo."""
    session = _sessao(SESSAO_CINCO)
    por_chave = _proposta(session, edits=Edits(tracks={"PRODUCTION:Audio 1": False}))
    por_origem = _proposta(
        session,
        edits=Edits(origins={(0, 0): False, (2, 0): False, (3, 0): False}),
    )
    assert _chaves(por_chave) == _chaves(por_origem) == ["PRODUCTION:Audio 2", "PRODUCTION:Audio 5"]
    assert _aceitos(por_chave) == _aceitos(por_origem) == {(1, 0), (4, 0)}
