"""Testes dos envelopes de volume das tracks de destino da organizacao.

A organizacao move clips entre tracks do editor, e o volume efetivo de um clip
vem da track onde ele esta: `track.gain_linear * clip.gain_linear * curva(t)`.
Estes testes montam a proposta a mao, movem os clips e conferem que o ganho
efetivo de cada clip e o mesmo antes e depois de organizar, e que a envelope da
track de destino carrega o ganho de origem de cada clip.

A matriz `CASOS_CURVA` traz os casos de curva do plano (P4): cada linha diz as
tracks de origem, os pontos que a track de destino deve ter e o fader dela. O
teste parametrizado confere, em cada caso, que o ganho efetivo de cada clip
continua igual antes e depois de organizar.
"""

from __future__ import annotations

import pytest

from aafton.aaf_write_gain import _ganho_base, _ganho_em, _keyframes_automacao
from aafton.ir import ClipIR, FadeIR, SessionIR, TrackIR
from aafton.organize_apply import annotate_origin_keys, apply_proposal
from aafton.organize_envelope import curvas_de_origem, refazer_envelopes
from aafton.organize_plan import DestinationTrack, Proposal, ProposalGroup
from aafton.organize_units import Unit, origin_keys_for_track
from aafton.report import Report
from aafton.timecode import TimecodeRate


def _sessao() -> SessionIR:
    """Sessao de teste, sem midia: a organizacao so precisa dos clips."""
    return SessionIR(
        name="envelopes",
        sample_rate=48000,
        timecode_rate=TimecodeRate(24, 1),
        start_timecode_samples=0,
    )


def _track(
    session: SessionIR,
    source_index: int,
    nome: str,
    trechos: list[tuple[int, int]],
    *,
    ganho: float = 1.0,
    pan: float = 0.0,
    envelope: list[tuple[int, float]] | None = None,
    pan_points: list[tuple[int, float]] | None = None,
    muted: bool = False,
) -> TrackIR:
    """Track do editor com um clip por trecho dado."""
    track_id = f"trk_{source_index}"
    clips = []
    for pos, (inicio, fim) in enumerate(trechos):
        clips.append(
            ClipIR(
                id=f"c_{source_index}_{pos}",
                track_id=track_id,
                channel_index=0,
                event_index=pos,
                name=f"Take {pos + 1}",
                media_id=f"m_c_{source_index}_{pos}",
                timeline_start_samples=inicio,
                timeline_end_samples=fim,
            )
        )
    track = TrackIR(
        id=track_id,
        source_index=source_index,
        name=nome,
        kind="audio",
        channel_count=1,
        clips=clips,
        gain_linear=ganho,
        pan=pan,
        muted=muted,
    )
    if envelope is not None:
        track.gain_points = list(envelope)
    if pan_points is not None:
        track.pan_points = list(pan_points)
    session.tracks.append(track)
    return track


def _unidades(track: TrackIR, clips: list[ClipIR]) -> tuple[Unit, ...]:
    """As unidades de movimento dos clips escolhidos da track."""
    chaves = origin_keys_for_track(track)
    unidades = []
    for clip in clips:
        unidades.append(
            Unit(
                origin_keys=(chaves[id(clip)],),
                source_tracks=(track.source_index,),
                source_label=track.name,
                channel_count=track.channel_count,
                start_samples=clip.timeline_start_samples,
                end_samples=clip.timeline_end_samples,
                name=clip.name,
                media_filename=None,
                kind="PRODUCTION",
                score=1,
                conflict=False,
                reasons=(),
                accepted=True,
            )
        )
    return tuple(unidades)


def _unidade_perdida() -> Unit:
    """Uma unidade cujas chaves nao existem na sessao."""
    return Unit(
        origin_keys=((99, 0),),
        source_tracks=(99,),
        source_label="Audio 99",
        channel_count=1,
        start_samples=0,
        end_samples=100,
        name="Take 99",
        media_filename=None,
        kind="PRODUCTION",
        score=1,
        conflict=False,
        reasons=(),
        accepted=True,
    )


def _destino(
    nome: str,
    unidades: tuple[Unit, ...],
    *,
    source_track: str | None = None,
    source_indices: tuple[int, ...] = (),
) -> DestinationTrack:
    """A track de destino proposta."""
    return DestinationTrack(
        key=f"PRODUCTION:{nome}",
        name=nome,
        kind="PRODUCTION",
        channel_count=1,
        units=unidades,
        source_track=source_track,
        source_indices=source_indices,
    )


def _proposta(*destinos: DestinationTrack) -> Proposal:
    """A proposta com as tracks de destino dadas."""
    grupo = ProposalGroup("PRODUCTION", "PRODUCTION", "PROD", False, tuple(destinos))
    return Proposal(
        stereo="split",
        level="permissive",
        signals=("file",),
        groups=(grupo,),
        left_tracks=(),
    )


def _origens(session: SessionIR, origens: list[tuple]) -> list[TrackIR]:
    """Monta as tracks de origem e devolve as unidades de movimento delas."""
    tracks = []
    for indice, (nome, trechos, opts) in enumerate(origens):
        tracks.append(_track(session, indice, nome, trechos, **opts))
    unidades: tuple[Unit, ...] = ()
    for track in tracks:
        unidades += _unidades(track, track.clips)
    return tracks, unidades


def _amostras(clip: ClipIR) -> list[int]:
    """As amostras de dentro do clip conferidas no teste."""
    inicio = clip.timeline_start_samples
    dur = clip.timeline_end_samples - inicio
    if dur <= 0:
        return [inicio]
    return [
        inicio,
        inicio + dur // 4,
        inicio + dur // 2,
        inicio + (3 * dur) // 4,
        inicio + dur - 1,
    ]


def _ganhos(session: SessionIR) -> dict[int, list[tuple[int, float]]]:
    """O ganho efetivo de cada clip, nas amostras de dentro do clip."""
    tabela: dict[int, list[tuple[int, float]]] = {}
    for track in session.tracks:
        for clip in track.clips:
            pontos: list[tuple[int, float]] = []
            for amostra in _amostras(clip):
                curva = _ganho_em(track.gain_points, amostra)
                pontos.append((amostra, track.gain_linear * clip.gain_linear * curva))
            tabela[id(clip)] = pontos
    return tabela


def _ganho_de(ganhos: dict[int, list[tuple[int, float]]], clip: ClipIR, amostra: int) -> float:
    """O ganho efetivo medido na amostra de um clip."""
    for t, valor in ganhos[id(clip)]:
        if t == amostra:
            return valor
    raise AssertionError(f"a amostra {amostra} do clip {clip.name!r} nao foi medida")


def _confere_ganhos(
    antes: dict[int, list[tuple[int, float]]],
    depois: dict[int, list[tuple[int, float]]],
) -> None:
    """Cada clip mantem o ganho efetivo que tinha antes de organizar."""
    assert set(antes) == set(depois)
    for clip_id, pontos in antes.items():
        for (amostra, esperado), (_, obtido) in zip(pontos, depois[clip_id]):
            mensagem = f"o ganho mudou na amostra {amostra}: {esperado} -> {obtido}"
            assert obtido == pytest.approx(esperado, abs=1e-9), mensagem


def _organizar(
    session: SessionIR,
    proposta: Proposal,
    report: Report | None = None,
) -> tuple[dict[int, list[tuple[int, float]]], dict[int, list[tuple[int, float]]]]:
    """Organiza a sessao e devolve os ganhos de antes e de depois."""
    antes = _ganhos(session)
    annotate_origin_keys(session)
    apply_proposal(session, proposta, colors=True, report=report)
    return antes, _ganhos(session)


# (id, tracks de origem, pontos da track de destino, fader da track de destino).
CASOS_CURVA = [
    (
        "envelope que varia dentro do clip",
        [("Audio 1", [(0, 1000)], {"envelope": [(0, 1.0), (500, 0.5)]})],
        [(-1, 1.0), (0, 1.0), (500, 0.5), (1000, 0.5)],
        1.0,
    ),
    (
        "envelope com fader diferente de um",
        [
            (
                "Audio 1",
                [(0, 1000)],
                {"ganho": 0.5, "envelope": [(0, 1.0), (500, 0.5)]},
            )
        ],
        [(-1, 0.5), (0, 0.5), (500, 0.25), (1000, 0.25)],
        1.0,
    ),
    (
        "clip que comeca antes do primeiro ponto da origem",
        [("Audio 1", [(0, 1000)], {"envelope": [(500, 0.25)]})],
        [(-1, 0.25), (0, 0.25), (500, 0.25), (1000, 0.25)],
        1.0,
    ),
    (
        "dois clips de origens diferentes na mesma track",
        [
            ("Audio 1", [(0, 500)], {"envelope": [(0, 1.0), (250, 0.5)]}),
            ("Audio 2", [(500, 1000)], {"envelope": [(500, 0.5), (750, 1.0)]}),
        ],
        [
            (-1, 1.0),
            (0, 1.0),
            (250, 0.5),
            (499, 0.5),
            (500, 0.5),
            (750, 1.0),
            (1000, 1.0),
        ],
        1.0,
    ),
    (
        "clips encostados viram degraus",
        [
            ("Audio 1", [(0, 500)], {"envelope": [(0, 1.0), (250, 0.5)]}),
            ("Audio 2", [(500, 1000)], {"envelope": [(500, 0.8)]}),
        ],
        [(-1, 1.0), (0, 1.0), (250, 0.5), (499, 0.5), (500, 0.8), (1000, 0.8)],
        1.0,
    ),
    (
        "origens sem envelope com o mesmo fader",
        [
            ("Audio 1", [(0, 500)], {"ganho": 0.8}),
            ("Audio 2", [(500, 1000)], {"ganho": 0.8}),
        ],
        [],
        0.8,
    ),
    (
        "origens sem envelope com faders diferentes",
        [
            ("Audio 1", [(0, 500)], {"ganho": 0.5}),
            ("Audio 2", [(500, 1000)], {"ganho": 0.8}),
        ],
        [(-1, 0.5), (0, 0.5), (499, 0.5), (500, 0.8), (1000, 0.8)],
        1.0,
    ),
    (
        "par L/R com envelope na origem",
        [
            ("Audio 1_L", [(0, 1000)], {"envelope": [(0, 1.0), (500, 0.5)]}),
            ("Audio 1_R", [(0, 1000)], {"envelope": [(0, 1.0), (500, 0.5)]}),
        ],
        [(-1, 1.0), (0, 1.0), (500, 0.5), (1000, 0.5)],
        1.0,
    ),
]


@pytest.mark.parametrize(
    "nome,origens,pontos,fader",
    CASOS_CURVA,
    ids=[caso[0] for caso in CASOS_CURVA],
)
def test_curva_da_track_de_destino(nome, origens, pontos, fader) -> None:
    """A track de destino leva o ganho de origem de cada clip, e o fader certo."""
    session = _sessao()
    _, unidades = _origens(session, origens)
    antes, depois = _organizar(session, _proposta(_destino("PROD 1", unidades)))
    _confere_ganhos(antes, depois)
    alvo = session.tracks[0]
    assert alvo.gain_linear == fader
    assert alvo.gain_points == pontos


def test_curvas_de_origem_guarda_o_fader_e_a_curva_de_cada_clip() -> None:
    """Cada clip guarda a curva e o fader da track onde ele estava."""
    session = _sessao()
    origem = _track(
        session,
        0,
        "Audio 1",
        [(0, 500)],
        ganho=0.7,
        pan=0.25,
        envelope=[(0, 1.0), (250, 0.5)],
        pan_points=[(0, 0.25)],
    )
    curvas = curvas_de_origem(session)
    curva = curvas[id(origem.clips[0])]
    assert curva.gain_points == [(0, 1.0), (250, 0.5)]
    assert curva.gain_points is not origem.gain_points
    assert curva.gain_linear == 0.7
    assert curva.pan == 0.25
    assert curva.tem_pan_points is True


def test_sobreposicao_com_ganhos_diferentes_avisa_e_a_curva_seguinte_vale() -> None:
    """Do inicio do clip seguinte vale a curva dele, e o relatorio avisa."""
    session = _sessao()
    tracks, unidades = _origens(
        session,
        [
            ("Audio 1", [(0, 1000)], {"envelope": [(0, 1.0)]}),
            ("Audio 2", [(500, 1500)], {"envelope": [(500, 0.5)]}),
        ],
    )
    clip_a = tracks[0].clips[0]
    clip_b = tracks[1].clips[0]
    report = Report(strict=False)
    antes, depois = _organizar(session, _proposta(_destino("PROD 1", unidades)), report)
    issues = [i for i in report.issues if i.code == "ORGANIZE_ENVELOPE_OVERLAP"]
    assert len(issues) == 1
    assert issues[0].context == {
        "track": "PROD 1",
        "clips": ["Take 1", "Take 1"],
        "start": 500,
        "end": 1000,
    }
    # O corpo do primeiro clip ate a sobreposicao continua igual.
    assert _ganho_de(depois, clip_a, 250) == pytest.approx(_ganho_de(antes, clip_a, 250))
    # Do inicio do clip seguinte vale a curva dele.
    assert _ganho_de(depois, clip_a, 500) == pytest.approx(0.5)
    # O clip seguinte nao muda de ganho.
    for amostra, esperado in antes[id(clip_b)]:
        _, obtido = next((t, v) for t, v in depois[id(clip_b)] if t == amostra)
        assert obtido == pytest.approx(esperado, abs=1e-9)


def test_sobreposicao_com_curvas_que_diferem_no_meio_avisa() -> None:
    """Duas origens que cruzam nas pontas da sobreposicao tambem avisam.

    As duas curvas dao o mesmo ganho no inicio e no fim do trecho e diferem
    no meio: as tres amostras do meio nao bastavam para ver a diferenca.
    """
    session = _sessao()
    _, unidades = _origens(
        session,
        [
            ("Audio 1", [(0, 1000)], {"envelope": [(0, 1.0), (1000, 0.5)]}),
            (
                "Audio 2",
                [(500, 1500)],
                {"envelope": [(500, 0.75), (600, 0.9), (750, 0.625), (1000, 0.5)]},
            ),
        ],
    )
    report = Report(strict=False)
    _organizar(session, _proposta(_destino("PROD 1", unidades)), report)
    issues = [i for i in report.issues if i.code == "ORGANIZE_ENVELOPE_OVERLAP"]
    assert len(issues) == 1
    assert issues[0].context["start"] == 500
    assert issues[0].context["end"] == 1000


def test_home_do_editor_que_recebe_clips_de_outra_track() -> None:
    """A curva da home e trocada pela curva com o ganho de origem de cada clip."""
    session = _sessao()
    _track(session, 0, "Audio 1", [(0, 500)], envelope=[(0, 1.0), (250, 0.5)])
    outra = _track(session, 1, "Audio 2", [(600, 1000)], envelope=[(600, 0.25)])
    destino = _destino(
        "PROD 1",
        _unidades(outra, outra.clips),
        source_track="Audio 1",
        source_indices=(0,),
    )
    antes, depois = _organizar(session, _proposta(destino))
    _confere_ganhos(antes, depois)
    alvo = session.tracks[0]
    assert alvo.name == "PROD 1"
    assert [c.name for c in alvo.clips] == ["Take 1", "Take 1"]
    assert alvo.gain_linear == 1.0
    assert alvo.gain_points == [
        (-1, 1.0),
        (0, 1.0),
        (250, 0.5),
        (500, 0.5),
        (599, 0.5),
        (600, 0.25),
        (1000, 0.25),
    ]


def test_home_com_clip_de_unidade_nao_encontrada_ficou_com_o_ganho_dele() -> None:
    """O clip que ficou na home mantem o ganho que ele tinha."""
    session = _sessao()
    home = _track(
        session,
        0,
        "Audio 1",
        [(0, 500), (600, 1000)],
        envelope=[(0, 1.0), (250, 0.5)],
    )
    unidades = _unidades(home, home.clips[:1]) + (_unidade_perdida(),)
    destino = _destino(
        "PROD 1",
        unidades,
        source_track="Audio 1",
        source_indices=(0,),
    )
    antes, depois = _organizar(session, _proposta(destino))
    _confere_ganhos(antes, depois)
    alvo = session.tracks[0]
    assert [c.name for c in alvo.clips] == ["Take 1", "Take 2"]
    assert alvo.gain_points == [
        (-1, 1.0),
        (0, 1.0),
        (250, 0.5),
        (500, 0.5),
        (599, 0.5),
        (600, 0.5),
        (1000, 0.5),
    ]


def test_pan_igual_das_origens_fica_no_destino_sem_aviso() -> None:
    """O pan igual das origens fica na track de destino, sem envelope de pan."""
    session = _sessao()
    _, unidades = _origens(
        session,
        [
            ("Audio 1", [(0, 500)], {"pan": 0.3}),
            ("Audio 2", [(500, 1000)], {"pan": 0.3}),
        ],
    )
    report = Report(strict=False)
    _organizar(session, _proposta(_destino("PROD 1", unidades)), report)
    alvo = session.tracks[0]
    assert alvo.pan == 0.3
    assert alvo.pan_points == []
    assert not [i for i in report.issues if i.code == "ORGANIZE_PAN_VARIES"]


def test_pan_diferente_das_origens_avisa_e_fica_com_o_do_primeiro_clip() -> None:
    """Pan diferente ou envelope de pan na origem: a track fica com um pan so."""
    session = _sessao()
    _, unidades = _origens(
        session,
        [
            ("Audio 1", [(0, 500)], {"pan": 0.3}),
            ("Audio 2", [(500, 1000)], {"pan": -0.4}),
        ],
    )
    report = Report(strict=False)
    _organizar(session, _proposta(_destino("PROD 1", unidades)), report)
    alvo = session.tracks[0]
    assert alvo.pan == 0.3
    assert alvo.pan_points == []
    issues = [i for i in report.issues if i.code == "ORGANIZE_PAN_VARIES"]
    assert len(issues) == 1
    assert issues[0].severity == "warning"
    assert issues[0].context == {"track": "PROD 1"}


def test_origem_com_envelope_de_pan_avisa() -> None:
    """A origem com envelope de pan nao cabe no Live: o relatorio avisa."""
    session = _sessao()
    _, unidades = _origens(
        session,
        [("Audio 1", [(0, 500)], {"pan_points": [(0, 0.1), (500, 0.9)]})],
    )
    report = Report(strict=False)
    _organizar(session, _proposta(_destino("PROD 1", unidades)), report)
    alvo = session.tracks[0]
    assert alvo.pan_points == []
    issues = [i for i in report.issues if i.code == "ORGANIZE_PAN_VARIES"]
    assert len(issues) == 1


def test_clip_com_fade_out_tem_o_ponto_de_fim_no_fim_do_clip() -> None:
    """O ponto de fim do segmento cai exatamente no fim do clip."""
    session = _sessao()
    origem = _track(session, 0, "Audio 1", [(0, 1000)], envelope=[(0, 1.0), (500, 0.5)])
    origem.clips[0].fade_out = FadeIR(kind="out", duration_samples=100)
    fim_do_clip = origem.clips[0].timeline_end_samples
    destino = _destino("PROD 1", _unidades(origem, origem.clips))
    antes, depois = _organizar(session, _proposta(destino))
    _confere_ganhos(antes, depois)
    alvo = session.tracks[0]
    assert alvo.gain_points[-1] == (1000, 0.5)
    assert alvo.gain_points[-1][0] == fim_do_clip


def test_destino_sem_clip_nao_ganha_pontos() -> None:
    """A track de destino sem clip nao ganha envelope nem fader novo."""
    session = _sessao()
    destino = _track(session, 0, "Audio 1", [], ganho=0.6, envelope=[(0, 1.0)])
    refazer_envelopes(destino, curvas_de_origem(session), Report(strict=False))
    assert destino.gain_points == []
    assert destino.gain_linear == 0.6


def test_destino_mudo_continua_mudo() -> None:
    """A organizacao nao mexe no mudo da track de destino."""
    session = _sessao()
    origem = _track(session, 0, "Audio 1", [(0, 500)], envelope=[(0, 1.0)])
    destino = _track(session, 1, "Audio 2", [], muted=True)
    refazer_envelopes(origem, curvas_de_origem(session), Report(strict=False))
    assert origem.muted is False
    assert destino.muted is True


def test_ponto_da_origem_depois_do_clip_nao_muda_o_ganho_de_outro_clip() -> None:
    """O ponto da origem que vem depois do clip nao entra no clip de outra origem.

    Os dois clips comecam juntos: o clip da origem com envelope e o clip da
    origem sem envelope. A envelope da origem e lisa no trecho do clip, para
    que a unica coisa que possa mudar o ganho do segundo clip sejam os pontos
    da origem que vem depois do fim do primeiro clip.
    """
    session = _sessao()
    tracks, unidades = _origens(
        session,
        [
            (
                "Audio 1",
                [(0, 1000)],
                {"envelope": [(0, 0.8), (1000, 0.8), (1500, 0.25), (1800, 0.1)]},
            ),
            ("Audio 2", [(0, 2000)], {"ganho": 0.8}),
        ],
    )
    clip_y = tracks[1].clips[0]
    antes, _ = _organizar(session, _proposta(_destino("PROD 1", unidades)))
    alvo = session.tracks[0]
    for amostra, esperado in antes[id(clip_y)]:
        assert esperado == pytest.approx(0.8, abs=1e-9), f"wrong gain before at {amostra}"
    for amostra in range(clip_y.timeline_start_samples, clip_y.timeline_end_samples):
        ganho = alvo.gain_linear * clip_y.gain_linear * _ganho_em(alvo.gain_points, amostra)
        assert ganho == pytest.approx(0.8, abs=1e-9), f"the gain changed at sample {amostra}"
    # A curva do destino nao tem ponto nenhum da origem depois do fim do clip.
    assert alvo.gain_points == [(-1, 0.8), (0, 0.8), (1000, 0.8), (2000, 0.8)]


def _curva_do_destino_de_origem(envelope: list[tuple[int, float]]) -> tuple[list, list[float]]:
    """A curva do destino e o ganho do clip em cada amostra, com a origem dada."""
    session = _sessao()
    _, unidades = _origens(session, [("Audio 1", [(250, 700)], {"envelope": envelope})])
    _organizar(session, _proposta(_destino("PROD 1", unidades)))
    alvo = session.tracks[0]
    ganhos = [
        alvo.gain_linear * _ganho_em(alvo.gain_points, amostra) for amostra in range(250, 700)
    ]
    return list(alvo.gain_points), ganhos


def test_origem_com_pontos_desordenados_da_o_ganho_da_lista_ordenada() -> None:
    """Os pontos da origem fora de ordem dao o mesmo ganho da lista ordenada.

    A envelope do AAF e uma curva so: os pontos podem vir fora de ordem e
    podem se repetir na mesma amostra. A curva da origem e lida ordenada, com a
    ordem estavel dos pontos que caem na mesma amostra.
    """
    pontos = [(1000, 0.75), (500, 0.5), (0, 1.0), (500, 0.75), (400, 0.5)]
    ordenado = sorted(pontos, key=lambda p: p[0])
    curva_fora, ganhos_fora = _curva_do_destino_de_origem(pontos)
    curva_ordenada, ganhos_ordenados = _curva_do_destino_de_origem(ordenado)
    for amostra, (fora, dentro) in enumerate(zip(ganhos_fora, ganhos_ordenados), start=250):
        assert fora == pytest.approx(dentro, abs=1e-9), f"the gain changed at sample {amostra}"
    assert curva_fora == curva_ordenada
    # O ponto repetido na mesma amostra fica com o valor de depois.
    assert [amostra for amostra, _ in curva_ordenada] == [249, 250, 400, 500, 700]
    assert [valor for _, valor in curva_ordenada] == pytest.approx(
        [0.68875, 0.6875, 0.5, 0.75, 0.75]
    )


def test_clips_encostados_com_ganhos_diferentes_nao_avisa_sobreposicao() -> None:
    """Clips que so se encostam nao sao sobreposicao."""
    session = _sessao()
    _, unidades = _origens(
        session,
        [
            ("Audio 1", [(0, 1000)], {"envelope": [(0, 1.0)]}),
            ("Audio 2", [(1000, 2000)], {"envelope": [(1000, 0.5)]}),
        ],
    )
    report = Report(strict=False)
    _organizar(session, _proposta(_destino("PROD 1", unidades)), report)
    assert not [i for i in report.issues if i.code == "ORGANIZE_ENVELOPE_OVERLAP"]


def test_comeco_do_clip_usa_o_ponto_de_depois_quando_a_origem_empata() -> None:
    """Dois pontos da origem na mesma amostra: o comeco do clip vale o de depois.

    A curva da origem e uma curva so: o ponto que vem antes e o fim do clip que
    termina ali e o ponto que vem depois e o comeco do clip que comeca ali.
    """
    session = _sessao()
    tracks, unidades = _origens(
        session,
        [
            (
                "Audio 1",
                [(0, 1000), (1000, 2000)],
                {"envelope": [(0, 1.0), (1000, 1.0), (1000, 0.5), (2000, 0.5)]},
            ),
            ("Audio 2", [(3000, 4000)], {"envelope": [(3000, 0.25)]}),
        ],
    )
    primeiro, segundo = tracks[0].clips
    _organizar(session, _proposta(_destino("PROD 1", unidades)))
    alvo = session.tracks[0]
    for clip, esperado in ((primeiro, 1.0), (segundo, 0.5)):
        for amostra in range(clip.timeline_start_samples, clip.timeline_end_samples):
            ganho = alvo.gain_linear * clip.gain_linear * _ganho_em(alvo.gain_points, amostra)
            assert ganho == pytest.approx(esperado, abs=1e-9), f"wrong gain at {amostra}"


def _ganho_dos_keyframes(keyframes: list[tuple[float, float]], posicao: float) -> float:
    """O ganho dos keyframes do AAF na posicao normalizada."""
    if posicao <= keyframes[0][0]:
        return keyframes[0][1]
    for (t0, v0), (t1, v1) in zip(keyframes, keyframes[1:]):
        if t0 <= posicao <= t1:
            if t1 == t0:
                return v1
            return v0 + (v1 - v0) * ((posicao - t0) / (t1 - t0))
    return keyframes[-1][1]


def test_ida_e_volta_ao_aaf_da_sessao_organizada() -> None:
    """Os keyframes do AAF dao o mesmo ganho que a sessao sem organizar."""
    session = _sessao()
    tracks, unidades = _origens(
        session,
        [
            ("Audio 1", [(0, 1000)], {"ganho": 0.7, "envelope": [(0, 1.0), (500, 0.5)]}),
            ("Audio 2", [(1100, 1600)], {"envelope": [(1100, 0.25)]}),
        ],
    )
    antes, depois = _organizar(session, _proposta(_destino("PROD 1", unidades)))
    _confere_ganhos(antes, depois)
    for track in session.tracks:
        for clip in track.clips:
            keyframes = _keyframes_automacao(
                track.gain_points,
                _ganho_base(track, clip),
                clip.timeline_start_samples,
                clip.timeline_end_samples,
                clip.duration_samples,
            )
            for amostra, esperado in antes[id(clip)]:
                posicao = (amostra - clip.timeline_start_samples) / clip.duration_samples
                obtido = _ganho_dos_keyframes(keyframes, posicao)
                assert obtido == pytest.approx(esperado, abs=1e-9)


def test_clips_encostados_de_origens_diferentes_ficam_com_o_ganho_deles() -> None:
    """Dois clips que se encostam, de origens diferentes, cada um com o seu ganho.

    A curva da track de destino da um degrau no ponto onde os clips se
    encostam: o keyframe de fim do primeiro clip nao pode pegar o ganho do
    clip seguinte.
    """
    session = _sessao()
    _, unidades = _origens(
        session,
        [
            ("Audio 1", [(0, 1000)], {"envelope": [(0, 1.0)]}),
            ("Audio 2", [(1000, 2000)], {"envelope": [(1000, 0.5)]}),
        ],
    )
    antes, depois = _organizar(session, _proposta(_destino("PROD 1", unidades)))
    _confere_ganhos(antes, depois)
    for track in session.tracks:
        for clip in track.clips:
            keyframes = _keyframes_automacao(
                track.gain_points,
                _ganho_base(track, clip),
                clip.timeline_start_samples,
                clip.timeline_end_samples,
                clip.duration_samples,
            )
            esperado = antes[id(clip)][-1][1]
            assert keyframes[-1][1] == pytest.approx(esperado, abs=1e-9), (
                f"the last keyframe of {clip.name!r} took the gain of the next clip"
            )
