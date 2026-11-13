"""Crossfade lido de uma Transition, com a duracao medida pela sobreposicao.

A Transition do AAF traz a sobreposicao em unidades de edicao. O fim do clip
anterior e o inicio do clip seguinte sao quantizados cada um a partir do
proprio cursor, entao, em taxa que nao fecha em sample inteiro, a conta da
Transition e a conta da sobreposicao podem diferir em 1 sample. O crossfade
precisa ter exatamente a duracao da sobreposicao, ou a emenda nao fecha.
"""

from __future__ import annotations

from pathlib import Path

import aaf2
from aaf2.auid import AUID

from aafton.aaf_read import read_aaf
from aafton.report import Report
from tests.aaf_factory import build_aaf_with_transition, wav_bytes

# 29,97 fps em ticks de AAF (100 ticks por quadro) com audio a 22050 Hz:
# a conversao de tick para sample nao fecha em numero inteiro.
TAXA_EDICAO = 2997
TAXA_AMOSTRAGEM = 22050
DURACAO_CLIP = 5000
DURACAO_TRANSICAO = 1000


def _build_aaf_com_transicao(
    path: Path,
    wav_path: Path,
    *,
    taxa_amostragem: int = TAXA_AMOSTRAGEM,
    taxa_edicao: int = TAXA_EDICAO,
    duracao_clip: int = DURACAO_CLIP,
    duracao_transicao: int = DURACAO_TRANSICAO,
) -> Path:
    """AAF com dois clips separados por uma Transition, em taxas escolhidas.

    Mesmo formato de `tests.aaf_factory.build_aaf_with_transition`, mas com a
    taxa de edicao separada da taxa de amostragem, para o teste poder escolher
    uma combinacao em que a conversao nao fecha em sample inteiro.
    """
    with aaf2.open(str(path), "w") as f:
        composition = f.create.CompositionMob("crossfade")
        f.content.mobs.append(composition)
        meta = {
            "format": {"filename": str(wav_path), "format_name": "wav"},
            "streams": [
                {
                    "codec_type": "audio",
                    "codec_name": "pcm_s16le",
                    "sample_rate": taxa_amostragem,
                    "duration_ts": taxa_amostragem * 60,
                    "channels": 1,
                }
            ],
        }
        master_mob, _s, _t = f.content.link_external_wav(meta)

        slot = composition.create_timeline_slot(edit_rate=taxa_edicao)
        slot.name = "CROSSFADE"
        sequence = f.create.Sequence(media_kind="sound")
        slot.segment = sequence

        dissolve_auid = AUID("0c3bea41-fc05-11d2-8a29-0050040ef7d2")
        op_def = f.create.OperationDef(dissolve_auid, "Avid Audio Dissolve", "")
        op_def["IsTimeWarp"].value = False
        op_def["NumberInputs"].value = 2
        op_def["DataDefinition"].value = f.dictionary.lookup_datadef("Sound")
        f.dictionary["OperationDefinitions"].append(op_def)

        primeiro = f.create.SourceClip(media_kind="sound", start=0)
        primeiro.length = duracao_clip
        primeiro.mob = master_mob
        primeiro.slot_id = 1
        sequence.components.append(primeiro)

        fade_op = f.create.OperationGroup(op_def)
        fade_op.media_kind = "sound"
        fade_op.length = duracao_transicao

        transicao = f.create.Transition(media_kind="sound", length=duracao_transicao)
        transicao.cutpoint = duracao_transicao
        transicao["OperationGroup"].value = fade_op
        sequence.components.append(transicao)

        segundo = f.create.SourceClip(media_kind="sound", start=0)
        segundo.length = duracao_clip
        segundo.mob = master_mob
        segundo.slot_id = 1
        sequence.components.append(segundo)
    return path


def _clips_do_crossfade(tmp_path: Path):
    """Le o AAF do crossfade e devolve os dois clips e o relatorio."""
    wav = tmp_path / "fonte.wav"
    wav.write_bytes(wav_bytes(48000, sample_rate=TAXA_AMOSTRAGEM))
    aaf = _build_aaf_com_transicao(tmp_path / "crossfade.aaf", wav)
    report = Report(strict=False)
    session = read_aaf(aaf, report)
    assert not report.incomplete, [i.message for i in report.issues if i.severity == "error"]
    clips = session.tracks[0].clips
    assert len(clips) == 2, [c.name for c in clips]
    return clips[0], clips[1], session, report


def test_crossfade_tem_a_duracao_da_sobreposicao(tmp_path):
    """Em taxa fracionaria o crossfade mede a sobreposicao, nao a Transition."""
    primeiro, segundo, session, report = _clips_do_crossfade(tmp_path)

    assert session.sample_rate == TAXA_AMOSTRAGEM
    sobreposicao = primeiro.timeline_end_samples - segundo.timeline_start_samples
    assert sobreposicao == 7358

    assert primeiro.fade_out is not None
    assert segundo.fade_in is not None
    assert primeiro.fade_out.duration_samples == sobreposicao
    assert segundo.fade_in.duration_samples == sobreposicao
    assert (
        primeiro.timeline_end_samples
        == segundo.timeline_start_samples + primeiro.fade_out.duration_samples
    )


def test_crossfade_reporta_a_diferenca_entre_transicao_e_sobreposicao(tmp_path):
    """A Transition quantizada sozinha da 7357; a sobreposicao real da 7358."""
    _primeiro, _segundo, _session, report = _clips_do_crossfade(tmp_path)

    notas = [i for i in report.issues if i.code == "TRANSITION_LENGTH_FROM_POSITIONS"]
    assert len(notas) == 1, [i.code for i in report.issues]
    assert notas[0].severity == "info"
    assert notas[0].context["transition_samples"] == 7357
    assert notas[0].context["overlap"] == 7358


def test_crossfade_em_taxa_inteira_mantem_a_duracao(tmp_path):
    """Em taxa que fecha em sample inteiro o crossfade continua igual."""
    wav = tmp_path / "fonte.wav"
    wav.write_bytes(wav_bytes(48000))
    aaf = build_aaf_with_transition(tmp_path / "crossfade.aaf", wav)
    report = Report(strict=False)
    session = read_aaf(aaf, report)
    assert not report.incomplete, [i.message for i in report.issues if i.severity == "error"]

    primeiro, segundo = session.tracks[0].clips
    sobreposicao = primeiro.timeline_end_samples - segundo.timeline_start_samples
    assert sobreposicao == 24000
    assert primeiro.fade_out is not None
    assert segundo.fade_in is not None
    assert primeiro.fade_out.duration_samples == 24000
    assert segundo.fade_in.duration_samples == 24000
    assert "TRANSITION_LENGTH_FROM_POSITIONS" not in [i.code for i in report.issues]
