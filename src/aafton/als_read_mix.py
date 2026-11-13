"""Leitura do mixer e das automacoes que o AAF nao representa.

O AAF exportado tem um slot por AudioTrack do Live. GroupTrack, ReturnTrack,
MainTrack e MasterTrack nao viram slot nenhum, e o envelope de automacao de
um clip tambem nao vira: por isso o volume, o pan, o mute e a automacao deles
somem na exportacao sem deixar rastro. Este modulo le esses dados e reporta o
que nao tem representacao, para que o exportador recuse o set em modo estrito
em vez de entregar um arquivo com menos informacao do que o set tinha.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from .als_read_clips import ReadContext, track_name
from .als_xml import get_value

# Parametros do mixer cuja automacao nao mexe no audio exportado. O tempo tem
# regra propria (ALS_TEMPO_AUTOMATION_UNSUPPORTED), porque o BPM decide onde
# cada beat cai na timeline; a formula de compasso move a grade musical sem mover
# amostra nenhuma, e por isso nao vira aviso.
TARGETS_SEM_EFEITO_NO_AUDIO = ("Tempo", "TimeSignature")

# Como o Live chama cada tipo de track que nao existe no AAF. O Live 12 nomeia
# a track master de MainTrack e o Live 11 de MasterTrack.
TIPOS_DE_TRACK = {
    "GroupTrack": "group",
    "ReturnTrack": "return",
    "MainTrack": "main",
    "MasterTrack": "master",
}

# Estado neutro do mixer de um grupo: volume 1.0, pan no centro e grupo sem
# mute. E o que o grupo clonado do template traz.
VOLUME_NEUTRO = 1.0
PAN_NEUTRO = 0.0
TOLERANCIA = 1e-6


def read_unsupported_mix(ctx: ReadContext, root: ET.Element) -> None:
    """Le as automacoes e o mixer que o AAF exportado nao consegue carregar."""
    for node in root.iter("GroupTrack"):
        _le_mix_do_grupo(ctx, node)
        _le_automacao_da_track(ctx, node, "GroupTrack")
    for node in root.iter("ReturnTrack"):
        _le_automacao_da_track(ctx, node, "ReturnTrack")
    for node in root.iter("MainTrack"):
        _le_automacao_da_track(ctx, node, "MainTrack")
    for node in root.iter("MasterTrack"):
        _le_automacao_da_track(ctx, node, "MasterTrack")
    for node in root.findall("./LiveSet/Tracks/AudioTrack"):
        _le_envelopes_dos_clips(ctx, node, track_name(node))


def _le_envelopes_dos_clips(ctx: ReadContext, node: ET.Element, nome_track: str) -> None:
    """Le o envelope de automacao de cada clip de uma track de audio."""
    for clip in node.findall(
        "DeviceChain/MainSequencer/Sample/ArrangerAutomation/Events/AudioClip"
    ):
        _le_envelope_do_clip(ctx, clip, nome_track)


def _le_automacao_da_track(ctx: ReadContext, node: ET.Element, tipo: str) -> None:
    """Reporta a automacao de uma track que o AAF nao tem como representar."""
    alvos = _alvos_do_mixer(node)
    for envelope in node.findall("AutomationEnvelopes/Envelopes/AutomationEnvelope"):
        ponteiro = envelope.find("EnvelopeTarget/PointeeId")
        alvo_id = ponteiro.get("Value", "") if ponteiro is not None else ""
        parametro = alvos.get(alvo_id, "")
        if not _tem_eventos(envelope) or parametro in TARGETS_SEM_EFEITO_NO_AUDIO:
            continue
        _reporta_automacao(ctx, node, tipo, parametro, alvo_id)


def _alvos_do_mixer(node: ET.Element) -> dict[str, str]:
    """Nome do parametro do mixer para cada AutomationTarget da track."""
    mixer = node.find("DeviceChain/Mixer")
    if mixer is None:
        return {}
    alvos: dict[str, str] = {}
    for parametro in mixer:
        alvo = parametro.find("AutomationTarget")
        if alvo is not None and alvo.get("Id"):
            alvos[alvo.get("Id", "")] = parametro.tag
    return alvos


def _tem_eventos(envelope: ET.Element) -> bool:
    """Diz se o envelope carrega algum evento de automacao."""
    return any(elem.tag.endswith("Event") for elem in envelope.iter())


def _reporta_automacao(
    ctx: ReadContext, node: ET.Element, tipo: str, parametro: str, alvo_id: str
) -> None:
    """Avisa que a automacao de uma track nao tem representacao no export."""
    nome = track_name(node) or tipo
    local = f"DeviceChain/Mixer/{parametro}" if parametro else f"target {alvo_id}"
    mensagem = (
        f"automation of the {TIPOS_DE_TRACK[tipo]} {nome!r} "
        "has no representation in the exported file: "
        f"envelope in {local}"
    )
    ctx.report.warning(
        "ALS_AUTOMATION_UNSUPPORTED",
        mensagem,
        {"track": nome, "kind": tipo, "target": parametro or alvo_id, "path": local},
    )
    ctx.session.unsupported_export_features.append(mensagem)


def _le_mix_do_grupo(ctx: ReadContext, node: ET.Element) -> None:
    """Reporta o volume, o pan ou o mute de um grupo que o AAF nao tem."""
    fora = _valores_fora_do_neutro(node)
    if not fora:
        return
    nome = track_name(node)
    valores = ", ".join(f"{chave}={valor}" for chave, valor in fora.items())
    mensagem = f"mix of the group {nome!r} has no representation in the exported file: {valores}"
    ctx.report.warning("ALS_GROUP_MIX_UNSUPPORTED", mensagem, {"group": nome, **fora})
    ctx.session.unsupported_export_features.append(mensagem)


def _valores_fora_do_neutro(node: ET.Element) -> dict[str, float | bool]:
    """Volume, pan e mute do grupo quando nao estao no estado neutro."""
    fora: dict[str, float | bool] = {}
    volume = _numero_do_mixer(node, "DeviceChain/Mixer/Volume/Manual")
    if volume is not None and abs(volume - VOLUME_NEUTRO) >= TOLERANCIA:
        fora["volume"] = volume
    pan = _numero_do_mixer(node, "DeviceChain/Mixer/Pan/Manual")
    if pan is not None and abs(pan - PAN_NEUTRO) >= TOLERANCIA:
        fora["pan"] = pan
    speaker = get_value(node, "DeviceChain/Mixer/Speaker/Manual")
    if speaker is not None and speaker.strip().lower() == "false":
        fora["mute"] = True
    return fora


def _numero_do_mixer(node: ET.Element, caminho: str) -> float | None:
    """Valor numerico do mixer; None quando falta ou nao e numero."""
    bruto = get_value(node, caminho)
    try:
        return float(bruto) if bruto is not None else None
    except ValueError:
        return None


def _le_envelope_do_clip(ctx: ReadContext, clip: ET.Element, nome_track: str) -> None:
    """Avisa que o clip tem envelope de automacao sem representacao no export."""
    for envelope in clip.findall("Envelopes/Envelopes/ClipEnvelope"):
        if not _tem_eventos(envelope):
            continue
        nome = clip.find("Name")
        nome_clip = nome.get("Value", "") if nome is not None else ""
        mensagem = (
            f"automation of the clip {nome_clip!r} in track {nome_track!r} "
            "has no representation in the exported file: "
            "ClipEnvelope in AudioClip/Envelopes/Envelopes/ClipEnvelope"
        )
        ctx.report.warning(
            "ALS_AUTOMATION_UNSUPPORTED",
            mensagem,
            {"clip": nome_clip, "track": nome_track, "target": "ClipEnvelope"},
        )
        ctx.session.unsupported_export_features.append(mensagem)
