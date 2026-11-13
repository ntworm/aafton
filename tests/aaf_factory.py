"""Constroi AAF minimos e controlados para os testes do leitor.

Nao usa aafton.aaf_write de proposito: se o leitor fosse testado contra o
proprio escritor do projeto, um erro simetrico nos dois passaria despercebido.

Notas de API (verificadas 2026-08-20):
- Timecode.__init__(fps, drop, length) nao aceita start= como kwarg.
  Usar tc.start = valor depois de criar.
- Transition exige 'OperationGroup' (StrongRef) e 'CutPoint' como propriedades
  obrigatorias. Usar trans['OperationGroup'].value = op_group e trans.cutpoint = n.
- CommentMarker nao tem atributos 'position' nem 'comment' Python. A propriedade
  AAF se chama 'Position': marker['Position'].value = posicao.
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path
from typing import Any

import aaf2

SAMPLE_RATE = 48000


def build_calibration_aaf(
    path: str | Path, tracks: list[dict], *, frames: int, start_timecode_frames: int = 90000
) -> Path:
    """AAF de calibracao diretamente da especificacao, sem escritor do produto.

    Cada track contem clips com path/start/duration/offset/gain. WAV stereo
    usa um combiner com fontes mono distintas; pares split ocupam duas tracks.
    Mobs Fade N referenciam WAV renderizados independentes, como Pro Tools.
    """
    import wave
    import hashlib
    from aaf2.auid import AUID

    path = Path(path)
    with aaf2.open(str(path), "w") as f:
        composition = f.create.CompositionMob("CALIBRACAO_AUDIO")
        f.content.mobs.append(composition)
        gain_parameter = f.create.ParameterDef(
            AUID("e4962321-2267-11d3-8a4c-0050040ef7d2"),
            "Amplitude",
            "",
            f.dictionary.lookup_typedef("Rational"),
        )
        f.dictionary["ParameterDefinitions"].append(gain_parameter)
        byte_order = f.create.ParameterDef(
            AUID("c0038672-a8cf-11d3-a05b-006094eb75cb"),
            "AvidParameterByteOrder",
            "",
            f.dictionary.lookup_typedef("aafInt16"),
        )
        effect_id = f.create.ParameterDef(
            AUID("93994bd6-a81d-11d3-a05b-006094eb75cb"),
            "AvidEffectID",
            "",
            f.dictionary.lookup_typedef("AvidBagOfBits"),
        )
        f.dictionary["ParameterDefinitions"].append(byte_order)
        f.dictionary["ParameterDefinitions"].append(effect_id)
        operations = {}
        for key, auid, name, inputs in (
            ("gain", "9d2ea894-0968-11d3-8a38-0050040ef7d2", "Audio Gain", 1),
            ("combiner", "6b46dd7a-132d-4856-ab21-8b751d8462ec", "Audio Channel Combiner", 1),
        ):
            op = f.create.OperationDef(AUID(auid), name, "")
            op["IsTimeWarp"].value = False
            op["NumberInputs"].value = inputs
            op["DataDefinition"].value = f.dictionary.lookup_datadef("Sound")
            if key == "gain":
                op["ParametersDefined"].value.append(gain_parameter)
            else:
                op["Bypass"].value = 0
                op["OperationCategory"].value = "OperationCategory_Effect"
                op["ParametersDefined"].value.extend([byte_order, effect_id])
            f.dictionary["OperationDefinitions"].append(op)
            operations[key] = op

        sources = {}
        for track in tracks:
            slot = composition.create_timeline_slot(edit_rate=SAMPLE_RATE)
            slot.name = track["name"]
            sequence = f.create.Sequence(media_kind="sound")
            slot.segment = sequence
            cursor = 0
            for clip in track["clips"]:
                source_path = Path(clip["path"]).resolve()
                source_key = (str(source_path), clip.get("mob_name"))
                if source_key not in sources:
                    with wave.open(str(source_path), "rb") as w:
                        channels, length, width = w.getnchannels(), w.getnframes(), w.getsampwidth()
                        pcm = w.readframes(length)
                    if channels not in (1, 2):
                        raise ValueError("calibracao exige mono ou stereo")
                    source_paths = [source_path]
                    if channels == 2:
                        directory = path.parent / f"{path.stem} Media"
                        directory.mkdir(exist_ok=True)
                        source_paths = []
                        for channel, suffix in enumerate(("L", "R")):
                            # Implementação própria da fábrica, sem helper do
                            # produto: um erro simétrico não serve de oráculo.
                            mono = b"".join(
                                pcm[i + channel * width : i + (channel + 1) * width]
                                for i in range(0, len(pcm), 2 * width)
                            )
                            digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
                            mono_path = directory / f"{digest}.{suffix}.wav"
                            if not mono_path.exists():
                                with wave.open(str(mono_path), "wb") as w:
                                    w.setparams(
                                        (1, width, SAMPLE_RATE, length, "NONE", "not compressed")
                                    )
                                    w.writeframes(mono)
                            else:
                                with wave.open(str(mono_path), "rb") as w:
                                    assert w.readframes(length) == mono
                            source_paths.append(mono_path.resolve())
                    masters = []
                    for media_path in source_paths:
                        metadata = {
                            "format": {"filename": str(media_path), "format_name": "wav"},
                            "streams": [
                                {
                                    "codec_type": "audio",
                                    "codec_name": f"pcm_s{width * 8}le",
                                    "sample_rate": SAMPLE_RATE,
                                    "duration_ts": length,
                                    "channels": 1,
                                }
                            ],
                        }
                        master, source_mob, _ = f.content.link_external_wav(metadata)
                        for mob in (master, source_mob):
                            for media_slot in mob.slots:
                                components = list(media_slot.segment.components)
                                assert len(components) == 1
                                media_slot.segment = components[0]
                        if clip.get("mob_name"):
                            master.name = clip["mob_name"]
                        masters.append(master)
                    sources[source_key] = masters, channels, length
                masters, channels, length = sources[source_key]
                start, duration, offset = clip["start"], clip["duration"], clip["offset"]
                if start < cursor or duration <= 0 or offset < 0 or offset + duration > length:
                    raise ValueError(f"spec de calibracao invalida: {track['name']}, {clip}")
                if start > cursor:
                    sequence.components.append(f.create.Filler("sound", length=start - cursor))
                inputs = []
                for channel in range(channels):
                    source = f.create.SourceClip(media_kind="sound", start=offset)
                    source.length = duration
                    source.mob = masters[channel]
                    source.slot_id = 1
                    inputs.append(source)
                segment = inputs[0]
                if channels == 2:
                    if not slot["TimelineMobAttributeList"].value:
                        slot["TimelineMobAttributeList"].append(
                            f.create.TaggedValue("_TRACK_FORMAT", 2, "aafInt32")
                        )
                    segment = f.create.OperationGroup(operations["combiner"])
                    segment.media_kind = "sound"
                    segment.length = duration
                    segment.segments.extend(inputs)
                    segment["ComponentAttributeList"].append(
                        f.create.TaggedValue("_IGNORE_TRACKING", 0, "aafInt32")
                    )
                    segment.parameters.append(f.create.ConstantValue(byte_order, 18761))
                    segment.parameters.append(
                        f.create.ConstantValue(effect_id, list(b"EFF2_AUDIO_CHANNEL_COMBINER\x00"))
                    )
                elif channels != 1:
                    raise ValueError("calibracao exige mono ou stereo")
                ratio = clip.get("gain", [1, 1])
                if ratio != [1, 1]:
                    group = f.create.OperationGroup(operations["gain"])
                    group.media_kind = "sound"
                    group.length = duration
                    group.segments.append(segment)
                    group.parameters.append(
                        f.create.ConstantValue(gain_parameter, aaf2.rational.AAFRational(*ratio))
                    )
                    segment = group
                sequence.components.append(segment)
                cursor = start + duration
            if cursor < frames:
                sequence.components.append(f.create.Filler("sound", length=frames - cursor))
            sequence.length = frames
        timecode_slot = composition.create_timeline_slot(edit_rate=25)
        timecode_slot.name = "TC"
        tc = f.create.Timecode(fps=25, drop=False)
        tc.start = start_timecode_frames
        tc.length = frames * 25 // SAMPLE_RATE
        timecode_slot.segment = tc
    return path


def wav_bytes(frames: int, channels: int = 1, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Um WAV PCM 16 bits de silencio, com cabecalho valido."""
    import struct

    data = b"\x00\x00" * frames * channels
    block_align = 2 * channels
    fmt = struct.pack(
        "<4sIHHIIHH",
        b"fmt ",
        16,
        1,
        channels,
        sample_rate,
        sample_rate * block_align,
        block_align,
        16,
    )
    body = fmt + struct.pack("<4sI", b"data", len(data)) + data
    return struct.pack("<4sI4s", b"RIFF", 4 + len(body), b"WAVE") + body


def build_linked_aaf(
    path: str | Path,
    clips: list[tuple[int, int, int]],
    wav_path: str | Path,
    track_name: str = "DIALOGO 1",
    start_timecode_frames: int | None = 90000,
    fps: int = 25,
    drop: bool = False,
    with_marker_at: int | None = None,
) -> Path:
    """Escreve um AAF com uma track de audio e clips linkados a um WAV externo.

    ``clips`` e uma lista de ternos ``(inicio_na_timeline, duracao, offset_na_fonte)``
    em samples. Lacunas entre clips viram Filler.

    ``with_marker_at`` aceita uma posicao em samples para inserir um CommentMarker.
    A propriedade AAF de posicao e 'Position' (capital P), nao 'position'.
    """
    path = Path(path)
    with aaf2.open(str(path), "w") as f:
        composition = f.create.CompositionMob("cena12")
        f.content.mobs.append(composition)

        meta = {
            "format": {"filename": str(wav_path), "format_name": "wav"},
            "streams": [
                {
                    "codec_type": "audio",
                    "codec_name": "pcm_s16le",
                    "sample_rate": SAMPLE_RATE,
                    "duration_ts": 48000 * 60,
                    "channels": 1,
                }
            ],
        }
        master_mob, _source_mob, _tape_mob = f.content.link_external_wav(meta)

        slot = composition.create_timeline_slot(edit_rate=SAMPLE_RATE)
        slot.name = track_name
        sequence = f.create.Sequence(media_kind="sound")
        slot.segment = sequence

        cursor = 0
        for start, duration, offset in clips:
            if start > cursor:
                sequence.components.append(f.create.Filler("sound", length=start - cursor))
                cursor = start
            source_clip = f.create.SourceClip(media_kind="sound", start=offset)
            source_clip.length = duration
            source_clip.mob = master_mob
            source_clip.slot_id = 1
            sequence.components.append(source_clip)
            cursor += duration

        if start_timecode_frames is not None:
            timecode_slot = composition.create_timeline_slot(
                edit_rate=f"{fps * 1000}/1001" if drop else fps
            )
            timecode_slot.name = "TC"
            tc = f.create.Timecode(fps=fps, drop=drop)
            tc.start = start_timecode_frames
            tc.length = cursor * fps // SAMPLE_RATE
            timecode_slot.segment = tc

        if with_marker_at is not None:
            marker_slot = composition.create_timeline_slot(edit_rate=SAMPLE_RATE)
            marker_slot.name = "Markers"
            eventos = f.create.Sequence(media_kind="DescriptiveMetadata")
            marker_slot.segment = eventos
            comentario = f.create.CommentMarker()
            comentario["Position"].value = with_marker_at
            comentario.length = 0
            eventos.components.append(comentario)

    return path


def build_aaf_with_transition(
    path: str | Path,
    wav_path: str | Path,
    first_duration: int = 96000,
    second_duration: int = 96000,
    transition_length: int = 24000,
) -> Path:
    """AAF com dois clips separados por uma Transition, ou seja, um crossfade.

    A Transition exige, no pyaaf2, um OperationGroup (referencia forte) e um
    CutPoint como propriedades obrigatorias. Usamos o AUID do 'Avid Audio Dissolve'
    (0c3bea41-fc05-11d2-8a29-0050040ef7d2) observado no fixture do Resolve.
    """
    from aaf2.auid import AUID

    path = Path(path)
    with aaf2.open(str(path), "w") as f:
        composition = f.create.CompositionMob("crossfade")
        f.content.mobs.append(composition)
        meta = {
            "format": {"filename": str(wav_path), "format_name": "wav"},
            "streams": [
                {
                    "codec_type": "audio",
                    "codec_name": "pcm_s16le",
                    "sample_rate": SAMPLE_RATE,
                    "duration_ts": 48000 * 60,
                    "channels": 1,
                }
            ],
        }
        master_mob, _s, _t = f.content.link_external_wav(meta)

        slot = composition.create_timeline_slot(edit_rate=SAMPLE_RATE)
        slot.name = "CROSSFADE"
        sequence = f.create.Sequence(media_kind="sound")
        slot.segment = sequence

        # Registrar OperationDef de dissolve (AUID observado no fixture do Resolve)
        dissolve_auid = AUID("0c3bea41-fc05-11d2-8a29-0050040ef7d2")
        op_def = f.create.OperationDef(dissolve_auid, "Avid Audio Dissolve", "")
        op_def["IsTimeWarp"].value = False
        op_def["NumberInputs"].value = 2
        op_def["DataDefinition"].value = f.dictionary.lookup_datadef("Sound")
        f.dictionary["OperationDefinitions"].append(op_def)

        primeiro = f.create.SourceClip(media_kind="sound", start=0)
        primeiro.length = first_duration
        primeiro.mob = master_mob
        primeiro.slot_id = 1
        sequence.components.append(primeiro)

        fade_op = f.create.OperationGroup(op_def)
        fade_op.media_kind = "sound"
        fade_op.length = transition_length

        transicao = f.create.Transition(media_kind="sound", length=transition_length)
        transicao.cutpoint = transition_length
        transicao["OperationGroup"].value = fade_op
        sequence.components.append(transicao)

        segundo = f.create.SourceClip(media_kind="sound", start=0)
        segundo.length = second_duration
        segundo.mob = master_mob
        segundo.slot_id = 1
        sequence.components.append(segundo)
    return path


def build_aaf_with_gain(
    path: str | Path,
    wav_path: str | Path,
    gain: tuple[int, int] = (1, 2),
    duration: int = 96000,
) -> Path:
    """AAF com um SourceClip embrulhado num OperationGroup de ganho constante.

    ``gain`` e o valor racional (numerador, denominador). (1, 2) e metade da
    amplitude, ou seja cerca de -6 dB.
    """
    from aaf2.auid import AUID

    path = Path(path)
    with aaf2.open(str(path), "w") as f:
        composition = f.create.CompositionMob("ganho")
        f.content.mobs.append(composition)
        meta = {
            "format": {"filename": str(wav_path), "format_name": "wav"},
            "streams": [
                {
                    "codec_type": "audio",
                    "codec_name": "pcm_s16le",
                    "sample_rate": SAMPLE_RATE,
                    "duration_ts": 48000 * 60,
                    "channels": 1,
                }
            ],
        }
        master_mob, _s, _t = f.content.link_external_wav(meta)

        slot = composition.create_timeline_slot(edit_rate=SAMPLE_RATE)
        slot.name = "COM GANHO"
        sequence = f.create.Sequence(media_kind="sound")
        slot.segment = sequence

        source_clip = f.create.SourceClip(media_kind="sound", start=0)
        source_clip.length = duration
        source_clip.mob = master_mob
        source_clip.slot_id = 1

        param_auid = AUID("e4962321-2267-11d3-8a4c-0050040ef7d2")
        param_def = f.create.ParameterDef(
            param_auid, "Amplitude", "", f.dictionary.lookup_typedef("Rational")
        )
        f.dictionary["ParameterDefinitions"].append(param_def)

        gain_auid = AUID("9d2ea894-0968-11d3-8a38-0050040ef7d2")
        op_def = f.create.OperationDef(gain_auid, "Audio Gain", "")
        op_def["IsTimeWarp"].value = False
        op_def["NumberInputs"].value = 2
        op_def["DataDefinition"].value = f.dictionary.lookup_datadef("Sound")
        op_def["ParametersDefined"].value.append(param_def)
        f.dictionary["OperationDefinitions"].append(op_def)

        operation = f.create.OperationGroup(op_def)
        operation.media_kind = "sound"
        operation.length = duration
        operation.segments.append(source_clip)

        parametro = f.create.ConstantValue(param_def, aaf2.rational.AAFRational(*gain))
        operation.parameters.append(parametro)

        sequence.components.append(operation)
    return path


def build_aaf_with_varying_gain(
    path: str | Path,
    wav_path: str | Path,
    points: list[tuple[tuple[int, int], tuple[int, int]]],
    duration: int = 96000,
) -> Path:
    """AAF com um SourceClip dentro de um OperationGroup com VaryingValue.

    ``points`` e uma lista de ternos/pares [((t_num, t_den), (v_num, v_den))].
    """
    from aaf2.auid import AUID
    from aaf2.rational import AAFRational

    path = Path(path)
    with aaf2.open(str(path), "w") as f:
        composition = f.create.CompositionMob("ganho_variavel")
        f.content.mobs.append(composition)
        meta = {
            "format": {"filename": str(wav_path), "format_name": "wav"},
            "streams": [
                {
                    "codec_type": "audio",
                    "codec_name": "pcm_s16le",
                    "sample_rate": SAMPLE_RATE,
                    "duration_ts": 48000 * 60,
                    "channels": 1,
                }
            ],
        }
        master_mob, _s, _t = f.content.link_external_wav(meta)

        slot = composition.create_timeline_slot(edit_rate=SAMPLE_RATE)
        slot.name = "COM GANHO VARIAVEL"
        sequence = f.create.Sequence(media_kind="sound")
        slot.segment = sequence

        source_clip = f.create.SourceClip(media_kind="sound", start=0)
        source_clip.length = duration
        source_clip.mob = master_mob
        source_clip.slot_id = 1

        param_auid = AUID("e4962321-2267-11d3-8a4c-0050040ef7d2")
        param_def = f.create.ParameterDef(
            param_auid, "Amplitude", "", f.dictionary.lookup_typedef("Rational")
        )
        f.dictionary["ParameterDefinitions"].append(param_def)

        interp_auid = AUID("5b7199c0-6744-11d2-8326-00609719b0fb")
        interp_def = f.create.InterpolationDef(interp_auid, "Linear", "")
        f.dictionary["InterpolationDefinitions"].append(interp_def)

        gain_auid = AUID("9d2ea894-0968-11d3-8a38-0050040ef7d2")
        op_def = f.create.OperationDef(gain_auid, "Audio Gain", "")
        op_def["IsTimeWarp"].value = False
        op_def["NumberInputs"].value = 2
        op_def["DataDefinition"].value = f.dictionary.lookup_datadef("Sound")
        op_def["ParametersDefined"].value.append(param_def)
        f.dictionary["OperationDefinitions"].append(op_def)

        operation = f.create.OperationGroup(op_def)
        operation.media_kind = "sound"
        operation.length = duration
        operation.segments.append(source_clip)

        vv = f.create.VaryingValue(param_def, interp_def)
        for (t_num, t_den), (v_num, v_den) in points:
            cp = f.create.ControlPoint()
            cp["Time"].value = AAFRational(t_num, t_den)
            cp["Value"].value = AAFRational(v_num, v_den)
            vv["PointList"].append(cp)

        operation.parameters.append(vv)
        sequence.components.append(operation)
    return path


def build_aaf_with_rendered_fades(
    path: str | Path,
    wav_path: str | Path,
    sequence_spec: list[tuple],
    track_name: str = "AUDIO 1",
    start_timecode_frames: int | None = 90000,
    fps: int = 25,
) -> Path:
    """AAF com sequencia contendo clips, fillers e segmentos de fade renderizados (Fade \\d+).

    sequence_spec e uma lista de tuplas:
    - ('filler', length)
    - ('clip', duration, offset, [mob_name])
    - ('fade', duration, fade_name)
    """
    path = Path(path)
    with aaf2.open(str(path), "w") as f:
        composition = f.create.CompositionMob("fades_session")
        f.content.mobs.append(composition)

        meta = {
            "format": {"filename": str(Path(wav_path).resolve()), "format_name": "wav"},
            "streams": [
                {
                    "codec_type": "audio",
                    "codec_name": "pcm_s16le",
                    "sample_rate": SAMPLE_RATE,
                    "duration_ts": 48000 * 60,
                    "channels": 1,
                }
            ],
        }
        master_mob, _s, _t = f.content.link_external_wav(meta)

        slot = composition.create_timeline_slot(edit_rate=SAMPLE_RATE)
        slot.name = track_name
        sequence = f.create.Sequence(media_kind="sound")
        slot.segment = sequence

        fade_mobs: dict[str, Any] = {}
        cursor = 0

        for item in sequence_spec:
            kind = item[0]
            if kind == "filler":
                length = item[1]
                filler = f.create.Filler("sound", length=length)
                sequence.components.append(filler)
                cursor += length
            elif kind == "clip":
                duration = item[1]
                offset = item[2]
                clip_mob = master_mob
                if len(item) > 3 and item[3]:
                    mob_name = item[3]
                    if mob_name not in fade_mobs:
                        m = f.create.MasterMob(mob_name)
                        f.content.mobs.append(m)
                        fade_mobs[mob_name] = m
                    clip_mob = fade_mobs[mob_name]
                sc = f.create.SourceClip(media_kind="sound", start=offset)
                sc.length = duration
                sc.mob = clip_mob
                sc.slot_id = 1
                sequence.components.append(sc)
                cursor += duration
            elif kind == "fade":
                duration = item[1]
                fade_name = item[2] if len(item) > 2 else "Fade 1"
                if fade_name not in fade_mobs:
                    m = f.create.MasterMob(fade_name)
                    f.content.mobs.append(m)
                    fade_mobs[fade_name] = m
                sc = f.create.SourceClip(media_kind="sound", start=0)
                sc.length = duration
                sc.mob = fade_mobs[fade_name]
                sc.slot_id = 1
                sequence.components.append(sc)
                cursor += duration

        if start_timecode_frames is not None:
            timecode_slot = composition.create_timeline_slot(edit_rate=fps)
            timecode_slot.name = "TC"
            tc = f.create.Timecode(fps=fps, drop=False)
            tc.start = start_timecode_frames
            tc.length = cursor * fps // SAMPLE_RATE if SAMPLE_RATE else 0
            timecode_slot.segment = tc

    return path


def _wav_summary_header(channels: int = 1, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Cabecalho RIFF minimo para o campo WAVEDescriptor.Summary.

    O Summary e um snapshot do inicio do RIFF, nao o arquivo completo.
    Apenas o chunk fmt e necessario; os dados ficam no EssenceData stream.
    """
    import struct

    block_align = 2 * channels
    fmt = struct.pack(
        "<4sIHHIIHH",
        b"fmt ",
        16,
        1,
        channels,
        sample_rate,
        sample_rate * block_align,
        block_align,
        16,
    )
    body = b"WAVE" + fmt
    return struct.pack("<4sI", b"RIFF", len(body)) + body


def build_embedded_aaf(
    path: "Path | str",
    frames: int = SAMPLE_RATE,
    track_name: str = "DIALOGO",
    start_timecode_frames: int = 90000,
    fps: int = 25,
    clip_frames: "int | None" = None,
    sample_rate: int = SAMPLE_RATE,
    mob_name: str = "fonte",
    embed_as_riff: bool = True,
) -> Path:
    """Escreve um AAF com a essencia WAV embedded (sem Locator externo).

    O SourceMob recebe o sufixo '<SOURCE MOB>' no nome (convencao pyaaf2),
    garantindo que o MasterMob (nome = mob_name) e o SourceMob tenham nomes
    distintos. Isso e a condicao que expoe o defeito de busca por nome:
    qualquer buscador que use o nome do asset para localizar o SourceMob vai
    falhar, porque os nomes nunca coincidem.

    Se embed_as_riff=True (padrao), a essencia e um RIFF WAV completo, como
    o Pro Tools gera ao consolidar. Se embed_as_riff=False, a essencia e PCM
    puro (outro dialeto possivel).
    """
    import aaf2
    import aaf2.rational

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if clip_frames is None:
        clip_frames = frames // 2

    if embed_as_riff:
        essence_bytes = wav_bytes(frames, channels=1, sample_rate=sample_rate)
    else:
        essence_bytes = b"\x00\x00" * frames

    summary = _wav_summary_header(channels=1, sample_rate=sample_rate)

    with aaf2.open(str(path), "w") as f:
        composition = f.create.CompositionMob("cena12")
        f.content.mobs.append(composition)

        master = f.create.MasterMob(mob_name)
        f.content.mobs.append(master)

        descritor = f.create.WAVEDescriptor()
        descritor["SampleRate"].value = aaf2.rational.AAFRational(sample_rate, 1)
        descritor["Length"].value = frames
        descritor["Summary"].value = list(summary)

        source = f.create.SourceMob()
        # Nome propositalmente diferente do MasterMob para provar que a
        # extracao nao depende de casamento por nome.
        source.name = mob_name + " <SOURCE MOB>"
        source["EssenceDescription"].value = descritor
        f.content.mobs.append(source)

        ed = f.create.EssenceData()
        ed.mob = source
        stream = ed.open("w")
        stream.write(essence_bytes)
        stream.close()
        f.content.essencedata.append(ed)

        sslot = source.create_timeline_slot(edit_rate=sample_rate)
        sslot.slot_id = 1
        sslot.name = "A1"
        sslot.segment = f.create.Filler("sound", length=frames)

        mslot = master.create_timeline_slot(edit_rate=sample_rate)
        mslot.slot_id = 1
        mslot.name = "A1"
        sc_src = f.create.SourceClip(media_kind="sound", start=0)
        sc_src.length = frames
        sc_src.mob = source
        sc_src.slot_id = 1
        mslot.segment = sc_src

        cslot = composition.create_timeline_slot(edit_rate=sample_rate)
        cslot.name = track_name
        seq = f.create.Sequence(media_kind="sound")
        cslot.segment = seq

        sc_master = f.create.SourceClip(media_kind="sound", start=0)
        sc_master.length = clip_frames
        sc_master.mob = master
        sc_master.slot_id = 1
        seq.components.append(sc_master)

        if start_timecode_frames is not None:
            tc_slot = composition.create_timeline_slot(edit_rate=fps)
            tc_slot.name = "TC"
            tc = f.create.Timecode(fps=fps, drop=False)
            tc.start = start_timecode_frames
            tc.length = max(1, clip_frames * fps // sample_rate)
            tc_slot.segment = tc

    return path


def build_protools_synthetic_aaf(
    path: str | Path,
    wav_path: str | Path,
    track_name: str = "AUDIO 1",
    start_timecode_frames: int = 90000,
    fps: int = 25,
    sample_rate: int = SAMPLE_RATE,
    use_combiner: bool = True,
    rendered_fades: bool = True,
) -> Path:
    """Dialeto Pro Tools: Combiner stereo multi-canal e rendered fades."""
    from aaf2.auid import AUID

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wav_path = Path(wav_path).resolve()

    with aaf2.open(str(path), "w") as f:
        composition = f.create.CompositionMob("ProTools_Session")
        composition.usage = "Usage_TopLevel"
        f.content.mobs.append(composition)

        meta = {
            "format": {"filename": str(wav_path), "format_name": "wav"},
            "streams": [
                {
                    "codec_type": "audio",
                    "codec_name": "pcm_s16le",
                    "sample_rate": sample_rate,
                    "duration_ts": sample_rate * 60,
                    "channels": 1,
                }
            ],
        }
        master_mob, _s, _t = f.content.link_external_wav(meta)

        byte_order = f.create.ParameterDef(
            AUID("c0038672-a8cf-11d3-a05b-006094eb75cb"),
            "AvidParameterByteOrder",
            "",
            f.dictionary.lookup_typedef("aafInt16"),
        )
        effect_id = f.create.ParameterDef(
            AUID("93994bd6-a81d-11d3-a05b-006094eb75cb"),
            "AvidEffectID",
            "",
            f.dictionary.lookup_typedef("AvidBagOfBits"),
        )
        f.dictionary["ParameterDefinitions"].append(byte_order)
        f.dictionary["ParameterDefinitions"].append(effect_id)

        combiner_op = f.create.OperationDef(
            AUID("6b46dd7a-132d-4856-ab21-8b751d8462ec"), "Audio Channel Combiner", ""
        )
        combiner_op["IsTimeWarp"].value = False
        combiner_op["NumberInputs"].value = 2
        combiner_op["DataDefinition"].value = f.dictionary.lookup_datadef("Sound")
        combiner_op["Bypass"].value = 0
        combiner_op["OperationCategory"].value = "OperationCategory_Effect"
        combiner_op["ParametersDefined"].value.extend([byte_order, effect_id])
        f.dictionary["OperationDefinitions"].append(combiner_op)

        slot = composition.create_timeline_slot(edit_rate=sample_rate)
        slot.name = track_name
        if use_combiner:
            slot["TimelineMobAttributeList"].append(
                f.create.TaggedValue("_TRACK_FORMAT", 2, "aafInt32")
            )
        sequence = f.create.Sequence(media_kind="sound")
        slot.segment = sequence

        fade_mob = None
        if rendered_fades:
            fade_mob = f.create.MasterMob("Fade 1")
            f.content.mobs.append(fade_mob)

        dur1 = sample_rate
        if use_combiner:
            in_l = f.create.SourceClip(media_kind="sound", start=0)
            in_l.length = dur1
            in_l.mob = master_mob
            in_l.slot_id = 1
            in_r = f.create.SourceClip(media_kind="sound", start=0)
            in_r.length = dur1
            in_r.mob = master_mob
            in_r.slot_id = 1
            comb_grp = f.create.OperationGroup(combiner_op)
            comb_grp.media_kind = "sound"
            comb_grp.length = dur1
            comb_grp.segments.extend([in_l, in_r])
            comb_grp.parameters.append(f.create.ConstantValue(byte_order, 18761))
            comb_grp.parameters.append(
                f.create.ConstantValue(effect_id, list(b"EFF2_AUDIO_CHANNEL_COMBINER\x00"))
            )
            sequence.components.append(comb_grp)
        else:
            sc = f.create.SourceClip(media_kind="sound", start=0)
            sc.length = dur1
            sc.mob = master_mob
            sc.slot_id = 1
            sequence.components.append(sc)

        fade_len = sample_rate // 20
        if rendered_fades and fade_mob:
            fade_sc = f.create.SourceClip(media_kind="sound", start=0)
            fade_sc.length = fade_len
            fade_sc.mob = fade_mob
            fade_sc.slot_id = 1
            sequence.components.append(fade_sc)

        dur2 = sample_rate
        if use_combiner:
            in_l2 = f.create.SourceClip(media_kind="sound", start=dur1)
            in_l2.length = dur2
            in_l2.mob = master_mob
            in_l2.slot_id = 1
            in_r2 = f.create.SourceClip(media_kind="sound", start=dur1)
            in_r2.length = dur2
            in_r2.mob = master_mob
            in_r2.slot_id = 1
            comb_grp2 = f.create.OperationGroup(combiner_op)
            comb_grp2.media_kind = "sound"
            comb_grp2.length = dur2
            comb_grp2.segments.extend([in_l2, in_r2])
            comb_grp2.parameters.append(f.create.ConstantValue(byte_order, 18761))
            comb_grp2.parameters.append(
                f.create.ConstantValue(effect_id, list(b"EFF2_AUDIO_CHANNEL_COMBINER\x00"))
            )
            sequence.components.append(comb_grp2)
        else:
            sc2 = f.create.SourceClip(media_kind="sound", start=dur1)
            sc2.length = dur2
            sc2.mob = master_mob
            sc2.slot_id = 1
            sequence.components.append(sc2)

        tc_slot = composition.create_timeline_slot(edit_rate=fps)
        tc_slot.name = "TC"
        tc = f.create.Timecode(fps=fps, drop=False)
        tc.start = start_timecode_frames
        total_len = dur1 + (fade_len if rendered_fades else 0) + dur2
        tc.length = total_len * fps // sample_rate
        tc_slot.segment = tc

    return path


def build_resolve_synthetic_aaf(
    path: str | Path,
    wav_path: str | Path,
    track_name: str = "Audio 1",
    edit_rate: Fraction | int = 24,
    start_timecode_frames: int = 86400,
    sample_rate: int = SAMPLE_RATE,
    include_video_slot: bool = True,
    include_transition: bool = True,
) -> Path:
    """Dialeto DaVinci Resolve: Slots com frame edit rates (24, 25 fps), video slot e transition."""
    from aaf2.auid import AUID
    from aaf2.rational import AAFRational

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wav_path = Path(wav_path).resolve()

    from aafton.timecode import exact_edit_rate

    rate_frac = exact_edit_rate(edit_rate)
    fps_nominal = round(rate_frac)

    with aaf2.open(str(path), "w") as f:
        composition = f.create.CompositionMob("Resolve_Timeline")
        composition.usage = "Usage_TopLevel"
        f.content.mobs.append(composition)

        meta = {
            "format": {"filename": str(wav_path), "format_name": "wav"},
            "streams": [
                {
                    "codec_type": "audio",
                    "codec_name": "pcm_s16le",
                    "sample_rate": sample_rate,
                    "duration_ts": sample_rate * 60,
                    "channels": 1,
                }
            ],
        }
        master_mob, _s, _t = f.content.link_external_wav(meta)

        if include_video_slot:
            vslot = composition.create_timeline_slot(
                edit_rate=AAFRational(rate_frac.numerator, rate_frac.denominator)
            )
            vslot.name = "Video 1"
            vseq = f.create.Sequence(media_kind="picture")
            vfiller = f.create.Filler("picture", length=fps_nominal * 4)
            vseq.components.append(vfiller)
            vslot.segment = vseq

        dissolve_auid = AUID("0c3bea41-fc05-11d2-8a29-0050040ef7d2")
        op_def = f.create.OperationDef(dissolve_auid, "Avid Audio Dissolve", "")
        op_def["IsTimeWarp"].value = False
        op_def["NumberInputs"].value = 2
        op_def["DataDefinition"].value = f.dictionary.lookup_datadef("Sound")
        f.dictionary["OperationDefinitions"].append(op_def)

        aslot = composition.create_timeline_slot(
            edit_rate=AAFRational(rate_frac.numerator, rate_frac.denominator)
        )
        aslot.name = track_name
        aseq = f.create.Sequence(media_kind="sound")
        aslot.segment = aseq

        clip1_frames = fps_nominal * 2
        sc1 = f.create.SourceClip(media_kind="sound", start=0)
        sc1.length = clip1_frames
        sc1.mob = master_mob
        sc1.slot_id = 1
        aseq.components.append(sc1)

        if include_transition:
            trans_len = fps_nominal // 2
            fade_op = f.create.OperationGroup(op_def)
            fade_op.media_kind = "sound"
            fade_op.length = trans_len

            trans = f.create.Transition(media_kind="sound", length=trans_len)
            trans.cutpoint = trans_len
            trans["OperationGroup"].value = fade_op
            aseq.components.append(trans)

        clip2_frames = fps_nominal * 2
        sc2 = f.create.SourceClip(media_kind="sound", start=sample_rate * 2)
        sc2.length = clip2_frames
        sc2.mob = master_mob
        sc2.slot_id = 1
        aseq.components.append(sc2)

        tc_slot = composition.create_timeline_slot(
            edit_rate=AAFRational(rate_frac.numerator, rate_frac.denominator)
        )
        tc_slot.name = "TC"
        tc = f.create.Timecode(fps=fps_nominal, drop=False)
        tc.start = start_timecode_frames
        tc.length = clip1_frames + clip2_frames
        tc_slot.segment = tc

    return path


def build_premiere_synthetic_aaf(
    path: str | Path,
    wav_path: str | Path,
    track_name: str = "Audio 1",
    start_timecode_frames: int = 90000,
    fps: int = 25,
    sample_rate: int = SAMPLE_RATE,
    auxiliary_sequences_count: int = 2,
) -> Path:
    """Dialeto Adobe Premiere: Multiplas CompositionMobs auxiliares precedendo a principal, NetworkLocators e Mono Audio Pan."""
    from aaf2.auid import AUID

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wav_path = Path(wav_path).resolve()

    with aaf2.open(str(path), "w") as f:
        for i in range(auxiliary_sequences_count):
            aux_mob = f.create.CompositionMob(f"Aux_Sequence_{i + 1}")
            f.content.mobs.append(aux_mob)
            aux_slot = aux_mob.create_timeline_slot(edit_rate=sample_rate)
            aux_slot.name = f"Aux_Track_{i + 1}"
            aux_seq = f.create.Sequence(media_kind="sound")
            aux_seq.components.append(f.create.Filler("sound", length=100))
            aux_slot.segment = aux_seq

        main_mob = f.create.CompositionMob("Main_Sequence")
        main_mob.usage = "Usage_TopLevel"
        f.content.mobs.append(main_mob)

        meta = {
            "format": {"filename": str(wav_path), "format_name": "wav"},
            "streams": [
                {
                    "codec_type": "audio",
                    "codec_name": "pcm_s16le",
                    "sample_rate": sample_rate,
                    "duration_ts": sample_rate * 60,
                    "channels": 1,
                }
            ],
        }
        master_mob, source_mob, _t = f.content.link_external_wav(meta)

        desc = source_mob.descriptor
        has_net_loc = any(
            type(loc).__name__ == "NetworkLocator"
            for loc in (desc.get("Locator").value if desc.get("Locator") else [])
        )
        if not has_net_loc:
            loc = f.create.NetworkLocator()
            loc["URLString"].value = wav_path.as_uri()
            desc["Locator"].append(loc)

        pan_auid = AUID("9d2ea893-0968-11d3-8a38-0050040ef7d2")
        op_def = f.create.OperationDef(pan_auid, "Mono Audio Pan", "")
        op_def["IsTimeWarp"].value = False
        op_def["NumberInputs"].value = 1
        op_def["DataDefinition"].value = f.dictionary.lookup_datadef("Sound")
        f.dictionary["OperationDefinitions"].append(op_def)

        slot1 = main_mob.create_timeline_slot(edit_rate=sample_rate)
        slot1.name = track_name
        seq1 = f.create.Sequence(media_kind="sound")

        sc1 = f.create.SourceClip(media_kind="sound", start=0)
        sc1.length = sample_rate * 2
        sc1.mob = master_mob
        sc1.slot_id = 1
        seq1.components.append(sc1)

        pan_op = f.create.OperationGroup(op_def)
        pan_op.media_kind = "sound"
        pan_op.length = sample_rate * 2
        pan_op.segments.append(seq1)
        slot1.segment = pan_op

        slot2 = main_mob.create_timeline_slot(edit_rate=sample_rate)
        slot2.name = "Audio 2"
        seq2 = f.create.Sequence(media_kind="sound")
        sc2 = f.create.SourceClip(media_kind="sound", start=0)
        sc2.length = sample_rate * 2
        sc2.mob = master_mob
        sc2.slot_id = 1
        seq2.components.append(sc2)
        slot2.segment = seq2

        tc_slot = main_mob.create_timeline_slot(edit_rate=fps)
        tc_slot.name = "TC"
        tc = f.create.Timecode(fps=fps, drop=False)
        tc.start = start_timecode_frames
        tc.length = fps * 2
        tc_slot.segment = tc

    return path


def build_embedded_dialect_aaf(
    path: str | Path,
    sample_rate: int = 48000,
    embed_as_riff: bool = True,
    duration_frames: int | None = None,
    track_name: str = "EMBEDDED",
    start_timecode_frames: int = 90000,
    fps: int = 25,
    mob_name: str = "embedded_essence",
) -> Path:
    """Dialeto de audio embutido (RIFF WAV ou PCM puro) em 44.1k, 48k ou 96k."""
    if duration_frames is None:
        duration_frames = sample_rate * 2

    return build_embedded_aaf(
        path=path,
        frames=duration_frames,
        track_name=track_name,
        start_timecode_frames=start_timecode_frames,
        fps=fps,
        clip_frames=duration_frames,
        sample_rate=sample_rate,
        mob_name=mob_name,
        embed_as_riff=embed_as_riff,
    )


def build_fractional_rate_synthetic_aaf(
    path: str | Path,
    wav_path: str | Path,
    fps_num: int = 24000,
    fps_den: int = 1001,
    drop_frame: bool = False,
    start_timecode_frames: int = 86400,
    sample_rate: int = SAMPLE_RATE,
    track_name: str = "FRAC_AUDIO",
) -> Path:
    """Dialeto com taxas fracionarias (23.976, 29.97 NDF/DF, 59.94)."""
    from fractions import Fraction
    from aaf2.rational import AAFRational

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wav_path = Path(wav_path).resolve()

    with aaf2.open(str(path), "w") as f:
        composition = f.create.CompositionMob("Frac_Session")
        composition.usage = "Usage_TopLevel"
        f.content.mobs.append(composition)

        meta = {
            "format": {"filename": str(wav_path), "format_name": "wav"},
            "streams": [
                {
                    "codec_type": "audio",
                    "codec_name": "pcm_s16le",
                    "sample_rate": sample_rate,
                    "duration_ts": sample_rate * 60,
                    "channels": 1,
                }
            ],
        }
        master_mob, _s, _t = f.content.link_external_wav(meta)

        slot = composition.create_timeline_slot(edit_rate=sample_rate)
        slot.name = track_name
        seq = f.create.Sequence(media_kind="sound")
        slot.segment = seq

        sc = f.create.SourceClip(media_kind="sound", start=0)
        sc.length = sample_rate * 2
        sc.mob = master_mob
        sc.slot_id = 1
        seq.components.append(sc)

        fps_nominal = round(Fraction(fps_num, fps_den))
        tc_slot = composition.create_timeline_slot(edit_rate=AAFRational(fps_num, fps_den))
        tc_slot.name = "TC"
        tc = f.create.Timecode(fps=fps_nominal, drop=drop_frame)
        tc.start = start_timecode_frames
        tc.length = fps_nominal * 2
        tc_slot.segment = tc

    return path


def build_long_session_synthetic_aaf(
    path: str | Path,
    wav_path: str | Path,
    duration_seconds: int = 7200,
    start_timecode_frames: int = -250,
    fps: int = 25,
    sample_rate: int = SAMPLE_RATE,
    track_name: str = "LONG_SESSION",
) -> Path:
    """Dialeto de sessao longa (2h+ / 7200s+) e timecode negativo / pre-roll."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wav_path = Path(wav_path).resolve()

    with aaf2.open(str(path), "w") as f:
        composition = f.create.CompositionMob("Long_Session")
        composition.usage = "Usage_TopLevel"
        f.content.mobs.append(composition)

        meta = {
            "format": {"filename": str(wav_path), "format_name": "wav"},
            "streams": [
                {
                    "codec_type": "audio",
                    "codec_name": "pcm_s16le",
                    "sample_rate": sample_rate,
                    "duration_ts": sample_rate * 10,
                    "channels": 1,
                }
            ],
        }
        master_mob, _s, _t = f.content.link_external_wav(meta)

        slot = composition.create_timeline_slot(edit_rate=sample_rate)
        slot.name = track_name
        seq = f.create.Sequence(media_kind="sound")
        slot.segment = seq

        sc1 = f.create.SourceClip(media_kind="sound", start=0)
        sc1.length = sample_rate
        sc1.mob = master_mob
        sc1.slot_id = 1
        seq.components.append(sc1)

        gap_samples = duration_seconds * sample_rate - sample_rate
        if gap_samples > 0:
            seq.components.append(f.create.Filler("sound", length=gap_samples))

        sc2 = f.create.SourceClip(media_kind="sound", start=0)
        sc2.length = sample_rate
        sc2.mob = master_mob
        sc2.slot_id = 1
        seq.components.append(sc2)

        tc_slot = composition.create_timeline_slot(edit_rate=fps)
        tc_slot.name = "TC"
        tc = f.create.Timecode(fps=fps, drop=False)
        tc.start = start_timecode_frames
        tc.length = (duration_seconds + 1) * fps
        tc_slot.segment = tc

    return path
