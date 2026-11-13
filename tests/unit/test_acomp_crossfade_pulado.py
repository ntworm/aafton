"""Clip que fica fora do AAF quando a transicao conflita com a automacao.

Quando a automacao segmentada impede a Transition, o clip seguinte do par nao
cabe no AAF: o preflight so dizia WRITE_FADE_UNSUPPORTED, como se o clip
tivesse sido escrito sem o fade. Agora o relatorio diz WRITE_CLIP_SKIPPED, com
o nome do clip e da track, e o fade do clip que nao foi escrito nao vira erro
de fade: o fade-out do clip anterior continua como envelope de ganho.
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


def test_transicao_conflitante_reporta_o_clip_pulado(tmp_path):
    """O clip que nao entra no AAF e nomeado no relatorio."""
    relatorio, destino = _escrever(tmp_path, "conflito", [(ORIGEM + 1600, 1.0)])

    pulados = [i for i in relatorio.issues if i.code == "WRITE_CLIP_SKIPPED"]
    assert len(pulados) == 1
    assert pulados[0].severity == "warning"
    assert pulados[0].context["clip"] == "a2.wav"
    assert pulados[0].context["track"] == "AUDIO 1"

    fades = [i for i in relatorio.issues if i.code == "WRITE_FADE_UNSUPPORTED"]
    assert [i.context["clip"] for i in fades] == ["a1.wav"]

    dados = _slots_de_audio(destino)[0][1]
    assert dados["tipos"] == ["OperationGroup"]
    assert dados["envelopes"] == 1


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
