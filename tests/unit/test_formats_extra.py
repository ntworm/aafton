"""Testes do pacote de formatos, que depende do extra opcional formats."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from aafton.formats import ADAPTADORES
from aafton.formats import ExtraAusente
from aafton.formats import carregar_otio

RAIZ = Path(__file__).resolve().parents[2]
SRC = RAIZ / "src"


def test_extensao_mapeia_para_o_adaptador_certo():
    assert ADAPTADORES == {
        ".edl": "cmx_3600",
        ".xml": "fcp_xml",
        ".fcpxml": "fcpx_xml",
    }


def test_carregar_otio_devolve_o_modulo_quando_o_extra_esta_instalado():
    pytest.importorskip("opentimelineio")
    modulo = carregar_otio()
    assert modulo is not None
    assert hasattr(modulo, "adapters")


def test_carregar_otio_sem_o_extra_levanta_extra_ausente(monkeypatch):
    monkeypatch.setitem(sys.modules, "opentimelineio", None)
    with pytest.raises(ExtraAusente) as erro:
        carregar_otio()
    assert "aafton[formats]" in str(erro.value)


def test_extra_ausente_e_um_runtime_error():
    assert issubclass(ExtraAusente, RuntimeError)


def test_importar_o_pacote_de_formatos_nao_carrega_o_opentimelineio():
    ambiente = dict(os.environ)
    ambiente["PYTHONPATH"] = str(SRC)
    comando = [
        sys.executable,
        "-c",
        'import aafton.formats, sys; print("opentimelineio" in sys.modules)',
    ]
    resultado = subprocess.run(
        comando,
        capture_output=True,
        text=True,
        env=ambiente,
        cwd=str(RAIZ),
        check=False,
    )
    assert resultado.returncode == 0, resultado.stderr
    assert resultado.stdout.strip() == "False"
