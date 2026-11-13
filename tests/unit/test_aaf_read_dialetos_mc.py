"""Dialectos do Media Composer na leitura de AAF.

O Media Composer grava o clip mudo como um Selector cujo material esta nos
Alternates, o efeito de velocidade como um OperationGroup com IsTimeWarp, e
o componente que ele nao explica como um NestedScope. Os tres precisam ser
lidos sem perder audio do arquivo.
"""

import aaf2
import pytest

from aafton.aaf_read import read_aaf
from aafton.report import Report, StrictModeError
from tests.aaf_factory import build_linked_aaf, wav_bytes


AUID_AUDIO_WARP = "f1db0f27-8d64-11d3-80df-006008143e6f"
AUID_AUDIO_GAIN = "a1b2c3d4-e5f6-0718-293a-4b5c6d7e8f90"
AUID_DATA_DEF = "11111111-2222-3333-4444-555555555555"


def _wav(tmp_path):
    """Arquivo de audio usado pelos AAFs sinteticos."""
    caminho = tmp_path / "audio.wav"
    caminho.write_bytes(wav_bytes(96000))
    return caminho


def _aaf_com_clips(tmp_path, wav, quantidade=3):
    """AAF com SourceClips seguidos, um comecando em cada 24000 samples."""
    clips = [(i * 24000, 24000, 0) for i in range(quantidade)]
    return build_linked_aaf(tmp_path / "source.aaf", clips, wav, start_timecode_frames=0)


def _sequence_sonora(f):
    """A Sequence de audio da composicao do AAF aberto."""
    comp = next(f.content.compositionmobs())
    return next(s.segment for s in comp.slots if s.segment.media_kind == "Sound")


def _codigos(report, severidade=None):
    """Codigos dos problemas registrados no relatorio."""
    return [
        issue.code for issue in report.issues if severidade is None or issue.severity == severidade
    ]


def _source_clip(f, mob, slot_id):
    """Um SourceClip que aponta para o mesmo material do AAF."""
    clip = f.create.SourceClip(media_kind="sound", start=0, length=24000)
    clip.mob, clip.slot_id = mob, slot_id
    return clip


def test_selector_mudo_vira_clip_mudo_sem_mover_o_clip_seguinte(tmp_path):
    """Clip mudo do Avid: Selector com Selected=Filler e material no Alternate.

    O clip continua na timeline, so que sem audio, e o clip seguinte nao
    se desloca.
    """
    wav = _wav(tmp_path)
    path = _aaf_com_clips(tmp_path, wav)
    with aaf2.open(str(path), "rw") as f:
        seq = _sequence_sonora(f)
        segundo = list(seq.components)[1]
        mob, slot_id = segundo.mob, segundo.slot_id
        selector = f.create.Selector(media_kind="sound", length=24000)
        selector["Selected"].value = f.create.Filler(media_kind="sound", length=24000)
        selector["Alternates"].value = [_source_clip(f, mob, slot_id)]
        seq.components[1] = selector
    report = Report(strict=False)
    session = read_aaf(path, report)
    clips = list(session.all_clips())
    assert len(clips) == 3
    assert [c.muted for c in clips] == [False, True, False]
    assert clips[1].timeline_start_samples == 24000
    assert clips[1].timeline_end_samples == 48000
    assert clips[2].timeline_start_samples == 48000
    assert "AAF_CLIP_MUTED" in _codigos(report)


def test_selector_com_source_clip_vira_clip_normal(tmp_path):
    """Selector cujo Selected e o SourceClip vira um clip comum."""
    wav = _wav(tmp_path)
    path = _aaf_com_clips(tmp_path, wav)
    with aaf2.open(str(path), "rw") as f:
        seq = _sequence_sonora(f)
        segundo = list(seq.components)[1]
        mob, slot_id = segundo.mob, segundo.slot_id
        selector = f.create.Selector(media_kind="sound", length=24000)
        selector["Selected"].value = _source_clip(f, mob, slot_id)
        seq.components[1] = selector
    report = Report(strict=False)
    session = read_aaf(path, report)
    clips = list(session.all_clips())
    assert len(clips) == 3
    assert not any(c.muted for c in clips)
    assert clips[1].timeline_start_samples == 24000
    assert "AAF_CLIP_MUTED" not in _codigos(report)
    assert "AAF_UNKNOWN_COMPONENT" not in _codigos(report)


def test_selector_sem_material_alternativo_vira_lacuna(tmp_path):
    """Selector com Selected=Filler e sem material alternativo fica como lacuna."""
    wav = _wav(tmp_path)
    path = _aaf_com_clips(tmp_path, wav)
    with aaf2.open(str(path), "rw") as f:
        seq = _sequence_sonora(f)
        selector = f.create.Selector(media_kind="sound", length=24000)
        selector["Selected"].value = f.create.Filler(media_kind="sound", length=24000)
        seq.components[1] = selector
    report = Report(strict=False)
    session = read_aaf(path, report)
    assert "AAF_UNKNOWN_COMPONENT" not in _codigos(report)
    clips = list(session.all_clips())
    assert len(clips) == 2
    assert clips[1].timeline_start_samples == 48000


def _aaf_com_audio_warp(tmp_path, wav):
    """AAF com um OperationGroup 'Audio Warp', marcado com IsTimeWarp."""
    path = _aaf_com_clips(tmp_path, wav)
    with aaf2.open(str(path), "rw") as f:
        seq = _sequence_sonora(f)
        segundo = list(seq.components)[1]
        mob, slot_id = segundo.mob, segundo.slot_id
        root = f.create.root
        datadef = f.create.from_name("DataDef")
        datadef.auid = aaf2.auid.AUID(AUID_DATA_DEF)
        datadef["Name"].value = "OperationDef"
        root.dictionary["DataDefinitions"].append(datadef)
        opdef = f.create.OperationDef()
        opdef.auid = aaf2.auid.AUID(AUID_AUDIO_WARP)
        opdef["Name"].value = "Audio Warp"
        opdef["IsTimeWarp"].value = True
        opdef["NumberInputs"].value = 1
        opdef["DataDefinition"].value = datadef
        root.dictionary["OperationDefinitions"].append(opdef)
        grupo = f.create.OperationGroup(operationdef=opdef, media_kind="sound", length=24000)
        inner = f.create.Sequence(media_kind="sound")
        inner.components.append(_source_clip(f, mob, slot_id))
        grupo.segments.append(inner)
        seq.components[1] = grupo
    return path


def test_efeito_de_velocidade_falha_no_modo_estrito(tmp_path):
    """Efeito de velocidade nao e importado: no modo estrito, o leitor falha."""
    wav = _wav(tmp_path)
    path = _aaf_com_audio_warp(tmp_path, wav)
    with pytest.raises(StrictModeError, match="AAF_TIME_WARP_UNSUPPORTED"):
        read_aaf(path, Report())


def test_efeito_de_velocidade_registra_erro_no_modo_permissivo(tmp_path):
    """No modo permissivo, o trecho fica como lacuna e o erro fica no relatorio."""
    wav = _wav(tmp_path)
    path = _aaf_com_audio_warp(tmp_path, wav)
    report = Report(strict=False)
    session = read_aaf(path, report)
    assert "AAF_TIME_WARP_UNSUPPORTED" in _codigos(report, "error")
    clips = list(session.all_clips())
    assert len(clips) == 2
    assert clips[1].timeline_start_samples == 48000


def _aaf_com_audio_warp_aninhado(tmp_path, wav):
    """AAF com um 'Audio Gain' que embrulha o OperationGroup de velocidade.

    O warp nao esta no topo da Sequence: ele vem dentro do grupo de ganho,
    e o SourceClip fica dentro do warp.
    """
    path = _aaf_com_audio_warp(tmp_path, wav)
    with aaf2.open(str(path), "rw") as f:
        seq = _sequence_sonora(f)
        warp = list(seq.components)[1]
        root = f.create.root
        opdef_warp = next(
            d for d in root.dictionary["OperationDefinitions"] if d["Name"].value == "Audio Warp"
        )
        opdef = f.create.OperationDef()
        opdef.auid = aaf2.auid.AUID(AUID_AUDIO_GAIN)
        opdef["Name"].value = "Audio Gain"
        opdef["NumberInputs"].value = 1
        opdef["DataDefinition"].value = opdef_warp["DataDefinition"].value
        root.dictionary["OperationDefinitions"].append(opdef)
        ganho = f.create.OperationGroup(operationdef=opdef, media_kind="sound", length=24000)
        seq.components[1] = ganho
        ganho.segments.append(warp)
    return path


def test_efeito_de_velocidade_aninhado_falha_no_modo_estrito(tmp_path):
    """Velocidade dentro de 'Audio Gain' tambem e recusada no modo estrito."""
    wav = _wav(tmp_path)
    path = _aaf_com_audio_warp_aninhado(tmp_path, wav)
    with pytest.raises(StrictModeError, match="AAF_TIME_WARP_UNSUPPORTED"):
        read_aaf(path, Report())


def test_efeito_de_velocidade_aninhado_registra_erro_no_modo_permissivo(tmp_path):
    """No modo permissivo, o warp aninhado fica como lacuna e vira erro."""
    wav = _wav(tmp_path)
    path = _aaf_com_audio_warp_aninhado(tmp_path, wav)
    report = Report(strict=False)
    session = read_aaf(path, report)
    assert "AAF_TIME_WARP_UNSUPPORTED" in _codigos(report, "error")
    clips = list(session.all_clips())
    assert len(clips) == 2
    assert clips[1].timeline_start_samples == 48000


def _aaf_com_scope(tmp_path, wav, com_filler):
    """AAF com um NestedScope, que guarda o material da segunda faixa."""
    path = _aaf_com_clips(tmp_path, wav)
    with aaf2.open(str(path), "rw") as f:
        seq = _sequence_sonora(f)
        segundo = list(seq.components)[1]
        mob, slot_id = segundo.mob, segundo.slot_id
        scope = f.create.NestedScope(media_kind="sound", length=24000)
        inner = f.create.Sequence(media_kind="sound")
        if com_filler:
            inner.components.append(f.create.Filler(media_kind="sound", length=24000))
        else:
            inner.components.append(_source_clip(f, mob, slot_id))
        scope.slots.append(inner)
        seq.components[1] = scope
    return path


def test_componente_desconhecido_com_source_clip_e_erro(tmp_path):
    """Componente que esconde SourceClip e erro: o material nao pode sumir."""
    wav = _wav(tmp_path)
    path = _aaf_com_scope(tmp_path, wav, com_filler=False)
    with pytest.raises(StrictModeError, match="AAF_UNKNOWN_COMPONENT"):
        read_aaf(path, Report())
    report = Report(strict=False)
    session = read_aaf(path, report)
    assert "AAF_UNKNOWN_COMPONENT" in _codigos(report, "error")
    assert "AAF_UNKNOWN_COMPONENT" not in _codigos(report, "warning")
    assert len(list(session.all_clips())) == 2


def test_componente_desconhecido_so_com_filler_continua_aviso(tmp_path):
    """Componente que so tem lacuna nao esconde audio: continua aviso."""
    wav = _wav(tmp_path)
    path = _aaf_com_scope(tmp_path, wav, com_filler=True)
    report = Report(strict=False)
    session = read_aaf(path, report)
    assert "AAF_UNKNOWN_COMPONENT" in _codigos(report, "warning")
    assert "AAF_UNKNOWN_COMPONENT" not in _codigos(report, "error")
    assert len(list(session.all_clips())) == 2
