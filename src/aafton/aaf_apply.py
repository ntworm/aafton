"""The apply: writes into a copy of an AAF the slips and cuts that come in a JSON.

The PhaseSlip standalone sends, per clip id of the inspect, the final list of
the pieces of that clip, each with its start and end on the timeline and its
offset in the source, all in absolute samples. One piece is a slip or a trim;
several are a split. The apply reads the AAF, swaps each clip for its pieces,
writes the AAF with the media as it came in (linked or embedded) and reads the
output back to prove that every piece landed where it was asked.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from .aaf_apply_pieces import ApplyRefused, parse_edits, swap_clips
from .aaf_inspect_media import embedded_media_ids, errors_by_stage, read_with_media
from .aaf_read import read_aaf
from .aaf_write import write_aaf
from .report import Report


def _same_file(entrada: Path, saida: Path) -> bool:
    """True when the output would be the input itself."""
    if entrada.resolve() == saida.resolve():
        return True
    return saida.exists() and os.path.samefile(entrada, saida)


def _media_mode(path: Path, session) -> str:
    """``embed`` when every media used by a clip is inside the AAF, else ``link``."""
    used = {clip.media_id for clip in session.all_clips() if clip.media_id is not None}
    inside = embedded_media_ids(path, session) & used
    if inside and inside != used:
        raise ApplyRefused(
            "APPLY_MIXED_MEDIA",
            "the AAF mixes embedded and linked media; the apply writes one or the other",
        )
    return "embed" if inside else "link"


def _track_pieces(track) -> list[tuple[int, int, int | None]]:
    """The (start, end, offset) of the clips of a track, in timeline order."""
    return sorted(
        (c.timeline_start_samples, c.timeline_end_samples, c.source_offset_samples)
        for c in track.clips
    )


def _track_fades(track) -> list[tuple[int, int]]:
    """The fade lengths of the clips of a track, in timeline order."""
    return [
        (
            c.fade_in.duration_samples if c.fade_in else 0,
            c.fade_out.duration_samples if c.fade_out else 0,
        )
        for c in sorted(track.clips, key=lambda c: c.timeline_start_samples)
    ]


def _check_readback(saida: Path, session, edited_tracks: list, mode: str, report: Report) -> None:
    """Reads the output back and compares the edited tracks with what was written."""
    leitura = Report(strict=False)
    try:
        relida = read_aaf(saida, leitura)
    except Exception as exc:  # noqa: BLE001 - an output that does not open fails the apply
        raise ApplyRefused("APPLY_READBACK_MISMATCH", f"the output does not read back: {exc}")
    if leitura.incomplete:
        first = next(i for i in leitura.issues if i.severity == "error")
        raise ApplyRefused(
            "APPLY_READBACK_MISMATCH", f"the output reads with errors: {first.message}"
        )
    if relida.name != session.name:
        raise ApplyRefused(
            "APPLY_READBACK_MISMATCH",
            f"the composition of the output is {relida.name!r}, not {session.name!r}",
        )
    _check_mode(saida, relida, mode)
    for track in edited_tracks:
        iguais = [t for t in relida.tracks if t.name == track.name]
        if len(iguais) != 1 or _track_pieces(iguais[0]) != _track_pieces(track):
            raise ApplyRefused(
                "APPLY_READBACK_MISMATCH",
                f"the track {track.name!r} does not read back with the pieces that were written",
            )
        if _track_fades(iguais[0]) != _track_fades(track):
            report.warning(
                "APPLY_READBACK_FADE",
                f"the fades of the track {track.name!r} read back different from the ones written",
                {"track": track.name},
            )


def _check_mode(saida: Path, relida, mode: str) -> None:
    """The output keeps the media as it came in: linked stays linked, embedded stays embedded."""
    used = {clip.media_id for clip in relida.all_clips() if clip.media_id is not None}
    inside = embedded_media_ids(saida, relida) & used
    if inside != (used if mode == "embed" else set()):
        raise ApplyRefused(
            "APPLY_READBACK_MISMATCH", f"the output does not keep the media {mode}ed as it came in"
        )


def _aux_media_path(saida: Path) -> Path:
    """Where the link writer puts the mono files it makes beside the output."""
    return saida.parent / f"{saida.stem} Media"


def _aux_media_dir(saida: Path) -> str | None:
    """The folder of mono files the link writer made beside the output, if any."""
    pasta = _aux_media_path(saida)
    return str(pasta.absolute()) if pasta.is_dir() else None


def _discard_output(saida: Path, had_aux_dir: bool) -> None:
    """Removes what a refused apply wrote: the AAF and a media folder it created."""
    saida.unlink(missing_ok=True)
    pasta = _aux_media_path(saida)
    if not had_aux_dir and pasta.is_dir():
        shutil.rmtree(pasta, ignore_errors=True)


def _set_aside(saida: Path) -> Path | None:
    """Moves an output that already exists aside, so a refusal can put it back."""
    if not saida.is_file():
        return None
    backup = saida.with_name(saida.name + ".aafton-backup")
    os.replace(saida, backup)
    return backup


def _restore(backup: Path | None, saida: Path) -> None:
    """Puts back the output that existed before a refused apply."""
    if backup is not None and backup.is_file():
        os.replace(backup, saida)


def _read_for_apply(entrada: Path, extraidos: str, report: Report):
    """Reads the AAF and its media; refuses what the apply cannot write back."""
    session, read_issues = read_with_media(entrada, report, extract_dir=extraidos)
    read_errors = [e for e in errors_by_stage(report, read_issues) if e["stage"] == "read"]
    if session is None or read_errors:
        message = read_errors[0]["message"] if read_errors else "the AAF could not be read"
        # The reader already put its error (AAF_UNREADABLE or the reading one) in the report.
        raise ApplyRefused("APPLY_READ_ERROR", message, reported=True)
    # A media that does not resolve only refuses the clips that use it
    # (checked per edited clip); for the rest of the AAF it is a warning.
    for issue in report.issues[read_issues:]:
        if issue.severity == "error":
            issue.severity = "warning"
    if session.unsupported_export_features:
        raise ApplyRefused(
            "APPLY_UNSUPPORTED_FEATURES",
            "the AAF has what the aafton does not write back: "
            + "; ".join(session.unsupported_export_features),
        )
    return session


def _write(session, saida: Path, mode: str, report: Report) -> None:
    """Writes the AAF; on an error of the writing no output is left."""
    escrita = Report(strict=False)
    try:
        write_aaf(session, saida, escrita, media=mode, composition_name=session.name)
    except Exception as exc:  # noqa: BLE001 - the apply answers with its JSON, never a traceback
        saida.unlink(missing_ok=True)
        raise ApplyRefused("APPLY_WRITE_ERROR", f"the AAF could not be written: {exc}") from exc
    report.issues.extend(escrita.issues)
    if escrita.incomplete or not saida.is_file():
        first = next((i for i in escrita.issues if i.severity == "error"), None)
        # The link writer publishes the file even with an error in a report
        # that is not strict: the apply removes it.
        saida.unlink(missing_ok=True)
        raise ApplyRefused(
            "APPLY_WRITE_ERROR", first.message if first else "the AAF was not written"
        )


def _write_and_check(entrada: Path, saida: Path, edicoes: dict, report: Report) -> dict:
    """Reads, swaps, writes and reads back; raises ApplyRefused on any refusal."""
    with tempfile.TemporaryDirectory(prefix="aafton-apply-") as extraidos:
        session = _read_for_apply(entrada, extraidos, report)
        mode = _media_mode(entrada, session)
        edited_tracks = swap_clips(session, edicoes, report)
        had_aux_dir = _aux_media_path(saida).exists()
        backup = _set_aside(saida)
        try:
            _write(session, saida, mode, report)
            # The expected pieces come from the session after the writing,
            # which trims in place the clip that passes the end of its media.
            _check_readback(saida, session, edited_tracks, mode, report)
        except ApplyRefused:
            _discard_output(saida, had_aux_dir)
            _restore(backup, saida)
            raise
        if backup is not None:
            backup.unlink(missing_ok=True)
    return {"mode": mode, "clips_changed": len(edicoes), "aux_media_dir": _aux_media_dir(saida)}


def apply_edits(entrada: str | Path, edicoes: dict, saida: str | Path, report: Report) -> dict:
    """Applies the edits and returns the JSON of the command; ``ok`` says if it wrote."""
    entrada, saida = Path(entrada), Path(saida)
    resultado = {"ok": False, "output": str(saida), "mode": None, "clips_changed": 0}
    resultado["aux_media_dir"] = None
    try:
        if not entrada.is_file():
            raise ApplyRefused("APPLY_INPUT_MISSING", f"the input AAF does not exist: {entrada}")
        if _same_file(entrada, saida):
            raise ApplyRefused("APPLY_SAME_FILE", "the output cannot be the input AAF")
        pedidos = parse_edits(edicoes)
        resultado.update(_write_and_check(entrada, saida, pedidos, report))
        resultado["ok"] = True
    except ApplyRefused as exc:
        if not exc.reported:
            report.error(exc.code, str(exc), exc.context)
    resultado["warnings"] = [
        {"code": i.code, "message": i.message} for i in report.issues if i.severity == "warning"
    ]
    resultado["errors"] = [
        {"code": i.code, "message": i.message} for i in report.issues if i.severity == "error"
    ]
    if resultado["errors"]:
        resultado["ok"] = False
    return resultado
