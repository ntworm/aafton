"""Testes do codificador LTC (SMPTE ST 12-1) com decodificador independente.

O decodificador deste arquivo foi escrito so a partir do layout da norma e nao
usa nenhuma funcao de aafton.ltc. Os valores esperados vem de timecode.py.
"""

import math
import wave

import pytest

from aafton.cli import main
from aafton.ltc import frame_bits, parse_ltc_rate, render_ltc, write_ltc_wav
from aafton.media_extract import parse_bext
from aafton.timecode import (
    TimecodeRate,
    frames_to_label,
    frames_to_samples_exact,
    label_to_frames,
    label_to_samples,
    parse_label,
    samples_per_frame,
)

SR = 48000
SR_ALTO = 192000
SYNC = (0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 1)
# Em intervalos: bit 0 e uma celula cheia (F) e bit 1 sao duas meias celulas (H).
# O padrao abaixo cobre os bits 64 a 78 da palavra de sync.
PADRAO_SYNC = "FF" + "H" * 24 + "F"
TAXA_23976 = TimecodeRate(24000, 1001)
TAXA_24 = TimecodeRate(24, 1)
TAXA_25 = TimecodeRate(25, 1)
TAXA_2997 = TimecodeRate(30000, 1001)
TAXA_2997DF = TimecodeRate(30000, 1001, drop_frame=True)
TAXA_30 = TimecodeRate(30, 1)
TODAS_AS_TAXAS = [TAXA_23976, TAXA_24, TAXA_25, TAXA_2997, TAXA_2997DF, TAXA_30]
AMPLITUDE_18 = round(10 ** (-18 / 20) * 8388607)


def _bits_bcd(valor, largura):
    """Bits de um digito BCD, do menos significativo para o mais significativo."""
    return [(valor >> i) & 1 for i in range(largura)]


def _frame_esperado(horas, minutos, segundos, frames, drop_frame, indice_polaridade):
    """Monta os 80 bits de um frame LTC conforme o layout SMPTE ST 12-1."""
    bits = [0] * 80
    bits[0:4] = _bits_bcd(frames % 10, 4)
    bits[8:10] = _bits_bcd(frames // 10, 2)
    bits[10] = 1 if drop_frame else 0
    bits[16:20] = _bits_bcd(segundos % 10, 4)
    bits[24:27] = _bits_bcd(segundos // 10, 3)
    bits[32:36] = _bits_bcd(minutos % 10, 4)
    bits[40:43] = _bits_bcd(minutos // 10, 3)
    bits[48:52] = _bits_bcd(horas % 10, 4)
    bits[56:58] = _bits_bcd(horas // 10, 2)
    bits[64:80] = list(SYNC)
    zeros = sum(1 for bit in bits if bit == 0)
    bits[indice_polaridade] = 1 if zeros % 2 else 0
    return bits


def _zero_crossings(amostras):
    """Instantes, em indices de sample, em que o sinal cruza o zero."""
    cruzes = []
    for i in range(len(amostras) - 1):
        a = amostras[i]
        b = amostras[i + 1]
        if a < 0 <= b or a > 0 >= b:
            cruzes.append(i + 1 if b == 0 else i - a / (b - a))
    return cruzes


def _tipos_de_intervalo(cruzes, celula):
    """Classifica cada intervalo: F e celula cheia, H e meia celula."""
    tipos = []
    for i in range(len(cruzes) - 1):
        duracao = cruzes[i + 1] - cruzes[i]
        tipos.append("H" if duracao < 0.75 * celula else "F")
    return "".join(tipos)


def _bits_antes_do_sync(tipos, inicio_sync):
    """Le de tras para frente os 64 bits de dados que precedem a palavra de sync."""
    bits = []
    j = inicio_sync - 1
    while len(bits) < 64:
        if j < 0:
            return None, -1
        if tipos[j] == "F":
            bits.append(0)
            j -= 1
        else:
            if j == 0 or tipos[j - 1] != "H":
                return None, -1
            bits.append(1)
            j -= 2
    bits.reverse()
    return bits, j + 1


def _bcd(bits, posicoes):
    """Le um digito BCD, da posicao menos significativa para a mais significativa."""
    return sum(bits[posicao] << i for i, posicao in enumerate(posicoes))


def _rotulo_do_frame(bits):
    """Le os campos BCD de um frame e monta o rotulo SMPTE correspondente."""
    horas = 10 * _bcd(bits, (56, 57)) + _bcd(bits, (48, 49, 50, 51))
    minutos = 10 * _bcd(bits, (40, 41, 42)) + _bcd(bits, (32, 33, 34, 35))
    segundos = 10 * _bcd(bits, (24, 25, 26)) + _bcd(bits, (16, 17, 18, 19))
    frames = 10 * _bcd(bits, (8, 9)) + _bcd(bits, (0, 1, 2, 3))
    separador = ";" if bits[10] else ":"
    return f"{horas:02d}:{minutos:02d}:{segundos:02d}{separador}{frames:02d}"


def _decodificar(amostras, rate, sample_rate):
    """Decodifica os frames LTC de um buffer. Devolve [(inicio, rotulo)]."""
    celula = float(samples_per_frame(rate, sample_rate) / 80)
    cruzes = _zero_crossings(amostras)
    tipos = _tipos_de_intervalo(cruzes, celula)
    frames = []
    posicao = 0
    while True:
        achado = tipos.find(PADRAO_SYNC, posicao)
        if achado < 0:
            break
        bits, inicio = _bits_antes_do_sync(tipos, achado)
        if bits is not None:
            frames.append((cruzes[inicio], _rotulo_do_frame(bits)))
        posicao = achado + 1
    return frames


def _amostras_pcm24(dados):
    """Converte bytes de PCM 24 bits little endian em amostras inteiras."""
    amostras = []
    for i in range(0, len(dados) - 2, 3):
        valor = dados[i] | (dados[i + 1] << 8) | (dados[i + 2] << 16)
        if valor >= 0x800000:
            valor -= 0x1000000
        amostras.append(valor)
    return amostras


def _ler_wav(caminho):
    """Le um WAV PCM 24 bits mono e devolve (taxa, amostras)."""
    with wave.open(str(caminho), "rb") as arquivo:
        assert arquivo.getnchannels() == 1
        assert arquivo.getsampwidth() == 3
        taxa = arquivo.getframerate()
        dados = arquivo.readframes(arquivo.getnframes())
    return taxa, _amostras_pcm24(dados)


def _primeira_subida(amostras):
    """Indice do primeiro par de amostras que sobe do negativo para o positivo."""
    for i in range(len(amostras) - 1):
        if amostras[i] < 0 <= amostras[i + 1]:
            return i
    raise AssertionError("nenhuma subida no buffer")


def _tempo_de_subida(amostras, i, amplitude, sample_rate):
    """Tempo, em microssegundos, entre os cruzamentos de 10% e 90% de uma subida."""
    baixo = -0.8 * amplitude
    alto = 0.8 * amplitude
    j = i
    while amostras[j] > baixo:
        j -= 1
    inicio = j + (baixo - amostras[j]) / (amostras[j + 1] - amostras[j])
    k = i
    while amostras[k] < alto:
        k += 1
    fim = k - 1 + (alto - amostras[k - 1]) / (amostras[k] - amostras[k - 1])
    return (fim - inicio) / sample_rate * 1e6


def _codigo_cli(argumentos):
    """Codigo de saida do CLI, aceitando o SystemExit que o argparse pode levantar."""
    try:
        return main(argumentos)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1


@pytest.mark.parametrize(
    "texto,esperado",
    [
        ("23.976", TAXA_23976),
        ("24", TAXA_24),
        ("25", TAXA_25),
        ("29.97", TAXA_2997),
        ("29.97df", TAXA_2997DF),
        ("30", TimecodeRate(30, 1)),
    ],
)
def test_parse_ltc_rate_reconhece_as_taxas_do_spec(texto, esperado):
    assert parse_ltc_rate(texto) == esperado


def test_parse_ltc_rate_recusa_taxa_fora_da_lista():
    with pytest.raises(ValueError):
        parse_ltc_rate("59.94")


def test_frame_bits_tem_a_palavra_de_sync_do_spec():
    bits = frame_bits(0, TAXA_25)
    assert len(bits) == 80
    assert tuple(bits[64:80]) == SYNC


def test_frame_bits_campos_bcd_de_01_23_45_12_em_25fps():
    bits = frame_bits(label_to_frames("01:23:45:12", TAXA_25), TAXA_25)
    assert bits == _frame_esperado(1, 23, 45, 12, False, 59)
    assert bits[0:4] == [0, 1, 0, 0]
    assert bits[8:10] == [1, 0]
    assert bits[16:20] == [1, 0, 1, 0]
    assert bits[24:27] == [0, 0, 1]
    assert bits[32:36] == [1, 1, 0, 0]
    assert bits[40:43] == [0, 1, 0]
    assert bits[48:52] == [1, 0, 0, 0]
    assert bits[56:58] == [0, 0]
    assert bits[10] == 0
    assert bits[11] == 0
    assert bits[43] == 0
    assert bits[58] == 0
    for inicio in (4, 12, 20, 28, 36, 44, 52, 60):
        assert bits[inicio : inicio + 4] == [0, 0, 0, 0]


@pytest.mark.parametrize("rate", TODAS_AS_TAXAS)
def test_frame_bits_tem_numero_par_de_zeros_em_200_frames(rate):
    for indice in range(200):
        bits = frame_bits(indice, rate)
        assert sum(1 for bit in bits if bit == 0) % 2 == 0


@pytest.mark.parametrize("rate", TODAS_AS_TAXAS)
def test_bit_de_drop_frame_so_liga_em_2997df(rate):
    for indice in range(20):
        bits = frame_bits(indice, rate)
        assert bits[10] == (1 if rate.drop_frame else 0)


@pytest.mark.parametrize(
    "rate,indice_polaridade",
    [
        (TAXA_25, 59),
        (TAXA_23976, 27),
        (TAXA_24, 27),
        (TAXA_2997, 27),
        (TAXA_2997DF, 27),
    ],
)
def test_bit_de_polaridade_fica_no_lugar_do_spec(rate, indice_polaridade):
    outro = 27 if indice_polaridade == 59 else 59
    for indice in range(200):
        bits = frame_bits(indice, rate)
        campos = parse_label(frames_to_label(indice, rate))
        assert bits == _frame_esperado(*campos, rate.drop_frame, indice_polaridade)
        assert bits[outro] == 0


@pytest.mark.parametrize("rate", TODAS_AS_TAXAS)
def test_volta_completa_do_ltc_em_48k(rate):
    primeiro = label_to_frames("00:59:58:00", rate)
    total = 2 * rate.nominal_fps
    offset, amostras = render_ltc(primeiro, total, rate, SR)
    fim = math.ceil(frames_to_samples_exact(primeiro + total, rate, SR))
    assert len(amostras) == fim - offset
    decodificados = _decodificar(amostras, rate, SR)
    assert len(decodificados) == total
    for i, (inicio, rotulo) in enumerate(decodificados):
        indice = primeiro + i
        assert rotulo == frames_to_label(indice, rate)
        esperado = float(frames_to_samples_exact(indice, rate, SR) - offset)
        assert abs(inicio - esperado) <= 0.5


def test_drop_frame_pula_os_numeros_no_meio_do_minuto():
    primeiro = label_to_frames("00:00:59;27", TAXA_2997DF)
    _, amostras = render_ltc(primeiro, 6, TAXA_2997DF, SR)
    rotulos = [rotulo for _, rotulo in _decodificar(amostras, TAXA_2997DF, SR)]
    assert rotulos == [
        "00:00:59;27",
        "00:00:59;28",
        "00:00:59;29",
        "00:01:00;02",
        "00:01:00;03",
        "00:01:00;04",
    ]


def test_drop_frame_mantem_00_e_01_no_minuto_dez():
    primeiro = label_to_frames("00:09:59;28", TAXA_2997DF)
    _, amostras = render_ltc(primeiro, 4, TAXA_2997DF, SR)
    rotulos = [rotulo for _, rotulo in _decodificar(amostras, TAXA_2997DF, SR)]
    assert rotulos == ["00:09:59;28", "00:09:59;29", "00:10:00;00", "00:10:00;01"]


@pytest.mark.parametrize(
    "rate,rotulo",
    [
        (TAXA_23976, "01:00:00:00"),
        (TAXA_24, "01:00:00:00"),
        (TAXA_25, "01:00:00:00"),
        (TAXA_2997, "01:00:00:00"),
        (TAXA_2997DF, "01:00:00:00"),
        (TAXA_2997DF, "10:00:00;00"),
        (TAXA_30, "01:00:00:00"),
    ],
)
def test_sem_drift_apos_uma_hora(rate, rotulo):
    primeiro = label_to_frames(rotulo, rate)
    offset, amostras = render_ltc(primeiro, 2, rate, SR)
    esperado = float(frames_to_samples_exact(primeiro, rate, SR) - offset)
    cruzes = _zero_crossings(amostras)
    assert cruzes
    assert abs(cruzes[0] - esperado) <= 0.5
    decodificados = _decodificar(amostras, rate, SR)
    assert decodificados[0][1] == frames_to_label(primeiro, rate)


def test_nivel_do_sinal_fica_em_menos_18_dbfs():
    _, amostras = render_ltc(0, 25, TAXA_25, SR)
    assert abs(max(abs(valor) for valor in amostras) - AMPLITUDE_18) <= 1


def test_nivel_acompanha_o_dbfs_pedido():
    _, amostras = render_ltc(0, 25, TAXA_25, SR, level_dbfs=-12.0)
    esperado = round(10 ** (-12 / 20) * 8388607)
    assert abs(max(abs(valor) for valor in amostras) - esperado) <= 1


def test_tempo_de_subida_de_25_us_em_192k():
    _, amostras = render_ltc(0, 2, TAXA_25, SR_ALTO)
    i = _primeira_subida(amostras)
    micros = _tempo_de_subida(amostras, i, AMPLITUDE_18, SR_ALTO)
    assert 20.0 <= micros <= 30.0


def test_write_ltc_wav_grava_bwf_com_time_reference(tmp_path):
    saida = write_ltc_wav(tmp_path / "ltc_25.wav", "01:00:00:00", TAXA_25, 25, SR)
    assert saida.exists()
    taxa, amostras = _ler_wav(saida)
    assert taxa == SR
    bext = parse_bext(saida)
    esperado = math.floor(label_to_samples("01:00:00:00", TAXA_25, SR)) - 2
    assert bext["time_reference"] == esperado
    decodificados = _decodificar(amostras, TAXA_25, taxa)
    assert decodificados[0][1] == "01:00:00:00"


def test_write_ltc_wav_em_drop_frame_leva_o_rotulo_do_inicio(tmp_path):
    saida = write_ltc_wav(tmp_path / "ltc_df.wav", "01:00:00;00", TAXA_2997DF, 30, SR)
    taxa, amostras = _ler_wav(saida)
    bext = parse_bext(saida)
    esperado = math.floor(label_to_samples("01:00:00;00", TAXA_2997DF, SR)) - 2
    assert bext["time_reference"] == esperado
    decodificados = _decodificar(amostras, TAXA_2997DF, taxa)
    assert decodificados[0][1] == "01:00:00;00"


def test_cli_ltc_gera_wav_com_a_duracao_pedida(tmp_path):
    saida = tmp_path / "ltc_cli.wav"
    codigo = _codigo_cli(
        ["ltc", "--start", "01:00:00:00", "--rate", "25", "--duration", "00:00:01:00", str(saida)]
    )
    assert codigo == 0
    assert saida.exists()
    taxa, amostras = _ler_wav(saida)
    rotulos = [rotulo for _, rotulo in _decodificar(amostras, TAXA_25, taxa)]
    assert len(rotulos) == 25
    assert rotulos[0] == "01:00:00:00"
    assert rotulos[-1] == "01:00:00:24"


def test_cli_ltc_com_fim_exclusivo_gera_o_mesmo_wav(tmp_path):
    saida = tmp_path / "ltc_cli_fim.wav"
    codigo = _codigo_cli(
        ["ltc", "--start", "01:00:00:00", "--rate", "25", "--end", "01:00:01:00", str(saida)]
    )
    assert codigo == 0
    taxa, amostras = _ler_wav(saida)
    rotulos = [rotulo for _, rotulo in _decodificar(amostras, TAXA_25, taxa)]
    assert len(rotulos) == 25
    assert rotulos[0] == "01:00:00:00"


def test_cli_ltc_recusa_taxa_desconhecida(tmp_path):
    saida = tmp_path / "ltc_cli_ruim.wav"
    codigo = _codigo_cli(
        ["ltc", "--start", "01:00:00:00", "--rate", "30df", "--duration", "00:00:01:00", str(saida)]
    )
    assert codigo != 0
