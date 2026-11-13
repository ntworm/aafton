"""Transition do AAF ao lado de Filler, de outra Transition e de Sequence vazia.

O Resolve e o Premiere gravam o fade-in a partir do silencio como
Filler, Transition, clip e o fade-out para o silencio como clip, Transition,
Filler. So o vizinho imediato da Transition conta: nenhum fade pode cair num
clip que nao encosta nela, nem sair negativo ou maior que o clip.
"""

from __future__ import annotations

from pathlib import Path

import aaf2
from aaf2.auid import AUID

from aafton.aaf_read import read_aaf
from aafton.report import Report
from tests.aaf_factory import wav_bytes

TAXA = 48000


def _operation_def(f):
    op_def = f.create.OperationDef(
        AUID("0c3bea41-fc05-11d2-8a29-0050040ef7d2"), "Avid Audio Dissolve", ""
    )
    op_def["IsTimeWarp"].value = False
    op_def["NumberInputs"].value = 2
    op_def["DataDefinition"].value = f.dictionary.lookup_datadef("Sound")
    f.dictionary["OperationDefinitions"].append(op_def)
    return op_def


def _transicao(f, op_def, comprimento: int):
    grupo = f.create.OperationGroup(op_def)
    grupo.media_kind = "sound"
    grupo.length = comprimento
    transicao = f.create.Transition(media_kind="sound", length=comprimento)
    transicao.cutpoint = comprimento // 2
    transicao["OperationGroup"].value = grupo
    return transicao


def _montar(path: Path, wav: Path, partes: list[tuple[str, int]], aninhar: bool = False) -> Path:
    """AAF com uma track cuja Sequence segue a lista ('clip'|'filler'|'transition', n)."""
    with aaf2.open(str(path), "w") as f:
        composicao = f.create.CompositionMob("vizinhos")
        f.content.mobs.append(composicao)
        meta = {
            "format": {"filename": str(wav), "format_name": "wav"},
            "streams": [
                {
                    "codec_type": "audio",
                    "codec_name": "pcm_s16le",
                    "sample_rate": TAXA,
                    "duration_ts": TAXA * 60,
                    "channels": 1,
                }
            ],
        }
        master_mob, _s, _t = f.content.link_external_wav(meta)
        slot = composicao.create_timeline_slot(edit_rate=TAXA)
        slot.name = "A1"
        sequencia = f.create.Sequence(media_kind="sound")
        slot.segment = sequencia
        op_def = _operation_def(f)
        destino = sequencia
        if aninhar:
            destino = f.create.Sequence(media_kind="sound")
            sequencia.components.append(destino)
        for tipo, comprimento in partes:
            if tipo == "clip":
                componente = f.create.SourceClip(media_kind="sound", start=0)
                componente.length = comprimento
                componente.mob = master_mob
                componente.slot_id = 1
            elif tipo == "filler":
                componente = f.create.Filler(media_kind="sound", length=comprimento)
            else:
                componente = _transicao(f, op_def, comprimento)
            destino.components.append(componente)
    return path


def _ler(tmp_path: Path, partes, aninhar: bool = False):
    wav = tmp_path / "fonte.wav"
    wav.write_bytes(wav_bytes(TAXA * 60, sample_rate=TAXA))
    aaf = _montar(tmp_path / "vizinhos.aaf", wav, partes, aninhar)
    report = Report(strict=False)
    session = read_aaf(aaf, report)
    return session.tracks[0].clips, report


def _duracao(fade):
    return fade.duration_samples if fade else 0


def test_fade_in_a_partir_do_silencio(tmp_path):
    """Filler, Transition, clip: fade-in no clip seguinte e nada no anterior."""
    clips, report = _ler(
        tmp_path, [("clip", 1000), ("filler", 5000), ("transition", 1000), ("clip", 3000)]
    )
    assert len(clips) == 2
    primeiro, segundo = clips
    assert primeiro.fade_out is None
    assert _duracao(segundo.fade_in) == 1000
    assert not [i for i in report.issues if i.code == "TRANSITION_OVERLAP_INVALID"]


def test_fade_out_para_o_silencio_nao_vaza_para_o_proximo_clip(tmp_path):
    """Clip, Transition, Filler, clip: o fade-out fica no primeiro e o segundo entra seco."""
    clips, _report = _ler(
        tmp_path, [("clip", 3000), ("transition", 1000), ("filler", 5000), ("clip", 3000)]
    )
    assert len(clips) == 2
    primeiro, segundo = clips
    assert _duracao(primeiro.fade_out) == 1000
    assert segundo.fade_in is None


def test_duas_transicoes_seguidas_nao_geram_fade_maior_que_o_clip(tmp_path):
    """Clip, Transition, Transition, clip: nenhum fade negativo nem maior que o clip."""
    clips, _report = _ler(
        tmp_path, [("clip", 1000), ("transition", 800), ("transition", 800), ("clip", 3000)]
    )
    for clip in clips:
        duracao = clip.timeline_end_samples - clip.timeline_start_samples
        for fade in (clip.fade_in, clip.fade_out):
            assert 0 <= _duracao(fade) <= duracao


def test_crossfade_entre_clips_continua_simetrico(tmp_path):
    """Clip, Transition, clip: fade-out e fade-in iguais a sobreposicao."""
    clips, _report = _ler(tmp_path, [("clip", 3000), ("transition", 1000), ("clip", 3000)])
    primeiro, segundo = clips
    sobreposicao = primeiro.timeline_end_samples - segundo.timeline_start_samples
    assert sobreposicao == 1000
    assert _duracao(primeiro.fade_out) == sobreposicao == _duracao(segundo.fade_in)


def test_sequence_aninhada_sem_comprimento_nao_gera_erro(tmp_path):
    """Sequence aninhada com length 0 e lida sem AAF_NESTED_SEQUENCE_LENGTH_MISMATCH."""
    clips, report = _ler(tmp_path, [("clip", 1000), ("clip", 2000)], aninhar=True)
    assert len(clips) == 2
    assert not [i for i in report.issues if i.code == "AAF_NESTED_SEQUENCE_LENGTH_MISMATCH"]
