"""Crossfade com automacao dentro do overlap numa track stereo.

O par stereo inteiro entra no AAF com a Transition: a automacao dentro do
overlap nao deixa nenhuma metade fora (antes de 16/11/2026 o clip seguinte e o
par dele saiam com WRITE_CLIP_SKIPPED).
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


def test_nenhuma_metade_do_par_fica_fora_nem_vira_aviso_de_fade(tmp_path):
    """Os dois clips do par entram: nenhum pulo e nenhum erro de fade."""
    relatorio, _ = _escrever(tmp_path)

    codigos = [i.code for i in relatorio.issues]
    assert "WRITE_CLIP_SKIPPED" not in codigos
    assert "WRITE_FADE_UNSUPPORTED" not in codigos


def test_o_arquivo_do_aaf_tem_os_dois_clips_e_a_transition(tmp_path):
    """O slot stereo tem os dois clips e a Transition entre eles."""
    _, destino = _escrever(tmp_path)

    dados = _slots_de_audio(destino)[0][1]
    assert dados["tipos"].count("OperationGroup") == 2
    assert dados["transicoes"] == 1
