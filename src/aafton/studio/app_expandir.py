"""Production audio folder of the AAF to Live direction: the sibling tracks.

With a folder, the read places the sibling tracks of the engine in the IR of
the session without writing a file, and the sheet lists each one right after
its owner track. The convert passes the folder to the import, and the verdict
says what the verify measured on the sibling clips. Without a folder nothing
here runs and the direction answers as it always did.
"""

from __future__ import annotations

from ..expandir import Plano, preview_production_channels
from ..ir import EXPANDED_MARK, SessionIR, TrackIR
from ..report import Report
from . import app_view

# The state of a sibling row and the mark of an owner row.
FROM_RECORDER = {"st": "ok", "label": "From recorder"}
READY_GROUP = f"Ready {app_view.MIDDLE_DOT} group"


def folder(payload: dict) -> str | None:
    """The production audio folder of the request; None when the field is empty."""
    texto = str(payload.get("production_audio") or "").strip()
    return texto or None


def preview(sessao: SessionIR, pasta: str, report: Report) -> Plano:
    """The sibling tracks of the folder, placed in the session; ValueError when refused."""
    return preview_production_channels(sessao, pasta, report)


def fact(plano: Plano) -> dict:
    """The Production audio fact: the takes found and how many gain channels."""
    takes = app_view.plural(len(plano.takes), "take", "takes")
    com = sum(1 for take in plano.takes.values() if take.faltando)
    sub = f"{com} with channels to add" if com else "none to add"
    return app_view.fact("Production audio", f"{takes} found", sub)


def is_sibling(track: TrackIR) -> bool:
    """True for a sibling track the expansion added."""
    return (getattr(track, "raw", None) or {}).get("expanded") == EXPANDED_MARK


def mark_rows(linhas: list[dict], sessao: SessionIR) -> list[dict]:
    """The rows of the sheet with the siblings marked under their owner.

    An editor track keeps the number it has without the folder; a sibling has
    no number of its own and an index after every editor track, so that each
    row still opens its own clips.
    """
    proximo = sum(1 for track in sessao.tracks if not is_sibling(track))
    numero, dona = 0, None
    for linha, track in zip(linhas, sessao.tracks):
        if not is_sibling(track):
            linha.update(index=numero, sibling=False)
            numero, dona = numero + 1, linha
            continue
        linha.update(index=proximo, sibling=True, state=dict(FROM_RECORDER))
        proximo += 1
        if dona is not None and not dona.get("group"):
            dona["group"] = True
            dona["state"] = _estado_da_dona(dona["state"])
    return linhas


def _estado_da_dona(estado: dict) -> dict:
    """The state of an owner track: ready in a group, or its notes in a group."""
    if estado.get("st") == "ok":
        return {"st": "ok", "label": READY_GROUP}
    return {**estado, "label": f"{estado.get('label', '')} {app_view.MIDDLE_DOT} group"}


def verdict_lines(result) -> list[str]:
    """The lines of the siblings in the verdict, as the verify measured them.

    Checked against the recorder, the sibling clips are one sentence: how many
    were added, equal to their channel, or the failure of the verify. Any other
    sibling line of the verify is kept as it is.
    """
    linhas = list(getattr(result, "expanded_lines", None) or [])
    medida = getattr(result, "sibling_clips", None)
    if not medida:
        return linhas
    if medida.get("differ"):
        return [str(medida.get("text") or "")]
    clips = app_view.plural(int(medida.get("checked") or 0), "clip", "clips")
    return [f"{clips} added from the recorder, equal to their channel"]
