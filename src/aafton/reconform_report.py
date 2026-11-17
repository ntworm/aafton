"""Relatorios textual e CSV da lista de mudancas da reconformacao."""

from __future__ import annotations

import csv
from pathlib import Path

from .reconform_diff import Change, ChangeList
from .timecode import TimecodeRate, frames_to_label

CSV_HEADER: tuple[str, ...] = (
    "kind",
    "old_in",
    "old_out",
    "new_in",
    "new_out",
    "delta_frames",
    "source",
    "detail",
)


def _format_rate(rate: TimecodeRate) -> str:
    """Formata a taxa de frames para o cabecalho do relatorio."""
    if rate.den == 1:
        fps = str(rate.num)
    else:
        fps = f"{rate.num / rate.den:.3f}".rstrip("0").rstrip(".")
    suffix = "df" if rate.drop_frame else ""
    return f"{fps}{suffix} fps"


def _tc_label(frame: int | None, rate: TimecodeRate, empty: str) -> str:
    """Converte um frame em rotulo de timecode ou no texto de ausencia."""
    return empty if frame is None else frames_to_label(frame, rate)


def _clean_text(text: str) -> str:
    """Normaliza quebras de linha para manter uma mudanca por linha."""
    return " ".join(text.replace("\r", " ").replace("\n", " ").split())


def _format_change_line(change: Change, rate: TimecodeRate) -> str:
    """Formata uma entrada de mudanca em uma unica linha de texto."""
    prefix = "[AMBIGUOUS] " if change.kind == "ambiguous" else ""
    old_in = _tc_label(change.old_in, rate, "-")
    old_out = _tc_label(change.old_out, rate, "-")
    new_in = _tc_label(change.new_in, rate, "-")
    new_out = _tc_label(change.new_out, rate, "-")
    delta = "-" if change.delta_frames is None else f"{change.delta_frames:+d}"
    source = _clean_text(change.source) if change.source else "-"
    line = (
        f"{prefix}{change.kind} | old {old_in} -> {old_out} | "
        f"new {new_in} -> {new_out} | delta {delta} | source {source}"
    )
    detail = _clean_text(change.detail) if change.detail else ""
    return f"{line} | {detail}" if detail else line


def _sequencia(cl: ChangeList) -> str:
    """Linha do inicio da sequencia quando os dois cortes nao comecam no mesmo frame."""
    if cl.old_start_frames == cl.new_start_frames:
        return ""
    antigo = frames_to_label(cl.old_start_frames, cl.rate)
    novo = frames_to_label(cl.new_start_frames, cl.rate)
    return f"Sequence start: {antigo} -> {novo}"


def change_list_text(cl: ChangeList) -> str:
    """Gera o relatorio textual completo da lista de mudancas."""
    counts = ", ".join(f"{kind}={count}" for kind, count in cl.counts.items()) or "none"
    lines = [
        f"Old: {cl.old_path}",
        f"New: {cl.new_path}",
        f"Rate: {_format_rate(cl.rate)}",
        f"Counts: {counts}",
    ]
    sequencia = _sequencia(cl)
    if sequencia:
        lines.append(sequencia)
    if cl.ambiguous:
        lines.append("Status: AMBIGUOUS")
    if cl.changes:
        lines.append("")
        lines.extend(_format_change_line(change, cl.rate) for change in cl.changes)
    return "\n".join(lines) + "\n"


def write_change_list_csv(cl: ChangeList, path: str | Path) -> Path:
    """Grava a lista de mudancas em CSV UTF-8 com rotulos de timecode."""
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_HEADER)
        for change in cl.changes:
            writer.writerow(
                [
                    change.kind,
                    _tc_label(change.old_in, cl.rate, ""),
                    _tc_label(change.old_out, cl.rate, ""),
                    _tc_label(change.new_in, cl.rate, ""),
                    _tc_label(change.new_out, cl.rate, ""),
                    "" if change.delta_frames is None else str(change.delta_frames),
                    change.source or "",
                    change.detail,
                ]
            )
    return out_path
