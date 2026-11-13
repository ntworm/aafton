"""Frame de inicio do timecode gravado no AAF.

A posicao de inicio da timeline vem em samples e nem sempre cai em um frame
inteiro. Truncar esse residuo faz o AAF comecar um frame antes e traz todos
os clips um frame para tras, entao o frame mais proximo vence quando a
distancia e de meio sample ou menos.
"""

import math
from fractions import Fraction

from .timecode import TimecodeRate

DISTANCIA_MAXIMA_SAMPLE = Fraction(1, 2)


def frames_exatos(samples, rate: TimecodeRate, sample_rate: int) -> Fraction:
    """Posicao em frames de uma posicao em samples, sem arredondar."""
    return Fraction(samples) * Fraction(rate.num, sample_rate * rate.den)


def samples_por_frame(rate: TimecodeRate, sample_rate: int) -> Fraction:
    """Quantos samples cabem em um frame. Pode nao ser inteiro."""
    return Fraction(sample_rate * rate.den, rate.num)


def frame_de_inicio(samples, rate: TimecodeRate, sample_rate: int) -> tuple[int, bool]:
    """Frame de inicio do timecode e se o inicio foi truncado.

    Quando a distancia ate o frame mais proximo e de meio sample ou menos, o
    frame mais proximo e o frame certo: truncar nesse caso comeca o AAF um
    frame antes e devolve todos os clips um frame para tras. Acima disso a
    truncacao continua valendo e o segundo valor do par avisa o chamador.
    """
    try:
        exato = frames_exatos(samples, rate, sample_rate)
    except ValueError:
        return samples * rate.num // (sample_rate * rate.den), True
    mais_proximo = math.floor(exato + Fraction(1, 2))
    distancia = abs(exato - mais_proximo) * samples_por_frame(rate, sample_rate)
    if distancia <= DISTANCIA_MAXIMA_SAMPLE:
        return mais_proximo, False
    return int(exato), True
