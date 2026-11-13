"""Conversoes de timecode SMPTE com aritmetica exata.

Este modulo importa apenas a biblioteca padrao e nao faz I/O. Toda a
matematica de tempo do projeto vive aqui, e e aqui que ela e testada.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from fractions import Fraction

DROP_FRAME_RATES = frozenset({(30000, 1001), (60000, 1001), (120000, 1001)})


def exact_edit_rate(rate: object) -> Fraction:
    """Converte qualquer especificacao de edit_rate/frame_rate em Fraction exata.

    Trata taxas fracionarias padrao de cinema e video (23.976, 29.97, 59.94)
    sem perda de precisao ou drift acumulado em conversoes de samples.
    """
    if isinstance(rate, TimecodeRate):
        return rate.fps

    if isinstance(rate, str):
        s = rate.strip()
        if s in ("23.976", "23.98"):
            return Fraction(24000, 1001)
        if s in ("29.97", "29.970"):
            return Fraction(30000, 1001)
        if s in ("59.94", "59.940"):
            return Fraction(60000, 1001)
        if s in ("47.952", "47.95"):
            return Fraction(48000, 1001)
        if s in ("119.88", "119.880"):
            return Fraction(120000, 1001)
        if "/" in s:
            return Fraction(s)
        try:
            val = float(s)
            return exact_edit_rate(val)
        except ValueError:
            return Fraction(s)

    if isinstance(rate, (float, int)):
        fval = float(rate)
        if abs(fval - 24000 / 1001) < 1e-4 or abs(fval - 23.976) < 1e-4 or abs(fval - 23.98) < 1e-4:
            return Fraction(24000, 1001)
        if abs(fval - 30000 / 1001) < 1e-4 or abs(fval - 29.97) < 1e-4:
            return Fraction(30000, 1001)
        if abs(fval - 60000 / 1001) < 1e-4 or abs(fval - 59.94) < 1e-4:
            return Fraction(60000, 1001)
        if abs(fval - 48000 / 1001) < 1e-4 or abs(fval - 47.952) < 1e-4:
            return Fraction(48000, 1001)
        if abs(fval - 120000 / 1001) < 1e-4 or abs(fval - 119.88) < 1e-4:
            return Fraction(120000, 1001)
        if isinstance(rate, int):
            return Fraction(rate, 1)
        return Fraction.from_float(fval)

    if hasattr(rate, "numerator") and hasattr(rate, "denominator"):
        num, den = rate.numerator, rate.denominator
        if (num, den) in ((23976, 1000), (2997, 100), (5994, 100)):
            if num == 23976:
                return Fraction(24000, 1001)
            elif num == 2997:
                return Fraction(30000, 1001)
            else:
                return Fraction(60000, 1001)
        return Fraction(num, den)

    if hasattr(rate, "num") and hasattr(rate, "den"):
        return Fraction(rate.num, rate.den)

    return Fraction(rate)  # type: ignore[arg-type]


@dataclass(frozen=True)
class TimecodeRate:
    """Taxa de frames como fracao exata.

    23.976 Ac 24000/1001, 29.97 Ac 30000/1001. Guardar como fracao em vez de
    float Ac o que permite converter frames para samples sem erro acumulado.
    """

    num: int
    den: int
    drop_frame: bool = False

    def __post_init__(self) -> None:
        if self.den not in (1, 1001):
            raise ValueError(f"the frame rate denominator must be 1 or 1001, received {self.den}")
        if self.num <= 0:
            raise ValueError(f"the frame rate numerator must be positive, received {self.num}")
        if self.drop_frame and (self.num, self.den) not in DROP_FRAME_RATES:
            raise ValueError(
                f"drop frame only exists in 30000/1001, 60000/1001 and 120000/1001, "
                f"received {self.num}/{self.den}"
            )

    @property
    def fps(self) -> Fraction:
        """Taxa real, exata."""
        return Fraction(self.num, self.den)

    @property
    def nominal_fps(self) -> int:
        """Taxa usada na contagem de rA3tulos SMPTE. 29.97 conta como 30."""
        return round(self.fps)

    @property
    def dropped_per_minute(self) -> int:
        """Quantos numeros de frame sao pulados por minuto em drop frame."""
        if not self.drop_frame:
            return 0
        return self.nominal_fps // 15


_LABEL_RE = re.compile(r"^(\d{2,3}):(\d{2}):(\d{2})[:;](\d{2,3})$")


def parse_label(label: str) -> tuple[int, int, int, int]:
    """Separa um rotulo SMPTE em horas, minutos, segundos e frames.

    Validacao aqui e apenas sintatica. Se o rotulo existe de fato na taxa de
    frames alvo, incluindo as regras de drop frame, quem responde e
    label_to_frames. Nao trate a tupla devolvida como validada.
    """
    clean = label.strip().lstrip("-")
    match = _LABEL_RE.match(clean)
    if match is None:
        raise ValueError(f"invalid timecode label: {label!r}")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def label_to_frames(label: str, rate: TimecodeRate) -> int:
    """Converte um rotulo SMPTE no indice absoluto do frame."""
    clean = label.strip()
    is_neg = clean.startswith("-")
    if is_neg:
        clean = clean[1:]
    hh, mm, ss, ff = parse_label(clean)
    nominal = rate.nominal_fps
    if ff >= nominal:
        raise ValueError(f"frame {ff} does not exist at a nominal rate of {nominal}")
    if ss >= 60 or mm >= 60:
        raise ValueError(f"timecode label out of range: {label!r}")

    total_minutes = hh * 60 + mm
    dropped = rate.dropped_per_minute
    if dropped and ss == 0 and mm % 10 != 0 and ff < dropped:
        raise ValueError(f"the label {label!r} does not exist in drop frame")

    frames = ((total_minutes * 60) + ss) * nominal + ff
    if dropped:
        frames -= dropped * (total_minutes - total_minutes // 10)
    return -frames if is_neg else frames


def frames_to_label(frame: int, rate: TimecodeRate) -> str:
    """Converte o indice absoluto do frame no rotulo SMPTE."""
    if frame < 0:
        return "-" + frames_to_label(-frame, rate)

    nominal = rate.nominal_fps
    dropped = rate.dropped_per_minute

    if dropped:
        frames_per_ten_minutes = nominal * 600 - 9 * dropped
        frames_per_minute = nominal * 60 - dropped
        tens, remainder = divmod(frame, frames_per_ten_minutes)
        if remainder < nominal * 60:
            extra_minutes = 0
            within_minute = remainder
        else:
            remainder -= nominal * 60
            extra_minutes = 1 + remainder // frames_per_minute
            within_minute = remainder % frames_per_minute + dropped
        total_minutes = tens * 10 + extra_minutes
        hh, mm = divmod(total_minutes, 60)
        ss, ff = divmod(within_minute, nominal)
    else:
        ff = frame % nominal
        seconds_total = frame // nominal
        ss = seconds_total % 60
        minutes_total = seconds_total // 60
        mm = minutes_total % 60
        hh = minutes_total // 60

    separator = ";" if rate.drop_frame else ":"
    return f"{hh:02d}:{mm:02d}:{ss:02d}{separator}{ff:02d}"


def samples_per_frame(rate: TimecodeRate, sample_rate: int) -> Fraction:
    """Quantos samples cabem em um frame. Pode nao ser inteiro."""
    return Fraction(sample_rate * rate.den, rate.num)


def frames_to_samples_exact(frame: int, rate: TimecodeRate, sample_rate: int) -> Fraction:
    """Posicao em samples de um indice de frame, sem arredondar."""
    return Fraction(frame * sample_rate * rate.den, rate.num)


def samples_to_frames_exact(samples: Fraction | int, rate: TimecodeRate, sample_rate: int) -> int:
    """Indice do frame que contem a posicao dada em samples."""
    frames = Fraction(samples) * Fraction(rate.num, sample_rate * rate.den)
    return int(frames)


def label_to_samples(label: str, rate: TimecodeRate, sample_rate: int) -> Fraction:
    """Posicao em samples de um rotulo SMPTE, sem arredondar."""
    return frames_to_samples_exact(label_to_frames(label, rate), rate, sample_rate)


def samples_to_label(samples: Fraction | int, rate: TimecodeRate, sample_rate: int) -> str:
    """Rotulo SMPTE do frame que contem a posicao dada."""
    frames_frac = Fraction(samples) * Fraction(rate.num, sample_rate * rate.den)
    return frames_to_label(round(frames_frac), rate)


def quantize_samples(samples: Fraction | int) -> tuple[int, Fraction]:
    """Transforma uma posicao fracionaria em inteiro de samples.

    Retorna o inteiro e o residuo, definido como `inteiro - original`. Um
    residuo diferente de zero significa que a taxa de frames nao fecha em
    sample inteiro, e quem chama tem a obrigacao de registrar isso no
    relatorio de conversao em vez de descartar.

    Esta e a unica funcao do projeto que pode arredondar tempo.
    """
    exact = Fraction(samples)
    rounded = round(exact)
    return int(rounded), Fraction(rounded) - exact
