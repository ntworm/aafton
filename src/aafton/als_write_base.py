"""Parte de als_write: definicoes movidas sem mudanca (fase 1)."""

import xml.etree.ElementTree as ET

from dataclasses import dataclass

from fractions import Fraction

import math


from .als_xml import (
    renumber_siblings,
    set_value,
)


from .report import Report

from .timecode import TimecodeRate


VOLUME_GAIN_MIN = 0.0003162277571  # -70 dB


VOLUME_GAIN_MAX = 1.99526238  # +6 dB


SENTINEL_TIME = -63072000  # Sentinel antes do primeiro ponto


class AnchorError(ValueError):
    pass


# Lado em que o valor escrito pode errar quando o double exato nao existe.
# Medido no Live 12.4 (calibracao P03, docs/AUDIO_AUDIT.md): o inicio do clip no
# arranjo sobe para a amostra seguinte (1.000146 beat = 48007.008 samples tocou
# em 48008), e o offset na fonte desce para a anterior (617 gravado como
# 616.992 samples tambem atrasou uma amostra). Posicao e comprimento na
# timeline erram para baixo; posicao na fonte erra para cima. No caso comum o
# valor cai exatamente na amostra e o lado nao importa.
TIMELINE = "timeline"


SOURCE = "source"


# SaveAs humanos em 60 e 97 BPM, Live 12.4.15b6: a posicao e o comprimento
# dos clips usam esta grade em beats. Evidencia textual independente em
# tests/fixtures/als/live_clip_clock_profile.json; renders em docs/AUDIO_AUDIT.md.
LIVE_CLIP_TICKS_PER_BEAT = 3843840


def _live_samples(beats: float, sample_rate: int, bpm: float) -> tuple[float, ...]:
    """Conversao double de beats para samples, primeiro na ordem literal.

    Nao inclui a quantizacao do relogio de clips. As outras ordens de avaliacao
    so desempatam candidatos de precisao; a literal decide.
    """
    return (
        beats * 60.0 / bpm * sample_rate,
        beats * (60.0 / bpm) * sample_rate,
        beats / (bpm / 60.0) * sample_rate,
    )


def samples_to_beats(samples: int, sample_rate: int, bpm: float, side: str = TIMELINE) -> float:
    """Double cuja formula de volta cai na amostra inteira pedida.

    Comeca no double mais proximo do racional exato e examina os vizinhos. Prefere
    o que a formula literal leva exatamente a ``samples``; sem ele, o mais
    proximo do lado permitido (abaixo na timeline, acima na fonte). O erro fica
    abaixo de 1e-6 sample para posicoes de ate 2**31 samples.
    """
    if side not in (TIMELINE, SOURCE):
        raise ValueError(f"unknown rounding side: {side!r}")
    if samples == 0:
        return 0.0
    exact = Fraction(samples) * Fraction(bpm) / (60 * sample_rate)
    nearest = float(exact)
    candidates = [nearest]
    up = down = nearest
    for _ in range(16):
        up, down = math.nextafter(up, math.inf), math.nextafter(down, -math.inf)
        candidates += [up, down]

    def allowed(value: float) -> bool:
        return value <= samples if side == TIMELINE else value >= samples

    def score(beats: float) -> tuple:
        back = _live_samples(beats, sample_rate, bpm)
        return (
            back[0] == samples,
            all(allowed(v) for v in back),
            sum(v == samples for v in back),
            -abs(back[0] - samples),
            -abs(Fraction(beats) - exact),
        )

    usable = [b for b in candidates if allowed(_live_samples(b, sample_rate, bpm)[0])]
    if not usable:
        raise ValueError(
            f"no double near {samples} samples comes back on the {side!r} side; "
            f"position too large for sample precision"
        )
    return max(usable, key=score)


def source_seconds(samples: int, sample_rate: int) -> float:
    """Segundos do arquivo para os marcadores de loop de um clip sem warp.

    Num clip de audio sem warp o Live grava LoopStart, LoopEnd e HiddenLoopEnd
    em segundos do arquivo, em qualquer tempo do set (medido em 15/11/2026).
    Segundo e o beat a 60 BPM, entao a mesma busca de double serve, do lado da
    fonte.
    """
    return samples_to_beats(samples, sample_rate, 60.0, SOURCE)


def to_beats(
    abs_samples: int,
    live_origin_samples: int | None,
    sample_rate: int,
    bpm: float,
    side: str = TIMELINE,
) -> float:
    origin = 0 if live_origin_samples is None else live_origin_samples
    live_samples = abs_samples - origin
    if live_samples < 0:
        raise AnchorError(
            f"the position {abs_samples} falls before the origin {origin}; "
            f"Live has no negative time. Move the live-origin forward."
        )
    return samples_to_beats(live_samples, sample_rate, bpm, side)


def _clip_times(start: int, duration: int, options: "WriteOptions") -> tuple[float, float]:
    """Inicio e comprimento nao ultrapassam a amostra apos a grade do Live.

    O Live arredonda inicio e comprimento separadamente para ticks e toca na
    primeira amostra a partir da posicao resultante. Escolher ticks anteriores
    preserva os inteiros de inicio/fim sem modificar o audio. Offset, fades,
    envelopes, marcadores e video continuam usando a precisao double anterior.
    """
    origin = 0 if options.live_origin_samples is None else options.live_origin_samples
    relative_start = start - origin
    if relative_start < 0:
        raise AnchorError(
            f"the position {start} falls before the origin {origin}; "
            f"Live has no negative time. Move the live-origin forward."
        )
    ticks = LIVE_CLIP_TICKS_PER_BEAT
    scale = Fraction(options.bpm) * ticks / (60 * options.sample_rate)
    start_tick = math.floor(relative_start * scale)
    length_tick = math.floor(duration * scale)

    def back(beats):
        return _live_samples(beats, options.sample_rate, options.bpm)

    start_beats = start_tick / ticks
    # Um tick exatamente na amostra tambem pode voltar um ULP depois dela.
    if any(v > relative_start for v in back(start_beats)):
        start_tick -= 1
        start_beats = start_tick / ticks
    end_beats = (start_tick + length_tick) / ticks
    normalized_end = start_beats + round((end_beats - start_beats) * ticks) / ticks
    if any(v > relative_start + duration for v in back(normalized_end)):
        length_tick -= 1
        end_beats = (start_tick + length_tick) / ticks
        normalized_end = start_beats + round((end_beats - start_beats) * ticks) / ticks

    for beats, expected in (
        (start_beats, relative_start),
        (normalized_end, relative_start + duration),
    ):
        if any(math.ceil(v) != expected or round(v) != expected for v in back(beats)):
            raise ValueError("the Live clip grid does not preserve the sample at this rate/tempo")
    if round((normalized_end - start_beats) * 60.0 / options.bpm * options.sample_rate) != duration:
        raise ValueError("the Live clip grid does not preserve the length at this rate/tempo")
    return start_beats, end_beats


@dataclass(frozen=True)
class WriteOptions:
    sample_rate: int
    bpm: float = 60.0
    live_origin_samples: int | None = None
    permissive: bool = False
    fades: str = "absorb"
    stereo: str = "pair"
    groups: str = "none"


def clear_tempo_automation(root: ET.Element, report: Report) -> None:
    """Remove o envelope de automacao de tempo herdado do template.

    O BPM manual da MainTrack so vale quando nao ha envelope de tempo. O
    template traz um, entao o set gerado abria a 134.999 BPM mesmo com
    Manual=60 gravado, e toda posicao de clip saia deslocada.
    """
    alvos = {
        no.get("Id")
        for main in root.iter("MainTrack")
        for tempo in main.iter("Tempo")
        for no in tempo.iter("AutomationTarget")
        if no.get("Id")
    }
    if not alvos:
        return
    for envelopes in root.iter("Envelopes"):
        for envelope in list(envelopes):
            ponteiro = envelope.find("EnvelopeTarget/PointeeId")
            if ponteiro is not None and ponteiro.get("Value") in alvos:
                envelopes.remove(envelope)
                report.info(
                    "TEMPO_AUTOMATION_DROPPED",
                    "the template's tempo automation envelope was removed so that "
                    "the fixed BPM of the conversion holds",
                    {"pointee_id": ponteiro.get("Value")},
                )
        renumber_siblings(envelopes, "AutomationEnvelope")


def detach_from_group(node: ET.Element) -> None:
    """Reaponta a saida de audio da track para a Main.

    Uma track que vivia dentro de um GroupTrack tem
    AudioOutputRouting/Target = "AudioOut/GroupTrack". Ao tirar a track do grupo
    e remover o grupo do documento, esse destino deixa de existir e o Live fecha
    com "A serious program error has occurred" ao carregar, sem dizer o motivo.
    Nenhuma checagem de Id pega isso, porque a referencia e por nome de destino,
    nao por identificador.
    """
    saida = node.find("DeviceChain/AudioOutputRouting")
    if saida is None:
        return
    for tag, valor in (
        ("Target", "AudioOut/Main"),
        ("UpperDisplayString", "Main"),
        ("LowerDisplayString", ""),
    ):
        alvo = saida.find(tag)
        if alvo is not None:
            alvo.set("Value", valor)


def attach_to_group(node: ET.Element, group_id: int) -> None:
    """Aponta a track para pertencer e rotear som para o grupo informado."""
    set_value(node, "TrackGroupId", group_id)
    saida = node.find("DeviceChain/AudioOutputRouting")
    if saida is None:
        return
    for tag, valor in (
        ("Target", "AudioOut/GroupTrack"),
        ("UpperDisplayString", "Group"),
        ("LowerDisplayString", ""),
    ):
        alvo = saida.find(tag)
        if alvo is not None:
            alvo.set("Value", valor)


def clear_warp(node: ET.Element) -> None:
    markers = node.find("WarpMarkers")
    if markers is not None:
        restantes = list(markers)
        for filho in restantes[1:]:
            markers.remove(filho)
        if restantes:
            restantes[0].set("SecTime", "0")
            restantes[0].set("BeatTime", "0")
    try:
        set_value(node, "MarkersGenerated", False)
    except KeyError:
        pass


def timecode_rate_to_smpte_format(rate: TimecodeRate | None) -> str:
    """Mapeia TimecodeRate para o formato SMPTE nativo do Live 12.

    Live 12 SmpteFormat:
    0: Bars / ms (ms_time)
    1: 24 fps (ou 23.976 fps)
    2: 25 fps
    3: 30 fps NDF
    4: 29.97 fps DF (drop_frame=True)
    5: 29.97 fps NDF (drop_frame=False)
    Fallback: 0
    """
    if rate is None:
        return "0"
    num, den = rate.num, rate.den
    if (num, den) == (24, 1):
        return "1"
    if (num, den) == (24000, 1001):
        return "1"
    if (num, den) == (25, 1):
        return "2"
    if (num, den) == (30, 1) and not rate.drop_frame:
        return "3"
    if (num, den) == (30000, 1001):
        if rate.drop_frame:
            return "4"
        return "5"
    return "0"
