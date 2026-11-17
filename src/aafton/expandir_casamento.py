"""Expansao de canais, parte 1: indice do gravador e casamento clip -> original.

A pasta do gravador e indexada uma vez, so pelos metadados (``read_bwf`` com
fallback do bext). A midia de um clip casa com um original por
SCENE+TAKE+TimeReference; sem isso, pelo nome normalizado ou ORIGINAL_FILENAME.
O casamento so vale com o mesmo numero de frames. A entrada que o clip carrega
sai do TRACK_LIST de uma entrada da midia; numa copia de N canais com iXML, do
canal da fonte cruzado com o INTERLEAVE_INDEX; senao, da comparacao de amostras
com cada canal do original. O que nao casa fica como esta, com uma nota por
causa, agregada.
"""

from __future__ import annotations

import struct
import sys
from array import array
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .bwf_meta import BwfInfo, com_ixml_do_bext, read_bwf
from .expandir_nomes import nomes_do_clip, nomes_do_original
from .ir import ClipIR, MediaAsset
from .media_extract import read_audio_metadata
from .report import Report
from .som_direto import Arquivo, Canal, _canal, _wavs
from .som_direto_verify import FRAMES_POR_LEITURA, _canal_dos_dados

# Causas das notas agregadas, em ingles para o relatorio.
NOTA_ARQUIVO_ILEGIVEL = "unreadable WAV file of the production audio folder left out"
NOTA_SEM_MIDIA = "clip media not found on disk; the clip stays as it is"
NOTA_MIDIA_ILEGIVEL = "clip media is not a readable WAV; the clip stays as it is"
NOTA_SEM_ORIGINAL = "no original in the production audio folder; the clip stays as it is"
NOTA_FRAMES = "the original differs in length from the clip media; the clip stays as it is"
NOTA_MAIS_DE_UM = "more than one original matches the clip media; the clip stays as it is"
NOTA_ENTRADA = "the input of the clip media is ambiguous; the clip stays as it is"
# Causas que so dizem que a midia nao e do gravador: informacao, nao aviso.
NOTA_DUPLICADO = (
    "the editor used the same take on more than one track; the duplicate part was added once"
)
NOTAS_INFORMATIVAS = frozenset({NOTA_SEM_ORIGINAL, NOTA_SEM_MIDIA, NOTA_DUPLICADO})


class Notas:
    """Notas agregadas por causa, com a contagem."""

    def __init__(self) -> None:
        self.contagem: Counter[str] = Counter()

    def add(self, causa: str) -> None:
        self.contagem[causa] += 1

    def flush(self, report: Report) -> None:
        for causa, total in self.contagem.items():
            nivel = report.info if causa in NOTAS_INFORMATIVAS else report.warning
            nivel("EXPANSION_NOTE", f"{causa} ({total}x)", {"count": total})


@dataclass
class Original:
    """Um WAV do gravador e os canais dele, na ordem do arquivo."""

    arquivo: Arquivo
    canais: list[Canal]

    @property
    def path(self) -> Path:
        return self.arquivo.path

    @property
    def frames(self) -> int | None:
        return self.arquivo.info.frames


@dataclass
class Indice:
    """Originais da pasta do gravador por chave do iXML e por nome."""

    folder: Path
    por_chave: dict[tuple, list[Original]] = field(default_factory=dict)
    por_nome: dict[str, list[Original]] = field(default_factory=dict)
    total: int = 0  # originais legiveis da pasta


@dataclass
class Casamento:
    """O original de um clip e o canal dele (a partir de 1) que o clip carrega."""

    original: Original
    posicao: int

    @property
    def canal(self) -> Canal:
        return self.original.canais[self.posicao - 1]

    @property
    def key(self) -> tuple[str, int]:
        return self.canal.key


def chave_do_ixml(info: BwfInfo) -> tuple | None:
    """SCENE, TAKE e TimeReference do arquivo; None quando falta um deles."""
    ixml = info.ixml
    tc = info.bext.get("time_reference") if info.bext else None
    if ixml is None or not ixml.scene or not ixml.take or tc is None:
        return None
    return (ixml.scene, ixml.take, tc)


def original_do_arquivo(path: Path, info: BwfInfo) -> Original:
    """O WAV do gravador com os canais dele, como o indice o guarda."""
    arq = Arquivo(path, info, path.parent)
    return Original(arq, [_canal(arq, p) for p in range(1, info.channels + 1)])


def mesmo_take(clip: ClipIR, info: BwfInfo, original: Original) -> bool:
    """A midia do clip e uma copia do original: mesma chave do iXML (ou, sem
    iXML, o mesmo nome normalizado) e o mesmo numero de frames."""
    if not info.frames or info.frames != original.frames:
        return False
    chave = chave_do_ixml(info)
    if chave is not None:
        return chave == chave_do_ixml(original.arquivo.info)
    nomes_orig = set(nomes_do_original(original.path, original.arquivo.info))
    return any(nome in nomes_orig for nome in nomes_do_clip(clip, info))


def indexar(folder, notas: Notas) -> Indice:
    """Le os metadados de cada WAV da pasta uma vez e monta o indice."""
    indice = Indice(folder=Path(folder))
    for path in _wavs(Path(folder)):
        try:
            info = com_ixml_do_bext(read_bwf(path))
        except (ValueError, OSError):
            notas.add(NOTA_ARQUIVO_ILEGIVEL)
            continue
        if not info.frames or info.channels < 1:
            notas.add(NOTA_ARQUIVO_ILEGIVEL)
            continue
        original = original_do_arquivo(path, info)
        indice.total += 1
        chave = chave_do_ixml(info)
        if chave is not None:
            indice.por_chave.setdefault(chave, []).append(original)
        for nome in nomes_do_original(path, info):
            indice.por_nome.setdefault(nome, []).append(original)
    return indice


def _info(cache: dict, caminho: str) -> BwfInfo | None:
    """Metadados da midia do clip, lidos uma vez por arquivo."""
    if caminho not in cache:
        try:
            cache[caminho] = com_ixml_do_bext(read_bwf(caminho))
        except (ValueError, OSError):
            cache[caminho] = None
    return cache[caminho]


def _candidatos(clip: ClipIR, info: BwfInfo, indice: Indice) -> list[Original]:
    chave = chave_do_ixml(info)
    if chave is not None:
        return indice.por_chave.get(chave, [])
    achados: list[Original] = []
    vistos: set[int] = set()
    for nome in nomes_do_clip(clip, info):
        for original in indice.por_nome.get(nome, ()):
            if id(original) not in vistos:
                vistos.add(id(original))
                achados.append(original)
    return achados


def casar(
    clip: ClipIR, asset: MediaAsset | None, indice: Indice, cache: dict, notas: Notas
) -> Casamento | None:
    """Casamento do clip com um original da pasta; None com nota quando nao casa."""
    if asset is None or asset.status != "resolved" or not asset.resolved_path:
        notas.add(NOTA_SEM_MIDIA)
        return None
    info = _info(cache, str(asset.resolved_path))
    if info is None or not info.frames:
        notas.add(NOTA_MIDIA_ILEGIVEL)
        return None
    candidatos = _candidatos(clip, info, indice)
    iguais = [o for o in candidatos if o.frames == info.frames]
    causa = _causa(candidatos, iguais)
    if causa is not None:
        notas.add(causa)
        return None
    posicao = entrada_do_clip(clip, info, iguais[0])
    if posicao is None:
        notas.add(NOTA_ENTRADA)
        return None
    return Casamento(iguais[0], posicao)


def _causa(candidatos: list[Original], iguais: list[Original]) -> str | None:
    """Causa da nota quando os candidatos nao dao um original so; None quando dao."""
    if not candidatos:
        return NOTA_SEM_ORIGINAL
    if not iguais:
        return NOTA_FRAMES
    if len(iguais) > 1:
        return NOTA_MAIS_DE_UM
    return None


# --- Entrada que o clip carrega ---


def _posicao_da_entrada(original: Original, channel_index: int | None) -> int | None:
    if channel_index is None:
        return None
    return next((c.posicao for c in original.canais if c.key == ("In", channel_index)), None)


def _canal_da_copia(clip: ClipIR, info: BwfInfo) -> int:
    """Canal da midia que o clip le, a partir de 1."""
    if clip.source_channel and 1 <= clip.source_channel <= info.channels:
        return clip.source_channel
    if clip.channel_index < info.channels:
        return clip.channel_index + 1
    return 1


def _pelo_ixml(clip: ClipIR, info: BwfInfo, original: Original) -> int | None:
    """TRACK_LIST de uma entrada; numa copia de N canais, o INTERLEAVE_INDEX do canal."""
    lista = info.ixml.track_list if info.ixml is not None else None
    if not lista:
        return None
    if info.channels == 1 and len(lista) == 1:
        return _posicao_da_entrada(original, lista[0].channel_index)
    if info.channels > 1:
        canal = _canal_da_copia(clip, info)
        entrada = next((t for t in lista if t.interleave_index == canal), None)
        return _posicao_da_entrada(original, entrada.channel_index if entrada else None)
    return None


def entrada_do_clip(clip: ClipIR, info: BwfInfo, original: Original) -> int | None:
    """Canal do original (a partir de 1) que a midia do clip carrega; None se ambiguo."""
    posicao = _pelo_ixml(clip, info, original)
    if posicao is not None:
        return posicao
    return pela_comparacao(clip, info, original.path)


# --- Comparacao de amostras ---


@dataclass
class _Formato:
    """O que a comparacao precisa saber das amostras de um WAV."""

    path: Path
    channels: int
    largura: int
    flutuante: bool
    data_offset: int
    frames: int

    @property
    def quadro(self) -> int:
        return self.largura * self.channels

    @property
    def bits_inteiros(self) -> int | None:
        return None if self.flutuante else self.largura * 8


def _formato(path: Path) -> _Formato | None:
    """Formato das amostras pelo cabecalho; None quando a comparacao nao se aplica."""
    try:
        meta = read_audio_metadata(path)
    except (ValueError, OSError):
        return None
    payload = meta.get("fmt_payload") or b""
    if meta["format"] != "wav" or len(payload) < 2 or meta["data_offset"] is None:
        return None
    amostras = _largura_e_tipo(payload, meta["bits"])
    if amostras is None:
        return None
    largura, flutuante = amostras
    return _Formato(
        path, meta["channels"], largura, flutuante, meta["data_offset"], meta["duration_samples"]
    )


def _largura_e_tipo(payload: bytes, bits: int) -> tuple[int, bool] | None:
    """Largura em bytes e se a amostra e flutuante, pelo fmt; None quando nao se aplica."""
    tag = struct.unpack_from("<H", payload)[0]
    sub = struct.unpack_from("<H", payload, 24)[0] if tag == 0xFFFE and len(payload) >= 26 else 0
    flutuante = 3 in (tag, sub)
    largura = bits // 8
    if largura not in (1, 2, 3, 4) or (flutuante and largura != 4):
        return None
    return largura, flutuante


def _valores(bruto: bytes, fmt: _Formato, canal: int) -> list[float]:
    """Amostras de um canal de um bloco intercalado, em escala de -1 a 1."""
    mono = _canal_dos_dados(bruto, canal, fmt.largura, fmt.quadro)
    if fmt.largura == 1:
        return [(valor - 128) / 128 for valor in mono]
    if fmt.largura == 3:
        largo = bytearray(len(mono) // 3 * 4)
        for byte in range(3):
            largo[byte + 1 :: 4] = mono[byte::3]
        mono = bytes(largo)
    numeros = array("f" if fmt.flutuante else ("h" if fmt.largura == 2 else "i"), mono)
    if sys.byteorder == "big":
        numeros.byteswap()
    if fmt.flutuante:
        return list(numeros)
    escala = float(1 << (8 * numeros.itemsize - 1))
    return [valor / escala for valor in numeros]


def tolerancia(copia: _Formato, fonte: _Formato) -> float:
    """Diferenca aceita por amostra: o passo do formato inteiro mais grosso (2^-23 em 24 bits)."""
    bits = [b for b in (copia.bits_inteiros, fonte.bits_inteiros) if b]
    return 2.0 ** -(min(bits) - 1) if bits else 0.0


def _perto(copia: list[float], fonte: list[float], tol: float) -> bool:
    """Iguais dentro da tolerancia, ignorando as amostras do original com modulo >= 1."""
    return all(abs(a - b) <= tol for a, b in zip(copia, fonte) if abs(b) < 1.0)


def _restam(copia: _Formato, fonte: _Formato, canal: int, inicio: int, total: int) -> set[int]:
    """Canais do original que seguem iguais ao canal da copia no trecho."""
    tol = tolerancia(copia, fonte)
    restam = set(range(1, fonte.channels + 1))
    with copia.path.open("rb") as a, fonte.path.open("rb") as b:
        a.seek(copia.data_offset + inicio * copia.quadro)
        b.seek(fonte.data_offset + inicio * fonte.quadro)
        while total and restam:
            n = min(total, FRAMES_POR_LEITURA)
            bruto_a, bruto_b = a.read(n * copia.quadro), b.read(n * fonte.quadro)
            if len(bruto_a) != n * copia.quadro or len(bruto_b) != n * fonte.quadro:
                return set()
            valores = _valores(bruto_a, copia, canal)
            restam = {c for c in restam if _perto(valores, _valores(bruto_b, fonte, c), tol)}
            total -= n
    return restam


def canal_perto(clip: ClipIR, info: BwfInfo, original: Path, posicao: int) -> bool:
    """O canal da midia que o clip le e igual ao canal ``posicao`` do original no
    trecho da fonte do clip, na tolerancia do casamento."""
    copia, fonte = _formato(info.path), _formato(original)
    if copia is None or fonte is None or copia.frames != fonte.frames:
        return False
    inicio, total = clip.source_offset_samples or 0, clip.duration_samples
    if inicio < 0 or total <= 0 or inicio + total > copia.frames:
        return False
    return posicao in _restam(copia, fonte, _canal_da_copia(clip, info), inicio, total)


def pela_comparacao(clip: ClipIR, info: BwfInfo, original: Path) -> int | None:
    """Canal do original igual ao canal da copia no trecho do clip; None se nao for um so."""
    copia, fonte = _formato(info.path), _formato(original)
    if copia is None or fonte is None or copia.frames != fonte.frames:
        return None
    inicio = clip.source_offset_samples or 0
    total = min(clip.duration_samples, copia.frames - inicio)
    if total <= 0:
        return None
    restam = _restam(copia, fonte, _canal_da_copia(clip, info), inicio, total)
    return restam.pop() if len(restam) == 1 else None
