"""Codificador LTC (SMPTE ST 12-1) com aritmetica exata.

Cada bit da palavra de 80 bits ocupa uma celula do frame: bit 0 ocupa a
celula inteira, bit 1 ocupa duas metades de celula. O sinal e uma onda
quadrada cujas bordas sao rampas lineares, para que o cruzamento de zero
caia exatamente no instante da transicao.

O tempo de cada transicao vem de um indice inteiro. Com D = num * 160, a
meia celula h do frame i esta no instante T = (i * 160 + h) * sample_rate *
den, em unidades de 1/D de sample, e o sample n fica no instante n * D. Nao
ha Fraction dentro do laco, e o sinal nao acumula desvio.
"""

from __future__ import annotations

import math
import struct
from collections.abc import Iterator
from fractions import Fraction
from pathlib import Path

from .media_extract import _bext_chunk
from .timecode import (
    TimecodeRate,
    frames_to_label,
    frames_to_samples_exact,
    label_to_frames,
    parse_label,
)

# Taxas que o LTC aceita no comando de linha.
RATES_LTC: dict[str, TimecodeRate] = {
    "23.976": TimecodeRate(24000, 1001),
    "24": TimecodeRate(24, 1),
    "25": TimecodeRate(25, 1),
    "29.97": TimecodeRate(30000, 1001),
    "29.97df": TimecodeRate(30000, 1001, drop_frame=True),
    "30": TimecodeRate(30, 1),
}

# Palavra de sync do SMPTE ST 12-1, dos bits 64 a 79.
SYNC_BITS = (0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 1)

# Largura total da rampa linear de cada transicao, em segundos (31,25 us).
RAMPA = Fraction(1, 32000)

# A rampa sobe e desce em metade dessa largura: 1/ESCALA de segundo.
ESCALA = 2 * RAMPA.denominator

# Amplitude maxima de um sample PCM 24 bits.
AMPLITUDE_MAX = 8388607

# Quantos frames o gerador de PCM junta em um bloco.
BLOCO_FRAMES = 250

# Faixa de taxa de audio aceita pelo LTC, em Hz.
TAXA_MINIMA = 8000
TAXA_MAXIMA = 384000

# Limite de bytes do campo de tamanho do RIFF (2**32).
LIMITE_RIFF = 2**32

# O fim da linha do timecode: 24 horas.
LIMITE_DE_HORAS = "24:00:00:00"


def parse_ltc_rate(texto: str) -> TimecodeRate:
    """Taxa de frames do LTC a partir do texto do comando."""
    chave = texto.strip().lower()
    if chave not in RATES_LTC:
        raise ValueError(f"unknown LTC rate: {texto!r}")
    return RATES_LTC[chave]


def _bits_bcd(valor: int, largura: int) -> list[int]:
    """Bits de um campo BCD, do menos significativo para o mais significativo."""
    return [(valor >> posicao) & 1 for posicao in range(largura)]


def _bit_polaridade(bits: list[int]) -> int:
    """Bit de polaridade: deixa par o numero de zeros da palavra."""
    zeros = sum(1 for bit in bits if bit == 0)
    return 1 if zeros % 2 else 0


def _posicao_polaridade(rate: TimecodeRate) -> int:
    """Posicao do bit de polaridade no layout da taxa."""
    return 59 if rate.nominal_fps == 25 else 27


def frame_bits(indice: int, rate: TimecodeRate) -> list[int]:
    """Os 80 bits de um frame LTC conforme o layout SMPTE ST 12-1."""
    horas, minutos, segundos, frames = parse_label(frames_to_label(indice, rate))
    bits = [0] * 80
    bits[0:4] = _bits_bcd(frames % 10, 4)
    bits[8:10] = _bits_bcd(frames // 10, 2)
    bits[10] = 1 if rate.drop_frame else 0
    bits[16:20] = _bits_bcd(segundos % 10, 4)
    bits[24:27] = _bits_bcd(segundos // 10, 3)
    bits[32:36] = _bits_bcd(minutos % 10, 4)
    bits[40:43] = _bits_bcd(minutos // 10, 3)
    bits[48:52] = _bits_bcd(horas % 10, 4)
    bits[56:58] = _bits_bcd(horas // 10, 2)
    bits[64:80] = list(SYNC_BITS)
    bits[_posicao_polaridade(rate)] = _bit_polaridade(bits)
    return bits


def _janela(primeiro: int, total: int, rate: TimecodeRate, sample_rate: int) -> tuple[int, int]:
    """Offset do primeiro sample da janela e tamanho dela, em samples."""
    inicio = frames_to_samples_exact(primeiro, rate, sample_rate)
    fim = frames_to_samples_exact(primeiro + total, rate, sample_rate)
    # Dois samples de folga: um so para a subida da primeira borda.
    offset = max(0, math.floor(inicio) - 2)
    return offset, math.ceil(fim) - offset


def _meias_celulas(bits: list[int]) -> Iterator[int]:
    """Indices das meias celulas que tem transicao, em ordem crescente."""
    for posicao, bit in enumerate(bits):
        yield 2 * posicao
        if bit:
            yield 2 * posicao + 1


def _constante(nivel: int, amplitude: float) -> bytes:
    """Os tres bytes do nivel constante do sinal."""
    return round(nivel * amplitude).to_bytes(3, "little", signed=True)


def _transicao(
    partes: list[bytes],
    cursor: int,
    nivel: int,
    transicao: int,
    D: int,
    meia_rampa: int,
    divisor: int,
    constantes: dict[int, bytes],
    amplitude: float,
    fim: int,
) -> tuple[int, int]:
    """Escreve a rampa de uma transicao e devolve o cursor e o nivel novos."""
    primeiro = (transicao * ESCALA - meia_rampa) // divisor + 1
    ultimo = (transicao * ESCALA + meia_rampa - 1) // divisor
    if primeiro > cursor:
        limite = min(primeiro, fim)
        if limite > cursor:
            partes.append(constantes[nivel] * (limite - cursor))
            cursor = limite
    for n in range(max(primeiro, cursor), min(ultimo, fim - 1) + 1):
        posicao = ((n * D - transicao) * ESCALA + meia_rampa) / (2 * meia_rampa)
        valor = round(nivel * (1 - 2 * posicao) * amplitude)
        partes.append(valor.to_bytes(3, "little", signed=True))
        cursor = n + 1
    if transicao < fim * D:
        nivel = -nivel
    return cursor, nivel


def _blocos_pcm(
    primeiro: int,
    total: int,
    rate: TimecodeRate,
    sample_rate: int,
    amplitude: float,
    offset: int,
    tamanho: int,
) -> Iterator[bytes]:
    """Gera o PCM da janela em blocos de frames, sem carregar tudo em memoria."""
    D = rate.num * 160
    passo = sample_rate * rate.den
    meia_rampa = sample_rate * D
    divisor = D * ESCALA
    constantes = {-1: _constante(-1, amplitude), 1: _constante(1, amplitude)}
    nivel = -1
    cursor = offset
    fim_janela = offset + tamanho
    for inicio in range(primeiro, primeiro + total, BLOCO_FRAMES):
        fim = min(inicio + BLOCO_FRAMES, primeiro + total)
        partes: list[bytes] = []
        for indice in range(inicio, fim):
            base = indice * 160 * passo
            for meia in _meias_celulas(frame_bits(indice, rate)):
                transicao = base + meia * passo
                cursor, nivel = _transicao(
                    partes,
                    cursor,
                    nivel,
                    transicao,
                    D,
                    meia_rampa,
                    divisor,
                    constantes,
                    amplitude,
                    fim_janela,
                )
        yield b"".join(partes)
    # A rampa da transicao seguinte entra na janela. Sem ela o render e o
    # arquivo discordariam no ultimo sample.
    partes = []
    cursor, nivel = _transicao(
        partes,
        cursor,
        nivel,
        (primeiro + total) * 160 * passo,
        D,
        meia_rampa,
        divisor,
        constantes,
        amplitude,
        fim_janela,
    )
    if partes:
        yield b"".join(partes)
    if cursor < fim_janela:
        yield constantes[nivel] * (fim_janela - cursor)


def _desempacotar(pcm: bytes) -> list[int]:
    """Converte PCM 24 bits little endian em uma lista de samples."""
    return [
        int.from_bytes(pcm[inicio : inicio + 3], "little", signed=True)
        for inicio in range(0, len(pcm), 3)
    ]


def render_ltc(
    primeiro: int,
    total: int,
    rate: TimecodeRate,
    sample_rate: int,
    level_dbfs: float = -18.0,
) -> tuple[int, list[int]]:
    """Sinal LTC de `total` frames a partir do frame `primeiro`.

    Devolve o offset, em samples, do primeiro sample do buffer, e as amostras.
    """
    amplitude = 10 ** (level_dbfs / 20) * AMPLITUDE_MAX
    offset, tamanho = _janela(primeiro, total, rate, sample_rate)
    blocos = _blocos_pcm(primeiro, total, rate, sample_rate, amplitude, offset, tamanho)
    return offset, _desempacotar(b"".join(blocos))


def _chunks(sample_rate: int, time_reference: int, descricao: str) -> tuple[bytes, bytes]:
    """Os chunks fmt e bext do WAV de LTC, que nao dependem do tamanho dos dados."""
    fmt = struct.pack("<4sIHHIIHH", b"fmt ", 16, 1, 1, sample_rate, sample_rate * 3, 3, 24)
    return fmt, _bext_chunk(time_reference, description=descricao)


def _tamanho_do_cabecalho(fmt: bytes, bext: bytes, dados: int) -> int:
    """Quantos bytes o cabecalho RIFF ocupa, com o byte de paridade dos dados."""
    return 12 + len(fmt) + len(bext) + 8 + dados % 2


def _cabecalho(fmt: bytes, bext: bytes, dados: int) -> bytes:
    """RIFF, fmt, bext e o inicio do chunk data, com os tamanhos ja calculados."""
    corpo = 4 + len(fmt) + len(bext) + 8 + dados + dados % 2
    return (
        b"RIFF"
        + struct.pack("<I", corpo)
        + b"WAVE"
        + fmt
        + bext
        + struct.pack("<4sI", b"data", dados)
    )


def _valida_nivel(level_dbfs: float) -> None:
    """Recusa um nivel acima de 0 dBFS, que nao existe em audio digital.

    A comparacao e negada para o nan cair aqui tambem: um nan passa por
    qualquer teste de faixa e viraria um arquivo de 654 bytes.
    """
    if not level_dbfs <= 0:
        raise ValueError("the LTC level must be <= 0 dBFS")


def _valida_inicio(primeiro: int) -> None:
    """Recusa um LTC que comeca antes de 00:00:00:00.

    O sinal nasce no primeiro sample do arquivo: um inicio negativo nao tem
    sample onde comecar, e o Live nao le um rotulo de timecode negativo.
    """
    if primeiro < 0:
        raise ValueError("the LTC cannot start before 00:00:00:00")


def _valida_limite_de_horas(primeiro: int, frames: int, rate: TimecodeRate) -> None:
    """Recusa um LTC que passa do limite de 24:00:00:00.

    O timecode de 24 horas e o fim da linha: um LTC que passa dele nao tem
    rotulo valido no Live.
    """
    limite = label_to_frames(LIMITE_DE_HORAS, rate)
    if primeiro + frames > limite:
        raise ValueError(f"the LTC would pass the limit of {LIMITE_DE_HORAS}")


def _valida_taxa(sample_rate: int) -> None:
    """Recusa uma taxa de audio fora da faixa aceita pelo LTC."""
    if not TAXA_MINIMA <= sample_rate <= TAXA_MAXIMA:
        raise ValueError(
            f"the LTC audio rate must be between {TAXA_MINIMA} and {TAXA_MAXIMA} Hz, "
            f"received {sample_rate}"
        )


def _valida_tamanho(dados: int, cabecalho: int) -> None:
    """Recusa um WAV que nao cabe no campo de tamanho do RIFF."""
    total = dados + cabecalho
    if total >= LIMITE_RIFF:
        raise ValueError(
            f"the LTC WAV would pass the RIFF limit of {LIMITE_RIFF} bytes: {total} bytes"
        )


def write_ltc_wav(
    path: str | Path,
    label: str,
    rate: TimecodeRate,
    frames: int,
    sample_rate: int = 48000,
    level_dbfs: float = -18.0,
) -> Path:
    """Grava um WAV PCM 24 bits com o sinal LTC e o chunk bext."""
    _valida_nivel(level_dbfs)
    _valida_taxa(sample_rate)
    primeiro = label_to_frames(label, rate)
    _valida_inicio(primeiro)
    _valida_limite_de_horas(primeiro, frames, rate)
    amplitude = 10 ** (level_dbfs / 20) * AMPLITUDE_MAX
    offset, tamanho = _janela(primeiro, frames, rate, sample_rate)
    # A descricao do BWF nomeia o primeiro frame de verdade: um rotulo
    # digitado com sinal negativo ("-00:00:00:00") vira o rotulo do frame
    # zero, que e o que o sinal descreve.
    descricao = f"LTC {frames_to_label(primeiro, rate)} {rate.num}/{rate.den}"
    fmt, bext = _chunks(sample_rate, offset, descricao)
    dados = 3 * tamanho
    _valida_tamanho(dados, _tamanho_do_cabecalho(fmt, bext, dados))
    caminho = Path(path)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with caminho.open("wb") as arquivo:
        arquivo.write(_cabecalho(fmt, bext, dados))
        blocos = _blocos_pcm(primeiro, frames, rate, sample_rate, amplitude, offset, tamanho)
        for bloco in blocos:
            arquivo.write(bloco)
        if dados % 2:
            arquivo.write(b"\x00")
    return caminho
