"""Ajuste desconhecido do FCPXML em clip sem audio ou com o ajuste desligado.

O leitor avisa do ajuste que ele nao conhece, mas um ajuste desligado nao
muda o som e o clip sem audio nao tem ajuste: nenhum dos dois da aviso.
"""

from __future__ import annotations

from aafton.formats.fcpxml_read import read_fcpxml
from aafton.report import Report
from tests.unit.test_acomp_ajuste_desconhecido import (
    CLIP,
    DESCONHECIDO,
    _documento_com_midia,
)
from tests.unit.test_fcpxml_read_r3 import _codigos, _ler

DESLIGADO = '<adjust-foo enabled="0"/>'
INATIVO = '<adjust-foo active="0"/>'


def _documento_sem_audio(ajuste: str) -> str:
    """FCPXML de teste com o asset sem audio: o clip e so de video."""
    documento = _documento_com_midia("file:///C:/midia/dial02.wav", ajuste)
    return documento.replace('hasAudio="1"', 'hasAudio="0"')


def _ler_documento(tmp_path, documento: str):
    """Grava o documento, le o FCPXML e devolve o relatorio e a cena."""
    caminho = tmp_path / "caso.fcpxml"
    caminho.write_text(documento, encoding="utf-8")
    relatorio = Report(strict=False)
    return relatorio, read_fcpxml(caminho, relatorio)


def test_ajuste_desligado_nao_da_aviso(tmp_path):
    """Um ajuste desligado nao muda o som: o aviso nao sai."""
    report, sessao = _ler(tmp_path, f"{CLIP}{DESLIGADO}</asset-clip>")

    assert "FORMAT_EFFECT_UNKNOWN" not in _codigos(report)
    assert report.count("warning") == 0, _codigos(report)
    assert [c.name for c in sessao.all_clips()] == ["dial02"]


def test_ajuste_inativo_nao_da_aviso(tmp_path):
    """Um ajuste com active="0" tambem nao da aviso."""
    report, sessao = _ler(tmp_path, f"{CLIP}{INATIVO}</asset-clip>")

    assert "FORMAT_EFFECT_UNKNOWN" not in _codigos(report)
    assert report.count("warning") == 0, _codigos(report)
    assert [c.name for c in sessao.all_clips()] == ["dial02"]


def test_ajuste_desconhecido_em_clip_sem_audio_nao_da_aviso(tmp_path):
    """O clip de um asset sem audio nao tem ajuste que o leitor desconeca."""
    report, sessao = _ler_documento(tmp_path, _documento_sem_audio(DESCONHECIDO))

    assert "FORMAT_EFFECT_UNKNOWN" not in _codigos(report)
    assert report.count("warning") == 0, _codigos(report)
    assert list(sessao.all_clips()) == []


def test_ajuste_desconhecido_em_clip_com_audio_continua_aviso(tmp_path):
    """O ajuste que o leitor nao conhece em um clip com audio continua aviso."""
    report, sessao = _ler(tmp_path, f"{CLIP}{DESCONHECIDO}</asset-clip>")

    assert _codigos(report).count("FORMAT_EFFECT_UNKNOWN") == 1
    assert [c.name for c in sessao.all_clips()] == ["dial02"]
