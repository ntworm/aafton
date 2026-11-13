"""Corpus externo: os dois defeitos da importacao de AAF para .als.

Os AAFs aqui sao sinteticos, montados com pyaaf2 no mesmo desenho de
build_linked_aaf: um combiner com os dois canais apontando para o mesmo
arquivo estereo, e uma Sequence aninhada dentro da Sequence da track. O
objetivo e o mesmo do corpus de referencia: o .als nao pode ficar com clips
duplicados e sobrepostos, e uma Sequence aninhada nao pode virar erro.
"""

from __future__ import annotations

import gzip
from pathlib import Path

import aaf2
from aaf2.auid import AUID

from aafton.aaf_read import read_aaf
from aafton.cli import main
from aafton.media_extract import resolve_asset
from aafton.report import Report
from aafton.stereo import process_stereo_pairing
from tests.aaf_factory import build_linked_aaf, wav_bytes

SAMPLE_RATE = 48000
COMBINER_AUID = "6b46dd7a-132d-4856-ab21-8b751d8462ec"
TEMPLATE = Path(__file__).resolve().parents[2] / "templates" / "live12.als"
CLIPS = [(0, 48000, 0), (96000, 48000, 0)]


def _master_mob(f, wav_path, channels):
    """Liga um WAV externo ao AAF e devolve o MasterMob."""
    meta = {
        "format": {"filename": str(wav_path), "format_name": "wav"},
        "streams": [
            {
                "codec_type": "audio",
                "codec_name": "pcm_s16le",
                "sample_rate": SAMPLE_RATE,
                "duration_ts": SAMPLE_RATE * 60,
                "channels": channels,
            }
        ],
    }
    master_mob, _source_mob, _tape_mob = f.content.link_external_wav(meta)
    return master_mob


def _operation_combiner(f):
    """Registra o OperationDef do Audio Channel Combiner."""
    op_def = f.create.OperationDef(AUID(COMBINER_AUID), "Audio Channel Combiner", "")
    op_def["IsTimeWarp"].value = False
    op_def["NumberInputs"].value = 1
    op_def["DataDefinition"].value = f.dictionary.lookup_datadef("Sound")
    op_def["Bypass"].value = 0
    op_def["OperationCategory"].value = "OperationCategory_Effect"
    f.dictionary["OperationDefinitions"].append(op_def)
    return op_def


def _source_clip(f, mob, duration, offset, slot_id=1):
    """SourceClip de audio que aponta para o slot (canal) do mob."""
    source = f.create.SourceClip(media_kind="sound", start=offset)
    source.length = duration
    source.mob = mob
    source.slot_id = slot_id
    return source


def _combiner_aaf(path, wav_path, clips, offset_dir=0, slot_dir=2):
    """AAF com um combiner por evento, com os dois canais no mesmo arquivo.

    Cada canal ganha o seu proprio SourceMob, como o Resolve grava: os dois
    apontam para o mesmo WAV, mas o leitor ve duas midias diferentes.
    """
    with aaf2.open(str(path), "w") as f:
        composition = f.create.CompositionMob("stereo")
        f.content.mobs.append(composition)
        op_def = _operation_combiner(f)
        mob_esq = _master_mob(f, wav_path, 2)
        mob_dir = _master_mob(f, wav_path, 2)
        slot = composition.create_timeline_slot(edit_rate=SAMPLE_RATE)
        slot.name = "Audio 1"
        sequence = f.create.Sequence(media_kind="sound")
        slot.segment = sequence
        cursor = 0
        for start, duration, offset in clips:
            if start > cursor:
                sequence.components.append(f.create.Filler("sound", length=start - cursor))
                cursor = start
            entradas = [
                _source_clip(f, mob_esq, duration, offset),
                _source_clip(f, mob_dir, duration, offset_dir, slot_dir),
            ]
            group = f.create.OperationGroup(op_def)
            group.media_kind = "sound"
            group.length = duration
            group.segments.extend(entradas)
            group["ComponentAttributeList"].append(
                f.create.TaggedValue("_IGNORE_TRACKING", 0, "aafInt32")
            )
            sequence.components.append(group)
            cursor = start + duration
        sequence.length = cursor
    return path


def _nested_aaf(path, wav_path, clips, length=None):
    """AAF com uma Sequence aninhada dentro da Sequence da track."""
    with aaf2.open(str(path), "w") as f:
        composition = f.create.CompositionMob("aninhado")
        f.content.mobs.append(composition)
        mob = _master_mob(f, wav_path, 1)
        slot = composition.create_timeline_slot(edit_rate=SAMPLE_RATE)
        slot.name = "AUDIO 1"
        sequence = f.create.Sequence(media_kind="sound")
        slot.segment = sequence
        aninhada = f.create.Sequence(media_kind="sound")
        cursor = 0
        for start, duration, offset in clips:
            if start > cursor:
                aninhada.components.append(f.create.Filler("sound", length=start - cursor))
                cursor = start
            aninhada.components.append(_source_clip(f, mob, duration, offset))
            cursor = start + duration
        aninhada.length = cursor if length is None else length
        sequence.components.append(aninhada)
        sequence.length = cursor
    return path


def _stereo_wav(tmp_path):
    """WAV estereo de cinco segundos."""
    wav = tmp_path / "stereo.wav"
    wav.write_bytes(wav_bytes(SAMPLE_RATE * 5, channels=2))
    return wav


def _sessao(aaf_path, tmp_path):
    """Le o AAF e resolve a midia, como a CLI faz antes de escrever o .als."""
    report = Report(strict=False)
    session = read_aaf(aaf_path, report)
    for asset in session.media:
        resolve_asset(asset, [tmp_path], report)
    return session, report


def _audio_clips(als_path):
    """Quantos AudioClip o .als tem."""
    bruto = als_path.read_bytes()
    if bruto[:2] == b"\x1f\x8b":
        bruto = gzip.decompress(bruto)
    texto = bruto.decode("utf-8", errors="replace")
    return texto.count("<AudioClip Id=")


def test_combiner_de_fonte_unica_gera_um_clip_so_e_exporta(tmp_path):
    """Os dois canais do mesmo arquivo estereo viram um clip so no .als."""
    wav = _stereo_wav(tmp_path)
    aaf = _combiner_aaf(tmp_path / "combiner.aaf", wav, CLIPS)
    als = tmp_path / "saida.als"

    codigo = main(["import", str(aaf), str(als), "--template", str(TEMPLATE), "--media", "link"])
    assert codigo == 0
    assert _audio_clips(als) == len(CLIPS)

    volta = tmp_path / "volta.aaf"
    assert main(["export", str(als), str(volta)]) == 0
    assert volta.exists()


def test_combiner_de_fonte_unica_avisa_o_collapse(tmp_path):
    """O relatorio diz quantos pares viraram um clip so."""
    wav = _stereo_wav(tmp_path)
    aaf = _combiner_aaf(tmp_path / "combiner.aaf", wav, CLIPS)
    session, report = _sessao(aaf, tmp_path)

    process_stereo_pairing(session, "pair", report, tmp_path / "par")

    track = session.tracks[0]
    assert track.channel_count == 2
    assert len(track.clips) == len(CLIPS)
    assert [c.channel_index for c in track.clips] == [0, 0]
    infos = [i for i in report.issues if i.code == "STEREO_SINGLE_SOURCE_COLLAPSED"]
    assert len(infos) == 1
    assert infos[0].context["count"] == len(CLIPS)


def test_par_com_offsets_diferentes_nao_colapsa(tmp_path):
    """Dois canais que cortam o arquivo em lugares diferentes ficam como esta."""
    wav = _stereo_wav(tmp_path)
    aaf = _combiner_aaf(tmp_path / "combiner.aaf", wav, CLIPS, offset_dir=24000)
    session, report = _sessao(aaf, tmp_path)

    process_stereo_pairing(session, "pair", report, tmp_path / "par")

    track = session.tracks[0]
    assert len(track.clips) == 2 * len(CLIPS)
    assert sorted(c.channel_index for c in track.clips) == [0, 0, 1, 1]
    assert not [i for i in report.issues if i.code == "STEREO_SINGLE_SOURCE_COLLAPSED"]


def test_leitor_guarda_o_canal_do_arquivo_de_cada_clip(tmp_path):
    """Os dois canais do combiner leem os canais 1 e 2 do WAV estereo."""
    wav = _stereo_wav(tmp_path)
    aaf = _combiner_aaf(tmp_path / "combiner.aaf", wav, CLIPS)
    session, _report = _sessao(aaf, tmp_path)

    canais = {(c.channel_index, c.source_channel) for c in session.tracks[0].clips}
    assert canais == {(0, 1), (1, 2)}


def test_par_que_le_o_mesmo_canal_nao_colapsa(tmp_path):
    """Dual mono (os dois lados lendo o canal 1) nao vira estereo L/R."""
    wav = _stereo_wav(tmp_path)
    aaf = _combiner_aaf(tmp_path / "combiner.aaf", wav, CLIPS, slot_dir=1)
    session, report = _sessao(aaf, tmp_path)

    process_stereo_pairing(session, "pair", report, tmp_path / "par")

    assert len(session.tracks[0].clips) == 2 * len(CLIPS)
    assert not [i for i in report.issues if i.code == "STEREO_SINGLE_SOURCE_COLLAPSED"]


def test_sequence_aninhada_le_o_audio_como_a_versao_achatada(tmp_path):
    """A Sequence aninhada da o mesmo resultado da Sequence rasa."""
    wav = tmp_path / "mono.wav"
    wav.write_bytes(wav_bytes(SAMPLE_RATE * 5, channels=1))
    aninhado = _nested_aaf(tmp_path / "aninhado.aaf", wav, CLIPS)
    achatado = build_linked_aaf(tmp_path / "achatado.aaf", CLIPS, wav, start_timecode_frames=None)

    sessao_aninhada, report_aninhada = _sessao(aninhado, tmp_path)
    sessao_achatada, report_achatada = _sessao(achatado, tmp_path)

    assert not [i for i in report_aninhada.issues if i.severity == "error"]
    assert not [i for i in report_achatada.issues if i.severity == "error"]

    clips_aninhados = sessao_aninhada.tracks[0].clips
    clips_achatados = sessao_achatada.tracks[0].clips
    assert len(clips_aninhados) == len(CLIPS)
    assert len(clips_aninhados) == len(clips_achatados)
    for aninhado_clip, achatado_clip in zip(clips_aninhados, clips_achatados):
        assert aninhado_clip.timeline_start_samples == achatado_clip.timeline_start_samples
        assert aninhado_clip.timeline_end_samples == achatado_clip.timeline_end_samples
        assert aninhado_clip.source_offset_samples == achatado_clip.source_offset_samples


def test_sequence_aninhada_com_comprimento_errado_vira_erro(tmp_path):
    """Uma Sequence aninhada que nao bate com o cursor declarado e erro."""
    wav = tmp_path / "mono.wav"
    wav.write_bytes(wav_bytes(SAMPLE_RATE * 5, channels=1))
    aaf = _nested_aaf(tmp_path / "aninhado.aaf", wav, CLIPS, length=12345)
    report = Report(strict=False)

    read_aaf(aaf, report)

    erros = [i for i in report.issues if i.code == "AAF_NESTED_SEQUENCE_LENGTH_MISMATCH"]
    assert len(erros) == 1
    assert erros[0].context["declared_length"] == 12345
    assert erros[0].context["advanced"] == 3 * 48000
