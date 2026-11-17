"""Representacao intermediaria da lista de corte de imagem (V1).

Todo tempo aqui e um inteiro de frames na taxa de corte da lista:
``record_in`` e ``record_out`` sao contados em frames absolutos desde
``00:00:00:00``, e ``source_in`` e ``source_out`` sao frames absolutos no
timecode de origem do clip.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .timecode import TimecodeRate

CutKind = Literal["clip", "gap"]


def normalize_source(reel: str | None, filename: str | None = None) -> str | None:
    """Chave normalizada de correspondencia entre o corte e o som.

    Usa o reel quando ele existe; senao usa o nome do arquivo de midia sem a
    extensao. O resultado sai em maiusculas e sem espacos.
    """
    if reel is not None:
        chave = "".join(reel.split()).upper()
        if chave:
            return chave
    if not filename:
        return None
    nome = filename.replace("\\", "/").rstrip("/").split("/")[-1]
    if not nome:
        return None
    base = Path(nome).stem
    chave = "".join(base.split()).upper()
    return chave or None


@dataclass(frozen=True)
class CutEvent:
    """Evento da faixa principal de video (clip ou lacuna), em frames."""

    index: int
    kind: CutKind
    source: str | None
    reel: str | None
    source_in: int | None
    source_out: int | None
    record_in: int
    record_out: int
    transition_start: int | None = None
    transition_frames: int = 0

    def __post_init__(self) -> None:
        if self.kind not in ("clip", "gap"):
            raise ValueError(f"unknown cut event kind: {self.kind!r}")
        if self.record_out < self.record_in:
            raise ValueError(
                f"cut event {self.index} ends before it starts: "
                f"{self.record_out} < {self.record_in}"
            )
        if self.transition_frames < 0:
            raise ValueError(f"transition_frames cannot be negative: {self.transition_frames}")

    @property
    def duration_frames(self) -> int:
        """Duracao do evento na linha de tempo, em frames."""
        return self.record_out - self.record_in


@dataclass(frozen=True)
class CutIR:
    """Lista de corte de imagem lida de um EDL ou XML do FCP 7."""

    path: str
    rate: TimecodeRate
    start_frames: int
    events: list[CutEvent] = field(default_factory=list)
