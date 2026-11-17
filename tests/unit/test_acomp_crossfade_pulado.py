"""Crossfade com automacao de ganho dentro do overlap.

O writer grava cada clip inteiro com o proprio envelope de ganho, entao a
automacao dentro do overlap nao impede a Transition: nenhum clip fica fora do
AAF (antes de 16/11/2026 o clip seguinte saia com WRITE_CLIP_SKIPPED).
"""

from aafton.aaf_write import write_aaf
from aafton.report import Report
from tests.unit.test_melhorias_transicao import (
    ORIGEM,
    _asset,
    _crossfade,
    _make_dummy_wav,
    _sessao,
    _slots_de_audio,
    _track,
)


def _escrever(tmp_path, nome, gain_points=None):
    """Escreve a sessao com um crossfade e devolve o relatorio e o AAF."""
    wav = _make_dummy_wav(tmp_path / "audio.wav")
    asset = _asset(wav)
    tracks = [
        _track("t0", "AUDIO 1", 0, _crossfade("a", "t0", ORIGEM), gain_points),
    ]
    sessao = _sessao(nome, tracks, asset)
    destino = tmp_path / f"{nome}.aaf"
    relatorio = Report(strict=False)
    write_aaf(sessao, destino, relatorio)
    return relatorio, destino


def test_automacao_no_overlap_nao_deixa_clip_fora(tmp_path):
    """Os dois clips entram no AAF com a Transition entre eles."""
    relatorio, destino = _escrever(tmp_path, "conflito", [(ORIGEM + 1600, 1.0)])

    codigos = [i.code for i in relatorio.issues]
    assert "WRITE_CLIP_SKIPPED" not in codigos
    assert "WRITE_FADE_UNSUPPORTED" not in codigos

    dados = _slots_de_audio(destino)[0][1]
    tipos = [t for t in dados["tipos"] if t != "Filler"]
    assert tipos.count("Transition") == 1
    assert len(tipos) == 3


def test_transicao_sem_conflito_nao_reporta_clip_pulado(tmp_path):
    """Um crossfade sem conflito de automacao nao deixa clip fora do AAF."""
    relatorio, destino = _escrever(tmp_path, "sem_conflito")

    assert "WRITE_CLIP_SKIPPED" not in [i.code for i in relatorio.issues]

    dados = _slots_de_audio(destino)[0][1]
    assert [t for t in dados["tipos"] if t != "Filler"] == [
        "SourceClip",
        "Transition",
        "SourceClip",
    ]
