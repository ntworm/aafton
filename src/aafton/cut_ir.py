"""Representacao intermediaria da lista de corte de imagem (V1).

Todo tempo aqui e um inteiro de frames na taxa de corte da lista:
``record_in`` e ``record_out`` sao contados em frames absolutos desde
``00:00:00:00``, e ``source_in`` e ``source_out`` sao frames absolutos no
timecode de origem do clip.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
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


def _speed_reduzida(valor: int | Fraction) -> Fraction:
    """Velocidade como fracao reduzida.

    O AAF grava o Speed Ratio com o par cru (o Avid escreve o 2x como
    2000000/1000000). A fracao do CutEvent e sempre reduzida, para que o
    denominador diga se a velocidade cabe em frames inteiros.
    """
    if isinstance(valor, Fraction):
        numerador, denominador = valor.numerator, valor.denominator
    else:
        numerador, denominador = int(valor), 1
    if denominador == 0:
        raise ValueError(f"invalid speed: {valor!r}")
    return Fraction(int(numerador), int(denominador))


@dataclass(frozen=True)
class CutEvent:
    """Evento da faixa principal de video (clip ou lacuna), em frames.

    `speed` e a constante de velocidade do plano, quando ele tem um efeito de
    warping: 1 quando o plano corre na velocidade normal. `whole_only` marca o
    plano que so pode ser movido inteiro, porque a velocidade nao e uma
    constante inteira ou porque uma transicao encosta nele.
    """

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
    speed: Fraction | None = Fraction(1)
    whole_only: bool = False

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
        if self.speed is not None and not isinstance(self.speed, (int, Fraction)):
            raise ValueError(f"cut event {self.index} has an invalid speed: {self.speed!r}")
        if self.speed is not None:
            object.__setattr__(self, "speed", _speed_reduzida(self.speed))
        if not isinstance(self.whole_only, bool):
            raise ValueError(
                f"cut event {self.index} has an invalid whole_only: {self.whole_only!r}"
            )
        if self.speed is None and not self.whole_only:
            raise ValueError(
                f"cut event {self.index} has no speed and is not a whole shot: "
                "a shot without speed can only be a whole shot"
            )

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
