"""Envelopes de volume das tracks de destino da organizacao.

A organizacao move clips entre tracks do editor, e o volume efetivo de um clip
vem da track onde ele esta: `track.gain_linear * clip.gain_linear * curva(t)`.
Quando o clip sai da track de origem, o fader e a envelope de automacao dela
ficam para tras e o clip muda de volume. Este modulo guarda a curva de cada
clip antes da mudanca e refaz a envelope da track de destino, com o ganho de
origem de cada clip, para o som nao mudar.

A convencao do IR (aaf_write_gain.py): o ganho efetivo de um clip e
`track.gain_linear * clip.gain_linear * curva(t)`, e a curva e lida com
`_ganho_em`, que segura o valor extremo fora dos pontos.

A curva da track de destino reproduz a curva da origem de cada clip: um degrau
no inicio de cada clip (o ponto anterior segura o valor de antes), os pontos da
origem dentro do trecho do clip e um ponto de fim no fim do clip, ou no inicio
do clip seguinte quando os dois se sobrepoem. Duas origens sem envelope e com o
mesmo fader nao precisam de curva: a track de destino fica so com o fader.

Os pontos de cada origem sao guardados ordenados por amostra, com a ordem
estavel quando dois pontos caem na mesma amostra: a envelope da origem e uma
curva so, entao ela e lida nessa ordem. Na curva de destino so entram os
pontos da origem que estao dentro do trecho do clip: um ponto da origem que
vem depois do clip nao pertence a nenhum clip da track de destino e nao pode
mudar o ganho de outro clip.

Quando dois pontos da origem caem na mesma amostra, o ponto que vem antes e o
fim do clip que termina ali e o ponto que vem depois e o comeco do clip que
comeca ali: no comeco do clip vale o ponto de depois
(ganho_de_origem_da_direita) e no fim vale o de antes (ganho_de_origem).
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, field

from .aaf_write_gain import _ganho_em
from .ir import ClipIR, SessionIR, TrackIR
from .organize_units import sort_track_clips
from .report import Report

# Diferenca menor que isto na conta como ganho diferente.
EPSILON = 1e-9


@dataclass(frozen=True)
class CurvaDeOrigem:
    """O fader e a curva da track onde o clip estava antes de organizar."""

    gain_points: list[tuple[int, float]] = field(default_factory=list)
    gain_linear: float = 1.0
    pan: float = 0.0
    tem_pan_points: bool = False


@dataclass
class _Segmento:
    """Trecho da track de destino: clips com o mesmo inicio e o mesmo fim."""

    inicio: int
    fim: int
    clips: list[ClipIR]
    origens: list[CurvaDeOrigem]


def curvas_de_origem(session: SessionIR) -> dict[int, CurvaDeOrigem]:
    """Guarda o fader e a curva da track de cada clip, antes de qualquer mudanca.

    A chave e `id(clip)`: o clip e o mesmo objeto depois que ele muda de track.
    A lista de pontos e uma copia, nunca a lista da track: a curva da origem
    nao pode mudar por baixo da organizacao. A copia vem ordenada por amostra,
    com a ordem estavel dos pontos de uma mesma amostra: a envelope da origem
    e lida como uma curva, e a curva do AAF pode ter pontos fora de ordem e
    pontos repetidos na mesma amostra.
    """
    curvas: dict[int, CurvaDeOrigem] = {}
    for track in session.tracks:
        for clip in track.clips:
            curvas[id(clip)] = CurvaDeOrigem(
                gain_points=sorted(track.gain_points, key=lambda p: p[0]),
                gain_linear=track.gain_linear,
                pan=track.pan,
                tem_pan_points=bool(track.pan_points),
            )
    return curvas


def ganho_de_origem(origem: CurvaDeOrigem, amostra: int) -> float:
    """O ganho que a track de origem dava ao clip na amostra."""
    if not origem.gain_points:
        return origem.gain_linear
    return origem.gain_linear * _ganho_em(origem.gain_points, amostra)


def _valor_do_ultimo_ponto(pontos: list[tuple[int, float]], amostra: int) -> float | None:
    """O valor do ultimo ponto que cai na amostra; None quando nao ha ponto."""
    valor: float | None = None
    for ponto, ganho in pontos:
        if ponto == amostra:
            valor = ganho
    return valor


def ganho_de_origem_da_direita(origem: CurvaDeOrigem, amostra: int) -> float:
    """O ganho da origem na amostra, com o valor de depois quando ha empate.

    Com dois pontos na mesma amostra, o ponto de antes e o fim do clip que
    termina ali e o ponto de depois e o comeco do clip que comeca ali.
    """
    if not origem.gain_points:
        return origem.gain_linear
    valor = _valor_do_ultimo_ponto(origem.gain_points, amostra)
    if valor is None:
        return ganho_de_origem(origem, amostra)
    return origem.gain_linear * valor


def _origem(curvas: dict[int, CurvaDeOrigem], clip: ClipIR) -> CurvaDeOrigem:
    """A curva de origem do clip; uma curva neutra quando o clip nao tem."""
    origem = curvas.get(id(clip))
    if origem is None:
        return CurvaDeOrigem()
    return origem


def _segmentos(clips: list[ClipIR], origens: list[CurvaDeOrigem]) -> list[_Segmento]:
    """Agrupa os clips da track em segmentos; clips iguais formam um segmento so."""
    segmentos: list[_Segmento] = []
    for clip, origem in zip(clips, origens):
        mesmo = (
            segmentos
            and segmentos[-1].inicio == clip.timeline_start_samples
            and segmentos[-1].fim == clip.timeline_end_samples
        )
        if mesmo:
            segmentos[-1].clips.append(clip)
            segmentos[-1].origens.append(origem)
            continue
        segmentos.append(
            _Segmento(
                clip.timeline_start_samples,
                clip.timeline_end_samples,
                [clip],
                [origem],
            )
        )
    return segmentos


def _proximo_segmento(segmentos: list[_Segmento], pos: int) -> int | None:
    """O indice do segmento seguinte com inicio diferente; None no fim da track."""
    inicio = segmentos[pos].inicio
    for indice in range(pos + 1, len(segmentos)):
        if segmentos[indice].inicio != inicio:
            return indice
    return None


def _fim_do_segmento(seg: _Segmento, next_start: int | None) -> int | None:
    """A amostra do ponto de fim do segmento; None quando ela nao cabe."""
    if next_start is None or next_start > seg.fim:
        amostra = seg.fim
    else:
        amostra = next_start - 1
    return amostra if amostra >= seg.inicio else None


def _poe_ponto(ordenados: list[tuple[int, float]], amostra: int, valor: float) -> None:
    """Guarda o ponto na amostra; o ultimo clip que chega na amostra ganha."""
    pos = bisect_left(ordenados, (amostra,))
    if pos < len(ordenados) and ordenados[pos][0] == amostra:
        ordenados[pos] = (amostra, valor)
        return
    ordenados.insert(pos, (amostra, valor))


def _valor_antes(ordenados: list[tuple[int, float]], origem: CurvaDeOrigem, inicio: int) -> float:
    """O valor que a curva montada tem antes do inicio do segmento."""
    if ordenados:
        return _ganho_em(ordenados, inicio - 1)
    return ganho_de_origem(origem, inicio - 1)


def _pontos_do_segmento(
    ordenados: list[tuple[int, float]],
    seg: _Segmento,
    next_start: int | None,
    anterior: float,
) -> None:
    """Escreve os pontos de um segmento: o degrau do inicio, a curva e o fim.

    Os pontos da origem que entram na curva sao os que estao dentro do trecho
    do clip: um ponto da origem que vem depois do fim do clip nao pertence a
    nenhum clip da track de destino. O limite e o fim do clip, ou o inicio do
    clip seguinte quando esse vier antes (clipes sobrepostos).

    O degrau do inicio vale o ganho do comeco do clip (o ponto de depois quando
    a origem empata na amostra) e o ponto de fim vale o ganho do fim do clip.
    """
    limite = seg.fim if next_start is None else min(seg.fim, next_start)
    for origem in seg.origens:
        _poe_ponto(ordenados, seg.inicio - 1, anterior)
        _poe_ponto(ordenados, seg.inicio, ganho_de_origem_da_direita(origem, seg.inicio))
        for amostra, valor in origem.gain_points:
            if seg.inicio < amostra < limite:
                _poe_ponto(ordenados, amostra, origem.gain_linear * valor)
        fim = _fim_do_segmento(seg, next_start)
        if fim is not None:
            _poe_ponto(ordenados, fim, ganho_de_origem(origem, fim))


def _pontos_dentro(origens: tuple[CurvaDeOrigem, ...], inicio: int, fim: int) -> list[int]:
    """As amostras dos pontos das origens que caem dentro do trecho.

    As pontas do trecho ficam de fora: elas tem valor proprio, o do comeco do
    clip seguinte e o do fim do clip anterior.
    """
    amostras: list[int] = []
    for origem in origens:
        for amostra, _ in origem.gain_points:
            if inicio < amostra < fim and amostra not in amostras:
                amostras.append(amostra)
    return amostras


def _ganhos_diferentes(
    origem_a: CurvaDeOrigem, origem_b: CurvaDeOrigem, inicio: int, fim: int
) -> bool:
    """Diz se as duas origens dao ganhos diferentes no trecho.

    No inicio do trecho vale o ganho do comeco do clip seguinte (o ponto de
    depois quando a origem empata); no resto do trecho vale a curva.

    As duas curvas sao lineares entre os seus pontos: comparar as duas pontas
    do trecho e cada ponto de origem que cai dentro dele basta para saber se
    as curvas diferem em algum lugar do trecho.
    """
    ganho_a = ganho_de_origem_da_direita(origem_a, inicio)
    ganho_b = ganho_de_origem_da_direita(origem_b, inicio)
    if abs(ganho_a - ganho_b) > EPSILON:
        return True
    if abs(ganho_de_origem(origem_a, fim) - ganho_de_origem(origem_b, fim)) > EPSILON:
        return True
    for amostra in _pontos_dentro((origem_a, origem_b), inicio, fim):
        if abs(ganho_de_origem(origem_a, amostra) - ganho_de_origem(origem_b, amostra)) > EPSILON:
            return True
    return False


def _relatar_sobreposicao(
    track: TrackIR,
    seg: _Segmento,
    prox: _Segmento,
    next_start: int,
    report: Report | None,
) -> None:
    """Avisa que dois clips se sobrepoem e os ganhos de origem sao diferentes."""
    if report is None:
        return
    origem_a = seg.origens[0]
    origem_b = prox.origens[0]
    if not _ganhos_diferentes(origem_a, origem_b, next_start, seg.fim):
        return
    nome_a = seg.clips[0].name
    nome_b = prox.clips[0].name
    report.info(
        "ORGANIZE_ENVELOPE_OVERLAP",
        f"the clips {nome_a!r} and {nome_b!r} of the track {track.name!r} overlap "
        f"with different gain: from sample {next_start} to {seg.fim} the curve of "
        f"{nome_b!r} rules",
        {
            "track": track.name,
            "clips": [nome_a, nome_b],
            "start": next_start,
            "end": seg.fim,
        },
    )


def _curva_de_destino(
    segmentos: list[_Segmento],
    track: TrackIR,
    report: Report | None,
) -> list[tuple[int, float]]:
    """Monta a curva da track de destino e avisa as sobreposicoes.

    Dois clips que so se encostam (o fim de um e o inicio do outro) nao sao
    sobreposicao: so ha sobreposicao quando o clip seguinte comeca antes do
    fim do clip anterior.
    """
    ordenados: list[tuple[int, float]] = []
    for pos, seg in enumerate(segmentos):
        indice = _proximo_segmento(segmentos, pos)
        next_start = segmentos[indice].inicio if indice is not None else None
        if indice is not None and seg.fim > next_start:
            _relatar_sobreposicao(track, seg, segmentos[indice], next_start, report)
        anterior = _valor_antes(ordenados, seg.origens[0], seg.inicio)
        _pontos_do_segmento(ordenados, seg, next_start, anterior)
    return ordenados


def _fader_comum(origens: list[CurvaDeOrigem]) -> float | None:
    """O fader comum quando todas as origens nao tem envelope e tem o mesmo fader."""
    if any(origem.gain_points for origem in origens):
        return None
    faders = {origem.gain_linear for origem in origens}
    return faders.pop() if len(faders) == 1 else None


def _refazer_pan(
    track: TrackIR,
    origens: list[CurvaDeOrigem],
    report: Report | None,
) -> None:
    """O pan fixo da track de destino e o da origem do primeiro clip."""
    pans = {origem.pan for origem in origens}
    varia = len(pans) > 1 or any(origem.tem_pan_points for origem in origens)
    if varia and report is not None:
        report.warning(
            "ORGANIZE_PAN_VARIES",
            f"the track {track.name!r} keeps the pan of its first clip: the origins "
            "have different pan or a pan envelope of its own",
            {"track": track.name},
        )
    track.pan_points = []
    track.pan = origens[0].pan


def refazer_envelopes(
    track: TrackIR,
    curvas: dict[int, CurvaDeOrigem],
    report: Report | None = None,
) -> None:
    """Refaz a envelope de ganho e o pan da track de destino.

    A envelope da track de destino e trocada pela curva montada com o ganho de
    origem de cada clip: o ganho efetivo `track.gain_linear * clip.gain_linear *
    curva(t)` fica igual ao que era antes da organizacao. O pan fixo fica o da
    origem do primeiro clip e a envelope de pan fica vazia, porque o .als e o
    AAF nao guardam pan em envelope. O mudo da track nao muda.
    """
    clips = sort_track_clips(track.clips)
    if not clips:
        track.gain_points = []
        track.pan_points = []
        return
    origens = [_origem(curvas, clip) for clip in clips]
    _refazer_pan(track, origens, report)
    fader = _fader_comum(origens)
    if fader is not None:
        track.gain_points = []
        track.gain_linear = fader
        return
    segmentos = _segmentos(clips, origens)
    track.gain_linear = 1.0
    track.gain_points = _curva_de_destino(segmentos, track, report)
