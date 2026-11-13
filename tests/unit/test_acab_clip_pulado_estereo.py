"""Clip que fica fora do AAF em uma track stereo.

O clip que conflita com a automacao segmentada fica fora do AAF, e com ele a
outra metade do par stereo: o aviso WRITE_FADE_UNSUPPORTED nao pode nomear
uma metade que nao entrou no arquivo.
"""

from __future__ import annotations

from dataclasses import replace

from aafton.aaf_write import write_aaf
from aafton.ir import TrackIR
from aafton.report import Report
from tests.unit.test_acomp_crossfade_pulado import (
    ORIGEM,
    _asset,
    _crossfade,
    _make_dummy_wav,
    _sessao,
    _slots_de_audio,
)


def _par_stereo(clips):
    """A mesma cena no canal direito: o par stereo de cada clip."""
    return [
        replace(clip, id=f"{clip.id}_r", event_index=clip.event_index + 10, channel_index=1)
        for clip in clips
    ]


def _track_stereo(nome, indice, clips, gain_points=None):
    """Track de audio stereo com os clips e a automacao informados."""
    track = TrackIR(id="t0", source_index=indice, name=nome, kind="audio", channel_count=2)
    track.clips.extend(clips)
    track.gain_points.extend(gain_points or [])
    return track


def _escrever(tmp_path):
    """Escreve a sessao stereo com o crossfade em conflito e devolve o relatorio."""
    wav = _make_dummy_wav(tmp_path / "audio.wav")
    asset = _asset(wav)
    esquerda = _crossfade("a", "t0", ORIGEM)
    direita = _par_stereo(esquerda)
    clips = [esquerda[0], direita[0], esquerda[1], direita[1]]
    tracks = [_track_stereo("AUDIO 1", 0, clips, [(ORIGEM + 1600, 1.0)])]
    sessao = _sessao("estereo", tracks, asset)
    destino = tmp_path / "estereo.aaf"
    relatorio = Report(strict=False)
    write_aaf(sessao, destino, relatorio)
    return relatorio, destino


def test_a_metade_do_par_pulado_nao_vira_aviso_de_fade(tmp_path):
    """O clip pulado e o par inteiro: nenhum dos dois vira aviso de fade."""
    relatorio, destino = _escrever(tmp_path)

    pulados = [i for i in relatorio.issues if i.code == "WRITE_CLIP_SKIPPED"]
    assert len(pulados) == 1
    assert pulados[0].context["clip"] == "a2.wav"

    fades = [i for i in relatorio.issues if i.code == "WRITE_FADE_UNSUPPORTED"]
    assert "a2.wav" not in [i.context["clip"] for i in fades]


def test_o_arquivo_do_aaf_tem_o_clip_que_ficou(tmp_path):
    """O AAF escrito tem o clip que ficou, com o envelope do fade dele."""
    _, destino = _escrever(tmp_path)

    dados = _slots_de_audio(destino)[0][1]
    assert dados["tipos"] == ["OperationGroup"]
    assert dados["transicoes"] == 0
    assert dados["envelopes"] == 1
