"""Volta Live -> AAF do TESTE_AFF_PROTOOLS, sem organizacao e com --organize type.

Import, export estrito, verify da volta (AAF exportado contra o set) e verify de
ida (AAF de origem, no caminho do sidecar e copiado para outro caminho). O corpus
de teste e silencio: prova posicao e cobertura, nao som.
"""

from __future__ import annotations

import shutil

import aaf2
import pytest

from aafton.cli import main
from aafton.verify import verify_conversion
from tests.support.paths import FIXTURES_AAF, corpus_dir

AAF_PATH = FIXTURES_AAF / "TESTE_AFF_PROTOOLS.aaf"
TXT_PATH = FIXTURES_AAF / "teste_aaf_protools_txt.txt"


def _importar(tmp_path, modo):
    als = tmp_path / f"{modo}.als"
    argv = ["import", str(AAF_PATH), str(als), "--audio-dir", str(corpus_dir("teste_aaf"))]
    argv += ["--session-text", str(TXT_PATH), "--organize", modo, "--permissive"]
    assert main(argv) == 0
    return als


def _conferir_cobertura(resultado):
    assert resultado.errors == []
    assert resultado.audio_missing == 0
    assert resultado.audio_extra == 0
    assert resultado.audio_compared == resultado.audio_expected > 0
    assert resultado.max_delta_samples == 0
    assert resultado.ok


@pytest.mark.parametrize("modo", ["off", "type"])
def test_volta_do_set_passa_no_verify_sample_exato(tmp_path, modo):
    als = _importar(tmp_path, modo)
    aaf = tmp_path / f"{modo}-volta.aaf"
    report = tmp_path / f"{modo}-export.json"
    assert main(["export", str(als), str(aaf), "--report", str(report)]) == 0

    texto = report.read_text(encoding="utf-8")
    assert "WRITE_CLIP_SKIPPED" not in texto
    assert "WRITE_TRANSITION_SEGMENTED_AUTOMATION_UNSUPPORTED" not in texto
    assert "WRITE_FADE_UNSUPPORTED" not in texto
    assert "WRITE_FADE_AS_ENVELOPE" in texto

    volta = verify_conversion(aaf, als)
    _conferir_cobertura(volta)
    assert "media offset verified" in volta.coverage_msg

    ida = verify_conversion(AAF_PATH, als)
    _conferir_cobertura(ida)
    copia = tmp_path / "origem-copiada.aaf"
    shutil.copyfile(AAF_PATH, copia)
    _conferir_cobertura(verify_conversion(copia, als))

    with aaf2.open(str(aaf), "r") as f:
        nomes = {m.name for m in f.content.mobs if m.name}
        comp = next(m for m in f.content.mobs if type(m).__name__ == "CompositionMob")
        soundly = [s for s in comp.slots if s.name == "soundly"]
    assert not any(".L.R" in nome for nome in nomes)
    assert len(soundly) == 1
