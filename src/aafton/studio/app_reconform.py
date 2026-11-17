"""Aba Reconform do Studio: change list entre dois cortes e copia do set conformada."""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..cut_read import read_cut
from ..reconform_apply import apply_reconform
from ..reconform_diff import ChangeList, diff_cuts
from ..reconform_locators import write_change_locators
from ..reconform_verify import verify_reconform
from ..report import Report
from ..sidecar import read_anchor, sidecar_path_for
from ..timecode import frames_to_label
from . import app_convert, app_view
from .app_messages import falha_do_motor
from .app_read import error, taxa_edl

DIRECTION = "reconform"
_SUFIXOS = {".edl", ".xml", ".aaf"}
_ROTULOS = {
    "move": "Moved",
    "shift": "Shifted",
    "insert": "Added",
    "delete": "Removed",
    "trim_head": "Head trimmed",
    "trim_tail": "Tail trimmed",
    "transition": "Transition",
    "ambiguous": "Ambiguous",
}
_ESTADOS = {"ambiguous": "err", "delete": "warn", "insert": "warn"}


def _caminho(payload: dict, chave: str) -> Path | None:
    texto = str(payload.get(chave) or "").strip()
    return Path(texto) if texto else None


def _validar_corte(caminho: Path | None, nome: str) -> dict | None:
    if caminho is None:
        return error("bad_request", f"The {nome} cut is required.")
    if not caminho.is_file():
        return error("not_found", f"The {nome} cut {caminho} was not found.")
    if caminho.suffix.lower() not in _SUFIXOS:
        return error("unsupported", f"The {nome} cut must be an EDL, an FCP7 XML or an AAF.")
    return None


def _taxa(payload: dict, conjunto: Path | None):
    taxa, problema = taxa_edl(payload.get("edl_rate"))
    if taxa is None and problema is None and conjunto is not None:
        if sidecar_path_for(conjunto).exists():
            taxa = read_anchor(conjunto).timecode_rate
    return taxa, problema


def _ler(payload: dict, report: Report):
    """Os dois cortes e a change list; ou a resposta de erro."""
    antigo, novo = _caminho(payload, "input"), _caminho(payload, "new_cut")
    for caminho, nome in ((antigo, "old"), (novo, "new")):
        falha = _validar_corte(caminho, nome)
        if falha is not None:
            return None, falha
    taxa, problema = _taxa(payload, _caminho(payload, "set"))
    if problema is not None:
        return None, error("bad_request", problema)
    old = read_cut(antigo, report, frame_rate=taxa)
    new = read_cut(novo, report, frame_rate=taxa)
    cl = diff_cuts(old, new, report) if old is not None and new is not None else None
    if cl is None:
        primeiro = next((i for i in report.issues if i.severity == "error"), None)
        texto = primeiro.message if primeiro else "The cuts could not be compared."
        return None, error("unreadable", texto)
    return (old, new, cl), None


def _tc(frame: int | None, cl: ChangeList) -> str:
    return frames_to_label(frame, cl.rate) if frame is not None else "-"


def _linhas(cl: ChangeList) -> list[dict]:
    linhas = []
    for indice, mudanca in enumerate(cl.changes):
        delta = mudanca.delta_frames
        linhas.append(
            {
                "index": indice,
                "kind": _ROTULOS.get(mudanca.kind, mudanca.kind),
                "old": _tc(mudanca.old_in, cl),
                "new": _tc(mudanca.new_in, cl),
                "delta": "" if delta is None else f"{delta:+d}",
                "source": mudanca.source or "",
                "state": {"st": _ESTADOS.get(mudanca.kind, "ok"), "label": mudanca.detail},
            }
        )
    return linhas


def _fatos(cl: ChangeList) -> list[dict]:
    fatos = [app_view.fact("Changes", str(len(cl.changes)), app_view.rate_label(cl.rate))]
    for tipo, quantos in sorted(cl.counts.items()):
        st = "err" if tipo == "ambiguous" else None
        fatos.append(app_view.fact(_ROTULOS.get(tipo, tipo), str(quantos), None, st))
    return fatos


def read(payload: dict) -> dict:
    """Compara os dois cortes e devolve a change list para a tela."""
    report = Report(strict=False)
    try:
        lido, falha = _ler(payload, report)
    except Exception as exc:  # noqa: BLE001 - a mensagem do motor e a resposta
        return error("unreadable", falha_do_motor(exc, _caminho(payload, "input"), "read"))
    if falha is not None:
        return falha
    _old, _new, cl = lido
    conjunto = _caminho(payload, "set")
    sugestao = str(conjunto.with_name(f"{conjunto.stem} reconform.als")) if conjunto else ""
    return {
        "ok": True,
        "direction": DIRECTION,
        "ambiguous": cl.ambiguous,
        "facts": _fatos(cl),
        "items": _linhas(cl),
        "notes": app_view.notes(report.issues),
        "suggest": {"output": sugestao},
    }


def _escrever(payload: dict, lido, report: Report):
    old, new, cl = lido
    conjunto = _caminho(payload, "set")
    saida = str(payload.get("output") or "").strip() or None
    opcoes = payload.get("options") or {}
    if opcoes.get("apply") is False:
        res = write_change_locators(conjunto, cl, report, output=saida)
        return res.written, res.output, None
    sha = hashlib.sha256(conjunto.read_bytes()).hexdigest()
    lado = sidecar_path_for(conjunto)
    sha_sidecar = hashlib.sha256(lado.read_bytes()).hexdigest() if lado.exists() else None
    res = apply_reconform(conjunto, cl, report, output=saida)
    if not res.written:
        return False, res.output, None
    verificado = verify_reconform(
        old, new, conjunto, res.output, report, source_sha256=sha, source_sidecar_sha256=sha_sidecar
    )
    return True, res.output, verificado


def _veredito(escrito: bool, verificado, report: Report) -> dict:
    if not escrito:
        return {"state": "err", "word": "Not written", "detail": ["The set copy was not written."]}
    if verificado is None:
        return {"state": "ok", "word": "Locators written", "detail": ["The set is unchanged."]}
    if not verificado.ok:
        detalhe = [p["detail"] for p in verificado.problems[:6]]
        return {"state": "err", "word": "Verify failed", "detail": detalhe}
    detalhe = [f"{verificado.checked_clips} clips checked against the new cut, sample exact."]
    return {"state": "ok", "word": "Conformed", "detail": detalhe}


def run(payload: dict) -> dict:
    """Escreve a copia do set: so com locators, ou conformada e verificada."""
    conjunto = _caminho(payload, "set")
    if conjunto is None or not conjunto.is_file():
        return error("bad_request", "Choose the Live set to conform.")
    report = Report(strict=False)
    try:
        lido, falha = _ler(payload, report)
        if falha is not None:
            return falha
        escrito, saida, verificado = _escrever(payload, lido, report)
    except Exception as exc:  # noqa: BLE001 - a mensagem do motor e a resposta
        app_convert.LAST.pop(DIRECTION, None)
        return error("failed", falha_do_motor(exc, conjunto, "write"))
    if escrito:
        app_convert.LAST[DIRECTION] = {"output": saida, "report": report}
    else:
        app_convert.LAST.pop(DIRECTION, None)
    return {
        "ok": True,
        "written": escrito,
        "output": str(saida) if saida else None,
        "verdict": _veredito(escrito, verificado, report),
        "notes": app_view.notes(report.issues),
    }
