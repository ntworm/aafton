"""Lacunas do verify do reconform: posicao do saiu, envelope de clip e imports."""

from __future__ import annotations

import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from aafton import cli_reconform
from aafton.als_paths import AUDIO_CLIP_FIELDS
from aafton.als_write_base import SENTINEL_TIME
from aafton.als_xml import get_value, load_als, save_als
from aafton.cli import main
from aafton.cut_read import read_cut
from aafton.reconform_apply import apply_reconform
from aafton.reconform_diff import diff_cuts
from aafton.reconform_verify import verify_reconform
from aafton.report import Report
from tests.unit.test_cli_reconform import _edls
from tests.unit.test_reconform_apply import (
    _NEW_EDL_25,
    _OLD_EDL_25,
    RATE_25,
    _build_spanning_set,
    _sha256,
)
from tests.unit.test_reconform_verify import (
    CLIPS,
    _aplicado,
    _codigos,
    _mexer,
    _move_clip,
    _track,
)

# Eventos do ClipEnvelope do clip de origem: um sentinela e tres pontos.
_EVENTOS_ENV = [(SENTINEL_TIME, "0"), (0.0, "0.1"), (1.0, "0.5"), (3.0, "0.9")]

# O interpretador novo importa o verify e diz quais modulos pesados entraram.
_CODIGO_IMPORTS = """
import sys
import aafton.reconform_verify
import aafton.reconform_verify_extra
pesados = [m for m in ("aafton.reconform_map", "aafton.reconform_samples", "aafton.reconform_diff")
           if m in sys.modules]
print(",".join(pesados))
"""


def _clips_por_inicio(root, nome: str) -> list[ET.Element]:
    """Clips da track pedida, do mais antigo para o mais novo."""
    clips = _track(root, nome).findall(CLIPS)
    return sorted(
        clips, key=lambda c: float(get_value(c, AUDIO_CLIP_FIELDS["current_start"]) or "0")
    )


def _move_o_clip_saiu(root) -> None:
    """Move o clip da track [saiu] por cem amostras."""
    _move_clip(_track(root, "Track 0 [saiu]").findall(CLIPS)[0], 100)


def test_clip_saiu_no_lugar_passa_no_verify(tmp_path: Path) -> None:
    """O clip da track [saiu] fica na emenda do trecho removido."""
    old, new, origem, aplicado, sha = _aplicado(tmp_path)
    resultado = verify_reconform(
        old, new, origem, aplicado, Report(strict=False), source_sha256=sha
    )
    assert resultado.ok, resultado.problems


def test_clip_saiu_movido_cem_amostras_e_pego(tmp_path: Path) -> None:
    """O verify pega o clip da track [saiu] fora da emenda do trecho removido."""
    old, new, origem, aplicado, _sha = _aplicado(tmp_path)
    mexido = _mexer(aplicado, _move_o_clip_saiu)
    resultado = verify_reconform(old, new, origem, mexido, Report(strict=False))
    assert not resultado.ok
    assert "RECONFORM_VERIFY_POSITION" in _codigos(resultado)
    removidos = [p for p in resultado.problems if p["detail"].startswith("removed piece")]
    assert len(removidos) == 1


def _attach_clip_envelope(clip_node: ET.Element, eventos: list[tuple[float, str]]) -> None:
    """Anexa ao clip um ClipEnvelope com os eventos informados."""
    outer = clip_node.find("Envelopes")
    if outer is None:
        outer = ET.SubElement(clip_node, "Envelopes")
    inner = outer.find("Envelopes")
    if inner is None:
        inner = ET.SubElement(outer, "Envelopes")
    env = ET.SubElement(inner, "ClipEnvelope", {"Id": str(len(list(inner)))})
    caixa = ET.SubElement(ET.SubElement(env, "Automation"), "Events")
    for idx, (tempo, valor) in enumerate(eventos):
        ET.SubElement(caixa, "FloatEvent", {"Id": str(idx), "Time": f"{tempo:g}", "Value": valor})


def _set_com_envelope(tmp_path: Path) -> Path:
    """Set de exemplo com um ClipEnvelope no clip que o apply fatia."""
    set_path = _build_spanning_set(tmp_path)
    root = load_als(set_path)
    _attach_clip_envelope(_clips_por_inicio(root, "Track 0")[0], _EVENTOS_ENV)
    save_als(root, set_path)
    return set_path


def _aplicado_com_envelope(tmp_path: Path):
    """Aplica os cortes de exemplo sobre o set que tem ClipEnvelope."""
    pytest.importorskip("opentimelineio")
    old_p, new_p = tmp_path / "old.edl", tmp_path / "new.edl"
    old_p.write_text(_OLD_EDL_25, encoding="utf-8")
    new_p.write_text(_NEW_EDL_25, encoding="utf-8")
    old = read_cut(old_p, Report(), frame_rate=RATE_25)
    new = read_cut(new_p, Report(), frame_rate=RATE_25)
    set_path = _set_com_envelope(tmp_path)
    sha = _sha256(set_path)
    res = apply_reconform(set_path, diff_cuts(old, new, Report()), Report())
    assert res.written
    return old, new, set_path, res.output, sha


def test_apply_com_envelope_passa_no_verify(tmp_path: Path) -> None:
    """O apply que fatia o clip com envelope passa no verify."""
    old, new, origem, aplicado, sha = _aplicado_com_envelope(tmp_path)
    resultado = verify_reconform(
        old, new, origem, aplicado, Report(strict=False), source_sha256=sha
    )
    assert resultado.ok, resultado.problems


def _muda_o_valor_do_envelope(root) -> None:
    """Muda o valor de um evento do ClipEnvelope da peca fatiada."""
    clip = _clips_por_inicio(root, "Track 0")[1]
    env = clip.find("Envelopes/Envelopes/ClipEnvelope")
    assert env is not None
    for ev in env.find("Automation/Events"):
        if abs(float(ev.get("Time") or "0") - 2.0) < 1e-6:
            ev.set("Value", "0.5")
            return
    raise AssertionError("no clip envelope event at two beats")


def test_valor_do_envelope_mudado_e_pego(tmp_path: Path) -> None:
    """O verify pega o valor de um evento de envelope que mudou na peca."""
    old, new, origem, aplicado, _sha = _aplicado_com_envelope(tmp_path)
    mexido = _mexer(aplicado, _muda_o_valor_do_envelope)
    resultado = verify_reconform(old, new, origem, mexido, Report(strict=False))
    assert not resultado.ok
    assert "RECONFORM_VERIFY_CLIP_ENVELOPE" in _codigos(resultado)


def _raiz_do_repo() -> Path:
    """Raiz do repositorio, onde ficam o pacote e os testes."""
    return Path(__file__).resolve().parents[2]


def test_o_verify_nao_carrega_o_mapa_nem_o_diff() -> None:
    """O verify nao importa o mapa de tempo, as amostras nem o diff dos cortes."""
    raiz = _raiz_do_repo()
    caminhos = [str(raiz / "src"), str(raiz)]
    anterior = os.environ.get("PYTHONPATH")
    if anterior:
        caminhos.append(anterior)
    res = subprocess.run(
        [sys.executable, "-c", _CODIGO_IMPORTS],
        capture_output=True,
        text=True,
        cwd=str(raiz),
        env={**os.environ, "PYTHONPATH": os.pathsep.join(caminhos)},
        check=False,
    )
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == ""


def test_a_lista_de_mudancas_sai_antes_do_relatorio(tmp_path: Path, capsys) -> None:
    """A lista de mudancas e impressa antes da copia e do relatorio."""
    old, new = _edls(tmp_path)
    set_path = _build_spanning_set(tmp_path)
    assert main(["reconform", old, new, "--set", str(set_path), "--apply"]) == 0
    saida = capsys.readouterr().out
    assert "Old:" in saida
    assert "# Conversion report" in saida
    assert saida.index("Old:") < saida.index("# Conversion report")


def test_a_lista_de_mudancas_sai_antes_da_copia(tmp_path: Path, capsys, monkeypatch) -> None:
    """A lista de mudancas ja esta na tela quando a copia do set e gravada."""
    old, new = _edls(tmp_path)
    set_path = _build_spanning_set(tmp_path)
    antes: list[str] = []
    original = cli_reconform._gravar_set

    def _gravar(args, cl, old_cut, new_cut, report) -> bool:
        antes.append(capsys.readouterr().out)
        return original(args, cl, old_cut, new_cut, report)

    monkeypatch.setattr(cli_reconform, "_gravar_set", _gravar)
    assert main(["reconform", old, new, "--set", str(set_path), "--apply"]) == 0
    assert "Old:" in antes[0]
    assert (tmp_path / "review reconform.als").exists()
