"""Conferencias extras do reconform: campos, locators, envelopes e sidecar."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from aafton.als_paths import AUDIO_CLIP_FIELDS
from aafton.als_xml import format_value, get_value, load_als, save_als, set_value
from aafton.reconform_apply import apply_reconform
from aafton.reconform_diff import diff_cuts
from aafton.reconform_verify import verify_reconform
from aafton.report import Report
from aafton.sidecar import sidecar_path_for
from tests.unit.test_reconform_apply import _build_spanning_set
from tests.unit.test_reconform_verify import (
    CLIPS,
    _aplicado,
    _codigos,
    _cortes_com_leader,
    _mexer,
    _track,
)

pytest.importorskip("opentimelineio")


def _envelopes_com_eventos(root, nome: str):
    """Envelopes de automacao da track que tem eventos com tempo positivo."""
    for env in _track(root, nome).findall("AutomationEnvelopes/Envelopes/AutomationEnvelope"):
        caixa = env.find("Automation/Events")
        if caixa is None:
            continue
        if any(float(ev.get("Time") or "0") > 0 for ev in caixa):
            yield env


def _mexer_locator(root, nome: str, delta: float) -> None:
    """Desloca o locator pedido em um numero de beats."""
    for loc in root.findall(".//LiveSet/Locators/Locators/Locator"):
        if (get_value(loc, "Name") or "") != nome:
            continue
        tempo = float(get_value(loc, "Time") or "0")
        set_value(loc, "Time", tempo + delta)
        return
    raise AssertionError(f"locator {nome!r} not found in the copy")


def _time_fora(root) -> None:
    """Desloca o atributo Time de um clip em uma amostra."""
    clip = _track(root, "Track 0").findall(CLIPS)[0]
    bruto = clip.get("Time") or "0"
    clip.set("Time", format_value(float(bruto) + 1.0 / 48000))


def _loop_end_fora(root) -> None:
    """Muda o LoopEnd de um clip em dez amostras."""
    clip = _track(root, "Track 0").findall(CLIPS)[0]
    loop_end = float(get_value(clip, AUDIO_CLIP_FIELDS["loop_end"]))
    set_value(clip, AUDIO_CLIP_FIELDS["loop_end"], loop_end + 10 / 48000)


def _locator_de_usuario_fora(root) -> None:
    """Desloca o locator de usuario em um beat."""
    _mexer_locator(root, "Nota B", 1.0)


def _rc_fora(root) -> None:
    """Desloca o marcador RC de uma mudanca em um beat."""
    _mexer_locator(root, "RC insert 01:00:02:00", 1.0)


def _valor_de_automacao_fora(root) -> None:
    """Muda o valor de um evento de automacao."""
    for env in _envelopes_com_eventos(root, "Track 0"):
        for ev in env.find("Automation/Events"):
            if float(ev.get("Time") or "0") > 0:
                ev.set("Value", "0.25")
                return
    raise AssertionError("no automation event with a positive time")


def _sentinel_fora(root) -> None:
    """Remove um evento sentinela de automacao."""
    for env in _envelopes_com_eventos(root, "Track 0"):
        caixa = env.find("Automation/Events")
        for ev in list(caixa):
            if float(ev.get("Time") or "0") < 0:
                caixa.remove(ev)
                return
    raise AssertionError("no sentinel event in the automation")


def test_apply_certo_passa_as_conferencias_extras(tmp_path: Path) -> None:
    old, new, origem, aplicado, sha = _aplicado(tmp_path)
    resultado = verify_reconform(
        old, new, origem, aplicado, Report(strict=False), source_sha256=sha
    )
    assert resultado.ok, resultado.problems


@pytest.mark.parametrize(
    ("mudanca", "codigo"),
    [
        (_time_fora, "RECONFORM_VERIFY_CLIP_FIELDS"),
        (_loop_end_fora, "RECONFORM_VERIFY_CLIP_FIELDS"),
        (_locator_de_usuario_fora, "RECONFORM_VERIFY_LOCATOR"),
        (_rc_fora, "RECONFORM_VERIFY_LOCATOR"),
        (_valor_de_automacao_fora, "RECONFORM_VERIFY_AUTOMATION"),
        (_sentinel_fora, "RECONFORM_VERIFY_AUTOMATION"),
    ],
)
def test_desvio_extra_e_pego(tmp_path: Path, mudanca, codigo: str) -> None:
    old, new, origem, aplicado, _sha = _aplicado(tmp_path)
    mexido = _mexer(aplicado, mudanca)
    resultado = verify_reconform(old, new, origem, mexido, Report(strict=False))
    assert not resultado.ok
    assert codigo in _codigos(resultado)


def test_sidecar_da_copia_truncado(tmp_path: Path) -> None:
    old, new, origem, aplicado, _sha = _aplicado(tmp_path)
    mexido = _mexer(aplicado, lambda root: None)
    caminho = sidecar_path_for(mexido)
    dados = json.loads(caminho.read_text(encoding="utf-8"))
    dados["audio_source_indices"] = list(dados["audio_source_indices"])[:1]
    caminho.write_text(json.dumps(dados), encoding="utf-8")
    resultado = verify_reconform(old, new, origem, mexido, Report(strict=False))
    assert not resultado.ok
    assert "RECONFORM_VERIFY_SIDECAR" in _codigos(resultado)


def test_sidecar_da_origem_mudou(tmp_path: Path) -> None:
    old, new, origem, aplicado, _sha = _aplicado(tmp_path)
    caminho = sidecar_path_for(origem)
    antes = hashlib.sha256(caminho.read_bytes()).hexdigest()
    dados = json.loads(caminho.read_text(encoding="utf-8"))
    caminho.write_text(json.dumps(dados, indent=4), encoding="utf-8")
    resultado = verify_reconform(
        old, new, origem, aplicado, Report(strict=False), source_sidecar_sha256=antes
    )
    assert not resultado.ok
    assert "RECONFORM_VERIFY_SOURCE_CHANGED" in _codigos(resultado)


def _renomeia_inicio(root) -> None:
    """Renomeia o locator de inicio do set e o poe fora do tempo zero."""
    for loc in root.findall(".//LiveSet/Locators/Locators/Locator"):
        if (get_value(loc, "IsSongStart") or "").lower() != "true":
            continue
        set_value(loc, "Name", "Intro")
        set_value(loc, "Time", 2.0)
        return
    raise AssertionError("no song start locator in the set")


def _set_com_inicio_renomeado(tmp_path: Path) -> Path:
    """Set de exemplo com o locator de inicio renomeado para Intro."""
    set_path = _build_spanning_set(tmp_path)
    root = load_als(set_path)
    _renomeia_inicio(root)
    save_als(root, set_path)
    return set_path


def _aplicado_com_inicio_renomeado(tmp_path: Path):
    """Aplica um par de cortes com leader sobre o set de inicio renomeado."""
    antigo, novo = _cortes_com_leader()
    set_path = _set_com_inicio_renomeado(tmp_path)
    sha = hashlib.sha256(set_path.read_bytes()).hexdigest()
    res = apply_reconform(set_path, diff_cuts(antigo, novo, Report()), Report())
    assert res.written
    return antigo, novo, set_path, res.output, sha


def test_locator_de_inicio_fixo_passa_no_verify(tmp_path: Path) -> None:
    """Locator de inicio que o apply mantem fixo nao e conferido pelo oraculo."""
    antigo, novo, origem, aplicado, sha = _aplicado_com_inicio_renomeado(tmp_path)
    resultado = verify_reconform(
        antigo, novo, origem, aplicado, Report(strict=False), source_sha256=sha
    )
    assert resultado.ok, resultado.problems


def _move_o_inicio(root) -> None:
    """Move o locator de inicio do set renomeado."""
    _mexer_locator(root, "Intro", 1.0)


def test_locator_de_inicio_fixo_movido_e_pego(tmp_path: Path) -> None:
    """O verify pega o locator de inicio que o apply devia manter fixo."""
    antigo, novo, origem, aplicado, _sha = _aplicado_com_inicio_renomeado(tmp_path)
    mexido = _mexer(aplicado, _move_o_inicio)
    resultado = verify_reconform(antigo, novo, origem, mexido, Report(strict=False))
    assert not resultado.ok
    assert "RECONFORM_VERIFY_LOCATOR" in _codigos(resultado)
