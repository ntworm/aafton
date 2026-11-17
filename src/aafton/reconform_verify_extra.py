"""Conferencias extras do reconform: campos dos clips, locators, envelopes e sidecar.

As conferences nao usam o mapa de tempo nem o diff da reconformacao: o oraculo
vem dos eventos dos dois cortes, pelo TC de origem, e o sidecar e conferido
pelo que a copia aplicada carrega de fato.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from .als_paths import AUDIO_CLIP_FIELDS
from .als_read_clips import _beats_to_samples, track_name
from .als_xml import get_value
from .reconform_automation_points import _parse_events
from .reconform_locator_rules import _is_fixed_locator
from .reconform_targets import _index_targets
from .reconform_verify import (
    _Ctx,
    _base_saiu,
    _emenda,
    _evento_em,
    _frames,
    _oraculo,
    _sha256,
)
from .sidecar import Anchor, read_anchor, sidecar_path_for
from .timecode import label_to_frames, label_to_samples

_CAMINHO_CLIPS = "DeviceChain/MainSequencer/Sample/ArrangerAutomation/Events/AudioClip"
_CAMINHO_ENVELOPES = "AutomationEnvelopes/Envelopes/AutomationEnvelope"
# Uma amostra: a tolerancia de todas as posicoes conferidas.
_TOLERANCIA = 1
# O Time do clip e o CurrentStart saem do mesmo double: a comparacao e exata.
_TOLERANCIA_BEAT = 1e-9


@dataclass(frozen=True)
class _ClipCampos:
    """Campos de um AudioClip lidos do XML."""

    node: ET.Element
    track: str
    caminho: str
    loop_start: float
    loop_end: float
    out_marker: str | None
    inicio: float
    fim: float
    warped: bool


def _float(bruto: str | None) -> float | None:
    """Numero do XML, ou None quando nao da para ler."""
    if bruto is None:
        return None
    try:
        return float(bruto)
    except ValueError:
        return None


def _ler_clip(node: ET.Element, track: str) -> _ClipCampos | None:
    """Campos do clip, ou None quando falta algum campo esperado."""
    inicio = _float(get_value(node, AUDIO_CLIP_FIELDS["current_start"]))
    fim = _float(get_value(node, AUDIO_CLIP_FIELDS["current_end"]))
    loop_start = _float(get_value(node, AUDIO_CLIP_FIELDS["loop_start"]))
    loop_end = _float(get_value(node, AUDIO_CLIP_FIELDS["loop_end"]))
    if None in (inicio, fim, loop_start, loop_end):
        return None
    return _ClipCampos(
        node=node,
        track=track,
        caminho=(get_value(node, AUDIO_CLIP_FIELDS["sample_path"]) or "").replace("\\", "/"),
        loop_start=loop_start,
        loop_end=loop_end,
        out_marker=get_value(node, AUDIO_CLIP_FIELDS["out_marker"]),
        inicio=inicio,
        fim=fim,
        warped=(get_value(node, AUDIO_CLIP_FIELDS["is_warped"]) or "").lower() == "true",
    )


def _clips_lidos(root: ET.Element) -> list[_ClipCampos]:
    """Clips do set com os campos conferidos."""
    saida: list[_ClipCampos] = []
    for node in root.findall("./LiveSet/Tracks/AudioTrack"):
        nome = track_name(node)
        for clip in node.findall(_CAMINHO_CLIPS):
            campos = _ler_clip(clip, nome)
            if campos is not None:
                saida.append(campos)
    return saida


def _mesmo_valor(primeiro: str | None, segundo: str | None) -> bool:
    """Dois valores iguais, comparados como numero quando da."""
    if primeiro == segundo:
        return True
    try:
        return float(primeiro) == float(segundo)
    except (TypeError, ValueError):
        return False


@dataclass(frozen=True)
class Conference:
    """Os dois sets e as ancoras que as conferences extras comparam."""

    ctx: _Ctx
    root_o: ET.Element
    root_a: ET.Element
    origem_p: Path
    aplicado_p: Path
    anchor_o: Anchor
    anchor_a: Anchor


def _candidato(clip: _ClipCampos, origem: _ClipCampos) -> bool:
    """Verdadeiro quando o clip de origem contem o LoopStart do clip da copia."""
    return clip.caminho == origem.caminho and origem.loop_start <= clip.loop_start < origem.loop_end


def _conferir_campos_clip(dados: Conference) -> None:
    """Confere Time, comprimento do loop e OutMarker de cada clip da copia."""
    origem = _clips_lidos(dados.root_o)
    for clip in _clips_lidos(dados.root_a):
        _conferir_clip(dados, clip, origem)


def _conferir_clip(dados: Conference, clip: _ClipCampos, origem: list[_ClipCampos]) -> None:
    """Confere o Time, o comprimento do loop e o OutMarker de um clip da copia."""
    bruto = clip.node.get("Time")
    valor = _float(bruto)
    if valor is None or abs(valor - clip.inicio) > _TOLERANCIA_BEAT:
        dados.ctx.problema(
            "RECONFORM_VERIFY_CLIP_FIELDS",
            clip.track,
            f"the clip Time {bruto!r} differs from the CurrentStart {clip.inicio}",
        )
    if not clip.warped:
        duracao = (clip.fim - clip.inicio) * 60.0 / dados.anchor_a.bpm
        erro = abs((clip.loop_end - clip.loop_start) - duracao) * dados.anchor_a.sample_rate
        if erro > _TOLERANCIA:
            dados.ctx.problema(
                "RECONFORM_VERIFY_CLIP_FIELDS",
                clip.track,
                f"the clip loop covers {(clip.loop_end - clip.loop_start):.6f}s "
                f"and the clip lasts {duracao:.6f}s",
            )
    _conferir_marcador(dados, clip, origem)


def _conferir_marcador(dados: Conference, clip: _ClipCampos, origem: list[_ClipCampos]) -> None:
    """O OutMarker do clip tem de ser o do clip de origem de onde ele veio."""
    candidatos = [c for c in origem if _candidato(clip, c)]
    if not candidatos:
        dados.ctx.problema(
            "RECONFORM_VERIFY_CLIP_FIELDS",
            clip.track,
            "clip without a source clip with the same file and loop range",
        )
        return
    if not any(_mesmo_valor(clip.out_marker, c.out_marker) for c in candidatos):
        dados.ctx.problema(
            "RECONFORM_VERIFY_CLIP_FIELDS",
            clip.track,
            f"the clip OutMarker {clip.out_marker} differs from the source clip",
        )


def _locators(root: ET.Element, anchor: Anchor) -> list[tuple[str, int]]:
    """Nome e posicao absoluta, em samples, de cada locator do set."""
    container = root.find(".//LiveSet/Locators/Locators")
    if container is None:
        return []
    saida: list[tuple[str, int]] = []
    for loc in container.findall("Locator"):
        tempo = _float(get_value(loc, "Time"))
        if tempo is None:
            continue
        posicao = anchor.live_origin_samples + _beats_to_samples(
            tempo, anchor.sample_rate, anchor.bpm
        )
        saida.append((get_value(loc, "Name") or "", posicao))
    return saida


def _tempo_do_locator(loc: ET.Element) -> float | None:
    """O Time do locator em beats, ou None quando nao da para ler."""
    tempo = _float(get_value(loc, "Time"))
    if tempo is None or not math.isfinite(tempo):
        return None
    return tempo


def _locators_com_tempo(root: ET.Element) -> list[tuple[str, float]]:
    """Nome e Time de cada locator do set, em beats."""
    container = root.find(".//LiveSet/Locators/Locators")
    if container is None:
        return []
    saida: list[tuple[str, float]] = []
    for loc in container.findall("Locator"):
        tempo = _tempo_do_locator(loc)
        if tempo is None:
            continue
        saida.append((get_value(loc, "Name") or "", tempo))
    return saida


def _conferir_locator_fixo(dados: Conference, nome: str, tempo: float) -> None:
    """O locator que o apply mantem fixo tem de ficar com o mesmo Time na copia."""
    for outro, tempo_outro in _locators_com_tempo(dados.root_a):
        if outro == nome and abs(tempo_outro - tempo) <= _TOLERANCIA_BEAT:
            return
    dados.ctx.problema(
        "RECONFORM_VERIFY_LOCATOR",
        "",
        f"the fixed locator {nome!r} did not keep the time {tempo}",
    )


def _conferir_locator_de_usuario(
    dados: Conference, nome: str, tempo: float, aplicados: list[tuple[str, int]]
) -> None:
    """O locator de usuario tem de estar na posicao que o oraculo diz."""
    amostras = dados.anchor_o.live_origin_samples + _beats_to_samples(
        tempo, dados.anchor_o.sample_rate, dados.anchor_o.bpm
    )
    tipo, posicao = _oraculo(dados.ctx, amostras)
    if tipo == "map":
        esperado = nome
    elif tipo == "saiu":
        esperado = f"{nome} [saiu]"
    else:
        return
    if posicao is None:
        return
    achou = any(
        outro == esperado and abs(local - posicao) <= _TOLERANCIA for outro, local in aplicados
    )
    if not achou:
        dados.ctx.problema(
            "RECONFORM_VERIFY_LOCATOR",
            "",
            f"the locator {esperado!r} is not at the position {posicao}",
        )


def _conferir_locators_usuario(dados: Conference) -> None:
    """Cada locator de usuario da origem esta na posicao do oraculo.

    Os locators que o apply mantem fixos nao entram no oraculo: eles ficam
    onde a origem os deixou, e o que se confere e o Time que a copia carrega.
    """
    container = dados.root_o.find(".//LiveSet/Locators/Locators")
    if container is None:
        return
    aplicados = _locators(dados.root_a, dados.anchor_a)
    for loc in container.findall("Locator"):
        nome = get_value(loc, "Name")
        tempo = _tempo_do_locator(loc)
        if nome is None or tempo is None:
            continue
        if _is_fixed_locator(loc, nome):
            _conferir_locator_fixo(dados, nome, tempo)
            continue
        _conferir_locator_de_usuario(dados, nome, tempo, aplicados)


def _posicao_rc(dados: Conference, kind: str, tc: str) -> int | None:
    """Posicao esperada de um marcador RC, em samples."""
    rate = dados.anchor_a.timecode_rate
    try:
        if kind == "delete":
            return _emenda(dados.ctx, label_to_frames(tc, rate))
        return round(label_to_samples(tc, rate, dados.anchor_a.sample_rate))
    except ValueError:
        return None


def _conferir_locators_rc(dados: Conference) -> None:
    """Cada marcador RC da copia esta na posicao do TC que ele nomeia."""
    for nome, posicao in _locators(dados.root_a, dados.anchor_a):
        partes = nome.split(" ", 2)
        if len(partes) != 3 or partes[0] != "RC":
            continue
        esperado = _posicao_rc(dados, partes[1], partes[2])
        if esperado is None:
            continue
        if abs(posicao - esperado) > _TOLERANCIA:
            dados.ctx.problema(
                "RECONFORM_VERIFY_LOCATOR",
                "",
                f"the locator {nome!r} is not at the position of {partes[2]}",
            )


def _tracks_saiu(root: ET.Element, base: str) -> list[ET.Element]:
    """As tracks [saiu] que vieram da track de origem chamada base."""
    saida = []
    for node in root.findall("./LiveSet/Tracks/AudioTrack"):
        if _base_saiu(track_name(node)) == base:
            saida.append(node)
    return saida


def _envelopes_do_destino(dados: Conference, nome: str, alvo, tipo: str) -> list[ET.Element]:
    """Envelopes da copia onde o evento do destino deve estar."""
    if tipo == "map":
        nodes = [
            n for n in dados.root_a.findall("./LiveSet/Tracks/AudioTrack") if track_name(n) == nome
        ]
    elif tipo == "saiu" and not alvo.is_speaker:
        nodes = _tracks_saiu(dados.root_a, nome)
    else:
        return []
    envelopes = []
    for node in nodes:
        _by_id, by_path = _index_targets(node)
        alvo_id = by_path.get(alvo.path)
        if alvo_id is None:
            continue
        for env in node.findall(_CAMINHO_ENVELOPES):
            ponteiro = env.find("EnvelopeTarget/PointeeId")
            if ponteiro is not None and ponteiro.get("Value") == alvo_id:
                envelopes.append(env)
    return envelopes


def _evento_presente(
    envelopes: list[ET.Element], elemento: ET.Element, posicao: int, anchor: Anchor
) -> bool:
    """Verdadeiro quando algum envelope tem o evento com o mesmo valor na posicao."""
    for env in envelopes:
        caixa = env.find("Automation/Events")
        if caixa is None:
            continue
        _sentinelas, pontos = _parse_events(caixa, anchor)
        for ponto in pontos:
            if abs(ponto.abs_samples - posicao) > _TOLERANCIA:
                continue
            if _mesmo_valor(elemento.get("Value"), ponto.element.get("Value")):
                return True
    return False


def _conferir_automation(dados: Conference) -> None:
    """Confere os envelopes de automacao das tracks de origem."""
    for track_o in dados.root_o.findall("./LiveSet/Tracks/AudioTrack"):
        nome = track_name(track_o)
        by_id, _caminhos = _index_targets(track_o)
        for env in track_o.findall(_CAMINHO_ENVELOPES):
            ponteiro = env.find("EnvelopeTarget/PointeeId")
            if ponteiro is None:
                continue
            alvo = by_id.get(ponteiro.get("Value", ""))
            if alvo is None:
                continue
            _conferir_envelope(dados, env, alvo, nome)


def _conferir_envelope(dados: Conference, env: ET.Element, alvo, nome: str) -> None:
    """Confere os sentinelas e os eventos de um envelope de automacao."""
    caixa = env.find("Automation/Events")
    if caixa is None:
        return
    sentinelas_o, pontos_o = _parse_events(caixa, dados.anchor_o)
    env_a = _envelopes_do_destino(dados, nome, alvo, "map")
    if not env_a:
        if sentinelas_o:
            dados.ctx.problema(
                "RECONFORM_VERIFY_AUTOMATION", nome, "the envelope is missing in the copy"
            )
        return
    caixa_a = env_a[0].find("Automation/Events")
    sentinelas_a = _parse_events(caixa_a, dados.anchor_a)[0] if caixa_a is not None else []
    if len(sentinelas_a) < len(sentinelas_o):
        dados.ctx.problema(
            "RECONFORM_VERIFY_AUTOMATION",
            nome,
            "the copy lost the sentinel events of the envelope",
        )
    for ponto in pontos_o:
        _conferir_evento(dados, ponto, alvo, nome)


def _conferir_evento(dados: Conference, ponto, alvo, nome: str) -> None:
    """Confere um evento nao-sentinela do envelope de origem."""
    frame = _frames(dados.ctx, ponto.abs_samples)
    evento = _evento_em(dados.ctx.old.events, frame)
    if evento is None or evento.kind != "clip":
        return
    tipo, posicao = _oraculo(dados.ctx, ponto.abs_samples)
    if tipo == "saiu":
        # Na track [saiu] o ponto fica na emenda mais o deslocamento que tinha
        # dentro do trecho removido, como os clips (reconform_verify_saiu).
        from .reconform_verify_saiu import _esperado

        posicao = _esperado(dados.ctx, ponto.abs_samples)
    if posicao is None:
        return
    envelopes = _envelopes_do_destino(dados, nome, alvo, tipo)
    if not envelopes:
        return
    if not _evento_presente(envelopes, ponto.element, posicao, dados.anchor_a):
        dados.ctx.problema(
            "RECONFORM_VERIFY_AUTOMATION",
            nome,
            f"the event of value {ponto.element.get('Value')} is missing at {posicao}",
        )


def _clip_ranges(root: ET.Element, anchor: Anchor) -> list[dict]:
    """Os intervalos de clip de cada track, em samples, como o sidecar grava."""
    saida: list[dict] = []
    for node in root.findall("./LiveSet/Tracks/AudioTrack"):
        pares: set[tuple[int, int]] = set()
        for clip in node.findall(_CAMINHO_CLIPS):
            inicio = _float(get_value(clip, AUDIO_CLIP_FIELDS["current_start"]))
            fim = _float(get_value(clip, AUDIO_CLIP_FIELDS["current_end"]))
            if inicio is None or fim is None:
                continue
            primeiro = anchor.live_origin_samples + _beats_to_samples(
                inicio, anchor.sample_rate, anchor.bpm
            )
            ultimo = anchor.live_origin_samples + _beats_to_samples(
                fim, anchor.sample_rate, anchor.bpm
            )
            if ultimo > primeiro:
                pares.add((primeiro, ultimo))
        saida.append({"track": track_name(node), "ranges": [list(p) for p in sorted(pares)]})
    return saida


def _conferir_sidecar(dados: Conference, source_sidecar_sha256: str | None) -> None:
    """Confere o sidecar da copia: entradas por track e clip_ranges."""
    try:
        anchor = read_anchor(dados.aplicado_p)
    except (OSError, ValueError, KeyError) as exc:
        dados.ctx.problema(
            "RECONFORM_VERIFY_SIDECAR", "", f"the sidecar of the copy could not be read: {exc}"
        )
        return
    tracks = dados.root_a.findall("./LiveSet/Tracks/AudioTrack")
    if anchor.audio_source_indices is not None and len(anchor.audio_source_indices) != len(tracks):
        dados.ctx.problema(
            "RECONFORM_VERIFY_SIDECAR",
            "",
            "audio_source_indices has not one entry per track of the copy",
        )
    if anchor.track_bindings is None or len(anchor.track_bindings) != len(tracks):
        dados.ctx.problema(
            "RECONFORM_VERIFY_SIDECAR",
            "",
            "track_bindings has not one entry per track of the copy",
        )
    if anchor.clip_ranges != _clip_ranges(dados.root_a, anchor):
        dados.ctx.problema(
            "RECONFORM_VERIFY_SIDECAR",
            "",
            "clip_ranges does not match the clips of the copy",
        )
    if source_sidecar_sha256 is not None:
        origem = sidecar_path_for(dados.origem_p)
        if not origem.exists() or _sha256(origem) != source_sidecar_sha256:
            dados.ctx.problema("RECONFORM_VERIFY_SOURCE_CHANGED", "", "the source sidecar changed")


def _conferir_clip_envelopes(dados: Conference) -> None:
    """Confere os ClipEnvelopes dos clips da copia contra os da origem."""
    # O modulo de envelopes importa este aqui: a importacao fica dentro da
    # funcao para nao fechar o ciclo entre os dois.
    from .reconform_verify_clip_env import conferir_clip_envelopes

    conferir_clip_envelopes(
        dados.ctx, _clips_lidos(dados.root_o), _clips_lidos(dados.root_a), dados.anchor_a
    )


def conferir_extra(dados: Conference, source_sidecar_sha256: str | None = None) -> None:
    """Roda as conferences extras do reconform e registra os problemas."""
    _conferir_campos_clip(dados)
    _conferir_locators_usuario(dados)
    _conferir_locators_rc(dados)
    _conferir_automation(dados)
    _conferir_clip_envelopes(dados)
    _conferir_sidecar(dados, source_sidecar_sha256)
