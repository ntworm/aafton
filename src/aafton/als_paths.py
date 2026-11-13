"""Caminhos XML reais do template, descobertos pela Tarefa 9.

Nenhum nome de elemento neste projeto pode ser escrito de memoria. Todo valor
aqui saiu da saida de scripts/introspect_template.py rodando no template de
verdade, esta registrado em docs/ALS_XML_MAP.md, e e verificado pelo teste
tests/unit/test_template_completeness.py.

Se o Live mudar de versao e algum caminho deixar de existir, o teste falha e
este arquivo e o unico lugar a corrigir.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

REQUIRED_PROTOTYPES: tuple[str, ...] = (
    "AudioTrack",
    "GroupTrack",
    "ReturnTrack",
    "AudioClip",
    "Locator",
)

# Chave estavel usada pelo codigo -> caminho relativo ao no <AudioClip>,
# copiado de docs/ALS_XML_MAP.md.
AUDIO_CLIP_FIELDS: dict[str, str] = {
    "time": "CurrentStart",
    "current_start": "CurrentStart",
    "current_end": "CurrentEnd",
    "loop_start": "Loop/LoopStart",
    "loop_end": "Loop/LoopEnd",
    "loop_on": "Loop/LoopOn",
    "out_marker": "Loop/OutMarker",
    "hidden_loop_start": "Loop/HiddenLoopStart",
    "hidden_loop_end": "Loop/HiddenLoopEnd",
    "start_relative": "Loop/StartRelative",
    "is_warped": "IsWarped",
    "name": "Name",
    "sample_path": "SampleRef/FileRef/Path",
    "sample_relative_path": "SampleRef/FileRef/RelativePath",
    "fade_in_length": "Fades/FadeInLength",
    "fade_out_length": "Fades/FadeOutLength",
    "crossfade_in_state": "Fades/CrossfadeInState",
    "is_default_fade_in": "Fades/IsDefaultFadeIn",
    "is_default_fade_out": "Fades/IsDefaultFadeOut",
    "fade_in_curve_skew": "Fades/FadeInCurveSkew",
    "fade_in_curve_slope": "Fades/FadeInCurveSlope",
    "fade_out_curve_skew": "Fades/FadeOutCurveSkew",
    "fade_out_curve_slope": "Fades/FadeOutCurveSlope",
    "clip_gain": "SampleVolume",
}

LOCATOR_FIELDS: dict[str, str] = {
    "time": "Time",
    "name": "Name",
    "annotation": "Annotation",
    "is_song_start": "IsSongStart",
}

# Campos que o escritor grava na track do Live. O pan, o volume e o mute nao
# sao campos da track: sao parametros do mixer que vive no DeviceChain, e cada
# parametro tem o valor em Manual.
TRACK_FIELDS: dict[str, str] = {
    "name": "Name/UserName",
    "effective_name": "Name/EffectiveName",
    "annotation": "Name/Annotation",
    "pan": "DeviceChain/Mixer/Pan/Manual",
    "mute": "DeviceChain/Mixer/Speaker/Manual",
    "volume": "DeviceChain/Mixer/Volume/Manual",
    "volume_automation_target": "DeviceChain/Mixer/Volume/AutomationTarget",
    "arrangement_events": "DeviceChain/MainSequencer/Sample/ArrangerAutomation/Events",
}

# Campos que o escritor grava no no LiveSet.
LIVESET_FIELDS: dict[str, str] = {
    "smpte_format": "SmpteFormat",
}


def find_prototype(root: ET.Element, tag: str) -> ET.Element | None:
    """Primeira instancia de um elemento, para servir de molde de clonagem."""
    return root.find(f".//{tag}")
