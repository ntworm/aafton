"""Testes da compactacao das tracks de destino da organizacao de sessao."""

from __future__ import annotations

from aafton.ir import ClipIR, SessionIR, TrackIR
from aafton.organize_classify import PRODUCTION
from aafton.organize_compact import compactar_homes
from aafton.organize_home import _HomeBuilder
from aafton.organize_plan import Proposal, build_proposal
from aafton.organize_units import Unit
from aafton.pipeline import _relatar_compactacao
from aafton.report import Report
from aafton.timecode import TimecodeRate


def _unit(
    fonte: tuple[int, ...],
    inicio: int,
    fim: int,
    *,
    canais: int = 1,
    tipo: str = PRODUCTION,
) -> Unit:
    """Unidade de teste, com uma chave de origem por track de origem."""
    return Unit(
        origin_keys=((fonte[0], 0),),
        source_tracks=fonte,
        source_label=f"PROD {fonte[0]}",
        channel_count=canais,
        start_samples=inicio,
        end_samples=fim,
        name=f"Take {fonte[0]}",
        media_filename=None,
        kind=tipo,
        score=1,
        conflict=False,
        reasons=(),
        accepted=True,
    )


def _home(
    tipo: str,
    nome: str,
    indices: tuple[int, ...],
    canais: int,
    unidades: list[Unit],
) -> _HomeBuilder:
    """Home de teste, com as spans das unidades."""
    home = _HomeBuilder(tipo, nome, indices, canais)
    for unit in unidades:
        home.units.append(unit)
        home.spans.append((unit.start_samples, unit.end_samples))
    return home


def _ordem_editor(*fontes: tuple[int, ...]) -> dict[tuple[int, ...], int]:
    """Posicao de cada track de origem na ordem do editor."""
    return {fonte: idx for idx, fonte in enumerate(fontes)}


def _resumo(homes: list[_HomeBuilder]) -> list[tuple[str | None, tuple[int, ...], tuple]]:
    """Resumo comparavel das homes: identidade e unidades de cada track."""
    return [
        (h.source_track, h.source_indices, tuple(u.origin_keys for u in h.units)) for h in homes
    ]


def test_track_estereo_some_de_entre_as_tracks_mono():
    """O take do editor tem mono em PROD 5, estereo em PROD 6 e mono em PROD 7."""
    unidades = [
        _unit((5,), 0, 100),
        _unit((6,), 0, 100, canais=2),
        _unit((7,), 0, 100),
    ]
    homes = [
        _home(PRODUCTION, "PROD 5", (5,), 1, [unidades[0]]),
        _home(PRODUCTION, "PROD 6", (6,), 2, [unidades[1]]),
        _home(PRODUCTION, "PROD 7", (7,), 1, [unidades[2]]),
    ]
    novas, contagem = compactar_homes(homes, _ordem_editor((5,), (6,), (7,)))
    # Os monos ficam juntos no comeco do grupo e o estereo vai para o fim.
    assert [h.channel_count for h in novas] == [1, 1, 2]
    assert [h.source_track for h in novas] == ["PROD 5", "PROD 7", "PROD 6"]
    assert contagem == {PRODUCTION: (3, 3)}


def test_numero_de_tracks_igual_a_concorrencia_maxima():
    """Duas unidades juntas no inicio e uma depois: bastam duas tracks."""
    unidades = [
        _unit((1,), 0, 100),
        _unit((2,), 0, 100),
        _unit((3,), 100, 200),
    ]
    homes = [
        _home(PRODUCTION, "PROD 1", (1,), 1, [unidades[0]]),
        _home(PRODUCTION, "PROD 2", (2,), 1, [unidades[1]]),
        _home(PRODUCTION, "PROD 3", (3,), 1, [unidades[2]]),
    ]
    novas, contagem = compactar_homes(homes, _ordem_editor((1,), (2,), (3,)))
    assert len(novas) == 2
    assert contagem == {PRODUCTION: (3, 2)}
    # A terceira unidade sobe para a track da primeira: nao ha track vazia entre elas.
    assert novas[0].units == [unidades[0], unidades[2]]
    assert novas[1].units == [unidades[1]]


def test_unidades_que_comecam_junto_guardam_a_ordem_do_editor():
    unidades = [_unit((3,), 0, 100), _unit((1,), 0, 100)]
    homes = [
        _home(PRODUCTION, "PROD 3", (3,), 1, [unidades[0]]),
        _home(PRODUCTION, "PROD 1", (1,), 1, [unidades[1]]),
    ]
    novas, _ = compactar_homes(homes, _ordem_editor((1,), (2,), (3,)))
    assert [u.source_tracks for h in novas for u in h.units] == [(1,), (3,)]
    assert [h.source_track for h in novas] == ["PROD 1", "PROD 3"]


def test_unidade_com_varios_clips_nao_e_dividida():
    """Uma unidade de cadeia fica inteira numa track, mesmo com outra por cima."""
    longa = _unit((1,), 0, 1000)
    outras = [_unit((2,), 0, 100), _unit((3,), 100, 200)]
    homes = [
        _home(PRODUCTION, "PROD 1", (1,), 1, [longa]),
        _home(PRODUCTION, "PROD 2", (2,), 1, [outras[0]]),
        _home(PRODUCTION, "PROD 3", (3,), 1, [outras[1]]),
    ]
    novas, contagem = compactar_homes(homes, _ordem_editor((1,), (2,), (3,)))
    com_a_longa = [h for h in novas if longa in h.units]
    assert len(com_a_longa) == 1
    assert com_a_longa[0].units == [longa]
    assert contagem == {PRODUCTION: (3, 2)}


def test_tracks_mono_e_estereo_nunca_se_misturam():
    unidades = [
        _unit((1,), 0, 100),
        _unit((2,), 0, 100, canais=2),
        _unit((3,), 0, 100),
    ]
    homes = [
        _home(PRODUCTION, "PROD 1", (1,), 1, [unidades[0]]),
        _home(PRODUCTION, "PROD 2", (2,), 2, [unidades[1]]),
        _home(PRODUCTION, "PROD 3", (3,), 1, [unidades[2]]),
    ]
    novas, _ = compactar_homes(homes, _ordem_editor((1,), (2,), (3,)))
    for home in novas:
        assert len({u.channel_count for u in home.units}) == 1


def test_identidade_da_home_original_e_herdada():
    """A track i do bloco herda a identidade da home i do bloco, na ordem do editor."""
    unidades = [_unit((1,), 0, 100), _unit((2,), 200, 300), _unit((3,), 400, 500)]
    homes = [
        _home(PRODUCTION, "PROD 1", (1,), 1, [unidades[0]]),
        _home(PRODUCTION, "PROD 2", (2,), 1, [unidades[1]]),
        _home(PRODUCTION, "PROD 3", (3,), 1, [unidades[2]]),
    ]
    novas, contagem = compactar_homes(homes, _ordem_editor((1,), (2,), (3,)))
    # As tres unidades nao se sobrepoem: uma track so, com a identidade da primeira home.
    assert len(novas) == 1
    assert novas[0].source_track == "PROD 1"
    assert novas[0].source_indices == (1,)
    assert contagem == {PRODUCTION: (3, 1)}


def test_track_nova_nasce_sem_identidade():
    """A home que recebeu unidade de outra track precisa de uma track nova."""
    propria = _unit((1,), 0, 100)
    movida = _unit((2,), 0, 100)
    homes = [_home(PRODUCTION, "PROD 1", (1,), 1, [propria, movida])]
    novas, contagem = compactar_homes(homes, _ordem_editor((1,), (2,)))
    assert len(novas) == 2
    assert novas[0].source_track == "PROD 1"
    assert novas[0].source_indices == (1,)
    assert novas[1].source_track is None
    assert novas[1].source_indices == ()
    assert contagem == {PRODUCTION: (1, 2)}


def test_home_sem_unidade_some_do_resultado():
    unidades = [_unit((1,), 0, 100)]
    homes = [
        _home(PRODUCTION, "PROD 1", (1,), 1, unidades),
        _HomeBuilder(PRODUCTION, "PROD 9", (9,), 1),
    ]
    novas, contagem = compactar_homes(homes, _ordem_editor((1,), (9,)))
    assert [h.source_track for h in novas] == ["PROD 1"]
    # A home vazia nem entra na conta: ela nao virava track no grupo.
    assert contagem == {PRODUCTION: (1, 1)}


def test_compactacao_e_deterministica():
    unidades = [_unit((1,), 0, 100), _unit((2,), 0, 100), _unit((3,), 200, 300)]
    homes = [
        _home(PRODUCTION, "PROD 1", (1,), 1, [unidades[0]]),
        _home(PRODUCTION, "PROD 2", (2,), 1, [unidades[1]]),
        _home(PRODUCTION, "PROD 3", (3,), 1, [unidades[2]]),
    ]
    ordem = _ordem_editor((1,), (2,), (3,))
    primeira, contagem1 = compactar_homes(homes, ordem)
    segunda, contagem2 = compactar_homes(homes, ordem)
    assert _resumo(primeira) == _resumo(segunda)
    assert contagem1 == contagem2


def _sessao() -> SessionIR:
    """Sessao de teste: tres tracks mono com um clip cada e uma track estereo."""
    session = SessionIR(
        name="compact",
        sample_rate=48000,
        timecode_rate=TimecodeRate(30, 1),
        start_timecode_samples=0,
    )
    specs = [
        (1, "PROD 1", 1, 0, 1000),
        (2, "PROD 2", 1, 0, 1000),
        (3, "PROD 3", 1, 1000, 2000),
        (4, "PROD 4", 2, 0, 1000),
    ]
    for src, nome, canais, inicio, fim in specs:
        clip = ClipIR(
            id=f"c{src}",
            track_id=f"trk{src}",
            channel_index=0,
            event_index=0,
            name=f"Take {src}",
            media_id=None,
            timeline_start_samples=inicio,
            timeline_end_samples=fim,
        )
        session.tracks.append(
            TrackIR(
                id=f"trk{src}",
                source_index=src,
                name=nome,
                kind="audio",
                channel_count=canais,
                clips=[clip],
            )
        )
    return session


def test_build_proposal_sem_compact_muda_nada():
    session = _sessao()
    proposta = build_proposal(
        session,
        stereo="split",
        level="permissive",
        signals={"name"},
        media_meta=lambda _: None,
    )
    assert proposta.compactacao == {}
    grupo = proposta.groups[0]
    assert grupo.key == PRODUCTION
    # Uma track por track do editor, como antes da compactacao.
    assert [t.channel_count for t in grupo.tracks] == [1, 1, 1, 2]
    assert [len(t.units) for t in grupo.tracks] == [1, 1, 1, 1]


def test_build_proposal_com_compact_preenche_a_contagem():
    session = _sessao()
    proposta = build_proposal(
        session,
        stereo="split",
        level="permissive",
        signals={"name", "compact"},
        media_meta=lambda _: None,
    )
    assert proposta.compactacao == {PRODUCTION: (4, 3)}
    grupo = proposta.groups[0]
    # Os monos vem antes do estereo e as units que tocam juntas ficam empilhadas.
    assert [t.channel_count for t in grupo.tracks] == [1, 1, 2]
    assert [len(t.units) for t in grupo.tracks] == [2, 1, 1]


def test_pipeline_relata_as_tracks_por_grupo():
    report = Report(strict=False)
    proposta = Proposal(
        stereo="pair",
        level="permissive",
        signals=("compact",),
        groups=(),
        left_tracks=(),
        compactacao={"PRODUCTION": (13, 9), "CAMERA": (6, 4)},
    )
    _relatar_compactacao(proposta, report)
    assert len(report.issues) == 1
    issue = report.issues[0]
    assert issue.code == "ORGANIZE_COMPACTED"
    assert issue.message == "tracks per group after compacting: PRODUCTION 13->9, CAMERA 6->4"
    assert issue.context == {"groups": {"PRODUCTION": [13, 9], "CAMERA": [6, 4]}}


def test_pipeline_nao_relata_sem_compactacao():
    report = Report(strict=False)
    proposta = Proposal(
        stereo="pair",
        level="permissive",
        signals=(),
        groups=(),
        left_tracks=(),
    )
    _relatar_compactacao(proposta, report)
    assert report.issues == []


def test_unidade_nao_aceita_em_track_de_outra_identidade_nao_conta_como_movida():
    from dataclasses import replace

    from aafton.organize_plan import DestinationTrack, ProposalGroup

    retida = replace(_unit((3,), 0, 10), accepted=False)
    aceita = _unit((3,), 20, 30)
    track = DestinationTrack(
        key="PRODUCTION:PROD 1",
        name="PROD 1",
        kind=PRODUCTION,
        channel_count=1,
        units=(retida, aceita),
        source_track="PROD 1",
        source_indices=(1,),
    )
    grupo = ProposalGroup("PRODUCTION", "PRODUCTION", "PROD", False, (track,))
    proposta = Proposal(
        stereo="pair", level="permissive", signals=(), groups=(grupo,), left_tracks=()
    )
    assert proposta.moved_origin_keys() == set(aceita.origin_keys)
