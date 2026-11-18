"""Definicoes AAF criadas sob demanda durante a escrita do arquivo.

O writer usa algumas definicoes do dicionario (OperationDef, ParameterDef,
InterpolationDef) que antes eram criadas por funcoes aninhadas dentro de
write_aaf. Aqui elas viraram metodos de AafBuilder. A criacao continua
preguicosa: cada definicao nasce e e registrada no dicionario apenas no
primeiro uso, porque a ordem das entradas do dicionario faz parte do arquivo
AAF escrito.
"""

import fractions
import math

import aaf2
from aaf2.auid import AUID

from .ir import SessionIR
from .report import Report

_MONO_AUDIO_PAN_AUID = AUID("9d2ea893-0968-11d3-8a38-0050040ef7d2")
_PAN_PARAMETER_AUID = AUID("e4962322-2267-11d3-8a4c-0050040ef7d2")
_AUDIO_CHANNEL_COMBINER_AUID = AUID("6b46dd7a-132d-4856-ab21-8b751d8462ec")
_AVID_PARAMETER_BYTE_ORDER_AUID = AUID("c0038672-a8cf-11d3-a05b-006094eb75cb")
_AVID_EFFECT_ID_AUID = AUID("93994bd6-a81d-11d3-a05b-006094eb75cb")
_AAF_INT16_TYPE_AUID = AUID("01010600-0000-0000-060e-2b3401040101")
_AVID_BAG_OF_BITS_TYPE_AUID = AUID("ccaa73d1-f538-11d3-a081-006094eb75cb")
_AUDIO_CHANNEL_COMBINER_EFFECT_ID = list(b"EFF2_AUDIO_CHANNEL_COMBINER\x00")


class AafBuilder:
    """Cria as definicoes AAF do arquivo, uma vez cada, no primeiro uso."""

    def __init__(self, f, report: Report, session: SessionIR, curva_suportada) -> None:
        self.f = f
        self.report = report
        self.session = session
        self.curva_suportada = curva_suportada
        self.audio_dissolve_def = None
        self.audio_pan_def = None
        self.audio_pan_parameter_def = None
        self.audio_channel_combiner_def = None
        self.avid_byte_order_parameter_def = None
        self.avid_effect_id_parameter_def = None
        self.audio_gain_def = None
        self.audio_gain_parameter_def = None
        self.linear_interp_def = None

    def _get_linear_interp(self):
        """Cria a definicao de interpolacao linear, uma unica vez."""
        if self.linear_interp_def is None:
            from aaf2.misc import LinearInterp

            interp_def = self.f.create.InterpolationDef(LinearInterp, "LinearInterp", "")
            self.linear_interp_def = interp_def
            self.f.dictionary.register_def(interp_def)
        return self.linear_interp_def

    def _ensure_audio_gain(self):
        """Cria o ParameterDef e o OperationDef de ganho, uma unica vez."""
        if self.audio_gain_def is None:
            gain_parameter_def = self.f.create.ParameterDef(
                AUID("e4962321-2267-11d3-8a4c-0050040ef7d2"),
                "Amplitude",
                "",
                self.f.dictionary.lookup_typedef("Rational"),
            )
            self.f.dictionary["ParameterDefinitions"].append(gain_parameter_def)
            self.audio_gain_parameter_def = gain_parameter_def
            self.audio_gain_def = self.f.create.OperationDef(
                AUID("9d2ea894-0968-11d3-8a38-0050040ef7d2"), "Audio Gain", ""
            )
            gain_def = self.audio_gain_def
            gain_def["IsTimeWarp"].value = False
            gain_def["NumberInputs"].value = 1
            sound_def = self.f.dictionary.lookup_datadef("Sound")
            gain_def["DataDefinition"].value = sound_def
            gain_def["ParametersDefined"].value.append(self.audio_gain_parameter_def)
            self.f.dictionary["OperationDefinitions"].append(self.audio_gain_def)

    def _rational32_gain(self, value: float) -> fractions.Fraction:
        """Converte o ganho para uma Fraction dentro do limite do Rational32 do AAF."""
        rational = fractions.Fraction(str(value))
        if max(abs(rational.numerator), rational.denominator) > 2147483647:
            max_denominator = max(1, int(2147483647 / max(1.0, value)))
            rational = rational.limit_denominator(max_denominator)
        if value > 0 and rational == 0:
            raise ValueError(f"gain below the Rational32 resolution of the AAF: {value}")
        if abs(rational.numerator) > 2147483647:
            raise ValueError(f"gain exceeds the Rational32 of the AAF: {value}")
        if float(rational) != value:
            self.report.warning(
                "WRITE_GAIN_ROUNDING",
                "gain approximated for the Rational32 of the AAF",
                {"requested": value, "written": float(rational)},
            )
        return rational

    def wrap_mute(self, segment, length: int):
        """Clip mudo na forma do Avid e do Pro Tools.

        Um Selector cujo Selected e um Filler e cujo material fica nos
        Alternates: o clip continua na timeline e o editor so o desmuta.
        """
        selector = self.f.create.Selector(media_kind="sound", length=length)
        selector["Selected"].value = self.f.create.Filler(media_kind="sound", length=length)
        selector["Alternates"].value = [segment]
        return selector

    def wrap_audio_gain(self, segment, length: int, value: float):
        """Envolve o segmento em um grupo de operacao de ganho constante."""
        if value == 1.0:
            return segment
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"invalid gain: {value}")
        self._ensure_audio_gain()
        operation = self.f.create.OperationGroup(self.audio_gain_def)
        operation.media_kind = "sound"
        operation.length = length
        operation.segments.append(segment)
        rational = self._rational32_gain(value)
        constant = aaf2.rational.AAFRational(rational)
        operation.parameters.append(
            self.f.create.ConstantValue(self.audio_gain_parameter_def, constant)
        )
        return operation

    def _add_gain_keyframe(self, varying, t_norm: float, val: float) -> None:
        """Converte um keyframe de ganho e o acrescenta ao VaryingValue."""
        r_val = self._rational32_gain(max(0.0, val))
        r_time = fractions.Fraction(str(t_norm))
        if max(abs(r_time.numerator), r_time.denominator) > 2147483647:
            r_time = r_time.limit_denominator(2147483647)
        time_rational = aaf2.rational.AAFRational(r_time)
        value_rational = aaf2.rational.AAFRational(r_val)
        varying.add_keyframe(time_rational, value_rational)

    def keyframes_variam(self, keyframes: list[tuple[float, float]]) -> bool:
        """Diz se os keyframes descrevem uma curva e nao um valor unico."""
        if len(keyframes) < 2:
            return False
        return any(abs(k[1] - keyframes[0][1]) >= 1e-6 for k in keyframes)

    def escreve_curva(self, keyframes: list[tuple[float, float]]) -> bool:
        """Diz se os keyframes viram VaryingValue no AAF.

        Sem as definicoes de VaryingValue no pyaaf2 instalado, a curva vira
        ganho constante: quem escreve precisa saber disso para nao anunciar
        uma automacao que nao foi gravada.
        """
        if not keyframes:
            return False
        if not self.curva_suportada():
            return False
        return self.keyframes_variam(keyframes)

    def wrap_varying_gain(self, segment, length: int, keyframes: list[tuple[float, float]]):
        """Envolve o segmento em um grupo de operacao de ganho com envelope."""
        if not keyframes:
            return segment
        if not self.escreve_curva(keyframes):
            return self.wrap_audio_gain(segment, length, keyframes[0][1])
        self._ensure_audio_gain()
        interp = self._get_linear_interp()
        operation = self.f.create.OperationGroup(self.audio_gain_def)
        operation.media_kind = "sound"
        operation.length = length
        operation.segments.append(segment)
        varying = self.f.create.VaryingValue(self.audio_gain_parameter_def, interp)
        for t_norm, val in keyframes:
            self._add_gain_keyframe(varying, t_norm, val)
        operation.parameters.append(varying)
        return operation

    def append_audio_dissolve(self, sequence, length: int) -> None:
        """Acrescenta ao Sequence um dissolve de audio entre dois clips."""
        if self.audio_dissolve_def is None:
            self.audio_dissolve_def = self.f.create.OperationDef(
                AUID("0c3bea41-fc05-11d2-8a29-0050040ef7d2"),
                "Avid Audio Dissolve",
                "",
            )
            dissolve_def = self.audio_dissolve_def
            dissolve_def["IsTimeWarp"].value = False
            dissolve_def["NumberInputs"].value = 2
            sound_def = self.f.dictionary.lookup_datadef("Sound")
            dissolve_def["DataDefinition"].value = sound_def
            self.f.dictionary["OperationDefinitions"].append(self.audio_dissolve_def)

        operation = self.f.create.OperationGroup(self.audio_dissolve_def)
        operation.media_kind = "sound"
        operation.length = length
        transition = self.f.create.Transition(media_kind="sound", length=length)
        transition.cutpoint = length
        transition["OperationGroup"].value = operation
        sequence.components.append(transition)

    def _ensure_audio_pan(self):
        """Cria o ParameterDef e o OperationDef de pan, uma unica vez."""
        if self.audio_pan_parameter_def is None:
            pan_parameter_def = self.f.create.ParameterDef(
                _PAN_PARAMETER_AUID,
                "Pan value",
                "",
                self.f.dictionary.lookup_typedef("Rational"),
            )
            self.f.dictionary["ParameterDefinitions"].append(pan_parameter_def)
            self.audio_pan_parameter_def = pan_parameter_def
        if self.audio_pan_def is None:
            pan_def = self.f.create.OperationDef(
                _MONO_AUDIO_PAN_AUID,
                "Mono Audio Pan",
                "",
            )
            self.audio_pan_def = pan_def
            pan_def["IsTimeWarp"].value = False
            pan_def["NumberInputs"].value = 1
            sound_def = self.f.dictionary.lookup_datadef("Sound")
            pan_def["DataDefinition"].value = sound_def
            pan_def["ParametersDefined"].value.append(self.audio_pan_parameter_def)
            self.f.dictionary["OperationDefinitions"].append(self.audio_pan_def)

    def wrap_audio_pan(self, sequence, length: int, value: float):
        """Envolve o Sequence em um grupo de operacao de pan constante."""
        self._ensure_audio_pan()
        operation = self.f.create.OperationGroup(self.audio_pan_def)
        operation.media_kind = "sound"
        operation.length = length
        operation.segments.append(sequence)
        rational = aaf2.rational.AAFRational(fractions.Fraction(str(value)))
        operation.parameters.append(
            self.f.create.ConstantValue(self.audio_pan_parameter_def, rational)
        )
        return operation

    def _add_pan_keyframe(self, varying, rel_sample: int, length: int, live_pan: float) -> None:
        """Converte um keyframe de pan e o acrescenta ao VaryingValue."""
        t_norm = fractions.Fraction(rel_sample, max(1, length))
        aaf_pan = fractions.Fraction(str(max(0.0, min(1.0, (live_pan + 1.0) / 2.0))))
        time_rational = aaf2.rational.AAFRational(t_norm)
        pan_rational = aaf2.rational.AAFRational(aaf_pan)
        varying.add_keyframe(time_rational, pan_rational)

    def wrap_varying_pan(self, sequence, length: int, pan_points: list[tuple[int, float]]):
        """Envolve o Sequence em um grupo de operacao de pan com envelope."""
        self._ensure_audio_pan()
        interp = self._get_linear_interp()
        operation = self.f.create.OperationGroup(self.audio_pan_def)
        operation.media_kind = "sound"
        operation.length = length
        operation.segments.append(sequence)
        varying = self.f.create.VaryingValue(self.audio_pan_parameter_def, interp)
        track_start_abs = self.session.start_timecode_samples
        sorted_pts = sorted(pan_points, key=lambda p: p[0])
        pts_to_write = []
        if sorted_pts[0][0] > track_start_abs:
            pts_to_write.append((track_start_abs, sorted_pts[0][1]))
        pts_to_write.extend(sorted_pts)
        if sorted_pts[-1][0] < track_start_abs + length:
            pts_to_write.append((track_start_abs + length, sorted_pts[-1][1]))
        for sample_abs, live_pan in pts_to_write:
            rel_sample = max(0, min(length, sample_abs - track_start_abs))
            self._add_pan_keyframe(varying, rel_sample, length, live_pan)
        operation.parameters.append(varying)
        return operation

    def _ensure_audio_channel_combiner(self):
        """Cria as definicoes do combinador de canais, uma unica vez."""
        if self.avid_byte_order_parameter_def is None:
            byte_order_def = self.f.create.ParameterDef(
                _AVID_PARAMETER_BYTE_ORDER_AUID,
                "AvidParameterByteOrder",
                "",
                self.f.metadict.typedefs_by_auid[_AAF_INT16_TYPE_AUID],
            )
            self.f.dictionary["ParameterDefinitions"].append(byte_order_def)
            self.avid_byte_order_parameter_def = byte_order_def
        if self.avid_effect_id_parameter_def is None:
            effect_id_def = self.f.create.ParameterDef(
                _AVID_EFFECT_ID_AUID,
                "AvidEffectID",
                "",
                self.f.metadict.typedefs_by_auid[_AVID_BAG_OF_BITS_TYPE_AUID],
            )
            self.f.dictionary["ParameterDefinitions"].append(effect_id_def)
            self.avid_effect_id_parameter_def = effect_id_def
        if self.audio_channel_combiner_def is None:
            combiner_def = self.f.create.OperationDef(
                _AUDIO_CHANNEL_COMBINER_AUID,
                "Audio Channel Combiner",
                "",
            )
            self.audio_channel_combiner_def = combiner_def
            combiner_def["IsTimeWarp"].value = False
            combiner_def["NumberInputs"].value = 1
            combiner_def["Bypass"].value = 0
            combiner_def["OperationCategory"].value = "OperationCategory_Effect"
            sound_def = self.f.dictionary.lookup_datadef("Sound")
            combiner_def["DataDefinition"].value = sound_def
            combiner_def["ParametersDefined"].value.append(self.avid_byte_order_parameter_def)
            combiner_def["ParametersDefined"].value.append(self.avid_effect_id_parameter_def)
            self.f.dictionary["OperationDefinitions"].append(self.audio_channel_combiner_def)

    def combine_audio_channels(self, source_clips, length: int):
        """Envolve os clips de um canal no grupo que combina os canais de audio."""
        self._ensure_audio_channel_combiner()
        operation = self.f.create.OperationGroup(self.audio_channel_combiner_def)
        operation.media_kind = "sound"
        operation.length = length
        operation["ComponentAttributeList"].append(
            self.f.create.TaggedValue("_IGNORE_TRACKING", 0, "aafInt32")
        )
        for source_clip in source_clips:
            operation.segments.append(source_clip)
        operation.parameters.append(
            self.f.create.ConstantValue(self.avid_byte_order_parameter_def, 18761)
        )
        operation.parameters.append(
            self.f.create.ConstantValue(
                self.avid_effect_id_parameter_def,
                _AUDIO_CHANNEL_COMBINER_EFFECT_ID,
            )
        )
        return operation


# Name of the CompositionMob of the export of the Live set.
DEFAULT_COMPOSITION_NAME = "Exported from Ableton"
