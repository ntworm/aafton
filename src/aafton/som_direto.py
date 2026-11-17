"""Leitura de som direto: WAVs do gravador viram um SessionIR com diarias em blocos.

A entrada e uma pasta (com subpastas de diaria, ou uma diaria so) ou um WAV.
A diaria e a pasta imediata do arquivo, ou a pasta de gravacao para pastas
``.TAKE`` sob a regra do gravador. Um take junta os arquivos do mesmo
``FAMILY_UID`` real ou do dialeto reconhecido pelo nome e metadado; fora disso
cada arquivo e um take. Cada canal vira uma entrada: com iXML real a chave e
o ``CHANNEL_INDEX`` (``In N``), sem ele os canais do nome do dialeto ou a
posicao no arquivo (``Ch N``); as duas nunca se juntam.

O bext e o iXML saem de ``bwf_meta.read_bwf`` com fallback do bext ``Description``.
Um arquivo RF64 ou ilegivel fica de fora com uma nota; a leitura nunca para por
causa de um arquivo. As notas sao curtas, por causa, com a contagem de arquivos.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .bwf_meta import BwfInfo, Ixml, IxmlTrack, com_ixml_do_bext, read_bwf
from .gravador_dialetos import Partes, reconhecer, resolver_letras
from .ir import PRODUCTION_LAYOUT, SessionIR
from .report import Report
from .som_direto_blocos import marcadores, montar_blocos, taxa_de_timecode
from .som_direto_dialeto import relatar_canais_divergentes, relatar_dialetos, resolver_pastas_take
from .som_direto_pedacos import resolver_pedacos
from .som_direto_tracks import montar_tracks

# Nota do bwf_meta que so diz que um campo opcional faltou: fica fora do relatorio.
NOTA_OPCIONAL = "iXML without "
NOTA_RF64 = "RF64 not supported yet; file left out"
NOTA_ILEGIVEL = "unreadable WAV file left out"
NOTA_SEM_TAMANHO = "WAV file without a known length left out"
NOTA_BEXT_FALLBACK = "iXML read from the bext Description"
NOTA_DIALECT_UNCONFIRMED = "unconfirmed Sound Devices bext keys"
NOTAS_ESPECIAIS = {
    NOTA_BEXT_FALLBACK: ("info", "PRODUCTION_BEXT_FALLBACK"),
    NOTA_DIALECT_UNCONFIRMED: ("warning", "PRODUCTION_DIALECT_UNCONFIRMED"),
}


@dataclass
class Arquivo:
    """Um WAV legivel do gravador e a pasta da diaria dele."""

    path: Path
    info: BwfInfo
    day: Path
    partes: Partes | None = field(init=False, default=None)
    offset: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        self.partes = reconhecer(self.path, self.info)

    @property
    def ixml(self) -> Ixml | None:
        return self.info.ixml

    @property
    def timecode(self) -> int | None:
        """TimeReference do bext, em samples desde a meia-noite; None sem bext."""
        bext = self.info.bext
        return bext.get("time_reference") if bext else None


@dataclass
class Canal:
    """Um canal de um arquivo: chave da entrada, posicao no arquivo e microfone."""

    key: tuple[str, int]
    arquivo: Arquivo
    posicao: int
    mic: str | None


@dataclass
class Take:
    """Um take: os arquivos dele, os canais e a posicao no set."""

    day: Path
    arquivos: list[Arquivo]
    canais: list[Canal] = field(default_factory=list)
    start: int = 0
    # Dias somados ao timecode na diaria que passa da meia-noite.
    dias: int = 0

    @property
    def ixml(self) -> Ixml | None:
        return next((a.ixml for a in self.arquivos if a.ixml is not None), None)

    @property
    def timecode(self) -> int | None:
        return self.arquivos[0].timecode

    @property
    def name_key(self) -> str:
        return self.arquivos[0].path.name.casefold()

    @property
    def frames(self) -> int:
        return max((a.offset + (a.info.frames or 0) for a in self.arquivos), default=0)

    @property
    def circled(self) -> bool | None:
        ixml = self.ixml
        if ixml is not None and ixml.circled is not None:
            return ixml.circled
        if any(a.partes is not None and a.partes.circulado for a in self.arquivos):
            return True
        return None


SEM_METADADOS = frozenset({"no bext chunk", "no iXML chunk"})


class Notas:
    """Notas agregadas por causa, com a contagem de arquivos."""

    def __init__(self) -> None:
        self.contagem: Counter[str] = Counter()

    def add(self, causa: str) -> None:
        self.contagem[causa] += 1

    def flush(self, report: Report) -> None:
        for causa, total in self.contagem.items():
            plural = "file" if total == 1 else "files"
            mensagem = f"{causa} ({total} {plural})"
            if causa in NOTAS_ESPECIAIS:
                severidade, codigo = NOTAS_ESPECIAIS[causa]
                report.add(severidade, codigo, mensagem, {"count": total})
                continue
            # Sem bext ou iXML e so um gravador sem metadados: informacao, nao aviso.
            nivel = report.info if causa in SEM_METADADOS else report.warning
            nivel("PRODUCTION_FILE_NOTE", mensagem, {"count": total})


def is_production_input(path) -> bool:
    """Entrada de som direto: uma pasta ou um arquivo .wav."""
    caminho = Path(path)
    return caminho.is_dir() or caminho.suffix.lower() == ".wav"


def _wavs(raiz: Path) -> list[Path]:
    """WAVs da entrada: o proprio arquivo ou os da pasta, em todas as subpastas."""
    if raiz.is_file():
        return [raiz]
    if not raiz.is_dir():
        raise FileNotFoundError(2, "No such file or directory", str(raiz))
    return sorted(p for p in raiz.rglob("*") if p.is_file() and p.suffix.lower() == ".wav")


def _ler_um(path: Path, notas: Notas) -> Arquivo | None:
    """Le os metadados de um WAV; None com nota quando ele fica de fora."""
    try:
        info = com_ixml_do_bext(read_bwf(path))
    except ValueError as exc:
        notas.add(NOTA_RF64 if "RF64" in str(exc) else NOTA_ILEGIVEL)
        return None
    except OSError:
        notas.add(NOTA_ILEGIVEL)
        return None
    if not info.frames or info.channels < 1:
        notas.add(NOTA_SEM_TAMANHO)
        return None
    for nota in info.notes:
        if not nota.startswith(NOTA_OPCIONAL):
            notas.add(nota.split(":")[0])
    return Arquivo(path, info, path.parent)


def _taxa_unica(arquivos: list[Arquivo]) -> int:
    """Taxa de amostragem comum a todos os arquivos; taxas misturadas sao recusadas."""
    taxas = sorted({a.info.sample_rate for a in arquivos})
    if len(taxas) != 1:
        lista = ", ".join(str(t) for t in taxas)
        raise ValueError(
            f"production audio with mixed sample rates ({lista} Hz) is not supported; "
            "import one rate at a time"
        )
    return taxas[0]


def _ixml_real(ixml: Ixml | None) -> Ixml | None:
    """Devolve o iXML quando veio de um chunk iXML real, nao do fallback do bext."""
    return ixml if ixml is not None and ixml.source == "ixml" else None


def _chave_base(arq: Arquivo) -> tuple:
    """Chave base do take: FAMILY_UID real, dialeto ou o proprio arquivo."""
    ixml = _ixml_real(arq.ixml)
    if ixml is not None and ixml.family_uid:
        return (str(arq.day), "family", ixml.family_uid)
    partes = arq.partes
    if partes is None:
        return (str(arq.day), "file", arq.path.name)
    if partes.dialeto in ("tascam_dr40", "tascam_x8"):
        return (str(arq.day), partes.dialeto, partes.identidade, arq.info.frames)
    return (str(arq.day), partes.dialeto, partes.identidade)


def _chave_take(arq: Arquivo) -> tuple:
    """Chave do take do arquivo, com (alternativa, pedaco) no fim quando ha dialeto."""
    base = _chave_base(arq)
    if arq.partes is None:
        return base
    return (*base, arq.partes.alternativa, arq.partes.pedaco)


def _agrupar(arquivos: list[Arquivo]) -> list[Take]:
    """Junta os arquivos em takes, na ordem da pasta e do nome."""
    grupos: dict[tuple, list[Arquivo]] = {}
    for arq in sorted(arquivos, key=lambda a: (str(a.day), a.path.name.casefold())):
        grupos.setdefault(_chave_take(arq), []).append(arq)
    return [Take(day=itens[0].day, arquivos=itens) for itens in grupos.values()]


def _entrada(ixml: Ixml | None, posicao: int) -> IxmlTrack | None:
    """Entrada do TRACK_LIST na posicao do canal no arquivo."""
    if ixml is None or not ixml.track_list:
        return None
    return next((t for t in ixml.track_list if t.interleave_index == posicao), None)


def _canal(arq: Arquivo, posicao: int) -> Canal:
    """Chave e microfone de um canal: In N pelo iXML real, Ch N pelo nome ou posicao."""
    entrada = _entrada(_ixml_real(arq.ixml), posicao)
    if entrada is not None and entrada.channel_index is not None:
        return Canal(("In", entrada.channel_index), arq, posicao, entrada.name or None)
    partes = arq.partes
    canais = partes.canais_do_nome if partes is not None else None
    if canais is not None and len(canais) == arq.info.channels:
        numero = canais[posicao - 1]
    else:
        numero = posicao
    return Canal(("Ch", numero), arq, posicao, None)


def _canais(take: Take) -> list[Canal]:
    return [
        _canal(arq, posicao) for arq in take.arquivos for posicao in range(1, arq.info.channels + 1)
    ]


def _filtrar_circled(takes: list[Take], circled_only: bool, report: Report) -> list[Take]:
    """Com o filtro, so ficam os takes com CIRCLED verdadeiro; o relatorio conta."""
    circled = sum(1 for take in takes if take.circled)
    report.info(
        "PRODUCTION_TAKES",
        f"{len(takes)} takes in the folder, {circled} circled",
        {"takes": len(takes), "circled": circled},
    )
    if not circled_only:
        return takes
    fora = len(takes) - circled
    report.info(
        "PRODUCTION_CIRCLED_ONLY",
        f"{fora} takes left out because they are not circled",
        {"left_out": fora},
    )
    if not circled:
        raise ValueError("--circled-only left no take: no take of the folder is circled")
    return [take for take in takes if take.circled]


def ler_takes(path, report: Report, *, circled_only: bool = False) -> tuple[list[Take], int]:
    """Takes legiveis da entrada, com canais e filtro, e a taxa de amostragem."""
    raiz = Path(path)
    notas = Notas()
    arquivos = [a for a in (_ler_um(p, notas) for p in _wavs(raiz)) if a is not None]
    notas.flush(report)
    if not arquivos:
        raise ValueError(f"no readable WAV file in {path}")
    sample_rate = _taxa_unica(arquivos)
    resolver_letras(arquivos)
    resolver_pastas_take(arquivos, raiz, report)
    relatar_dialetos(arquivos, report)
    takes = resolver_pedacos(_agrupar(arquivos), report)
    for take in takes:
        take.canais = _canais(take)
    relatar_canais_divergentes(takes, report)
    return _filtrar_circled(takes, circled_only, report), sample_rate


def read_production(path, report: Report, *, circled_only: bool = False) -> SessionIR:
    """Le a pasta ou o WAV de som direto e monta o SessionIR em blocos."""
    takes, sample_rate = ler_takes(path, report, circled_only=circled_only)
    blocos = montar_blocos(takes, sample_rate, report)
    ordenados = [take for bloco in blocos for take in bloco.takes]
    rate = taxa_de_timecode(ordenados, report)
    tracks, media = montar_tracks(ordenados, report)
    caminho = Path(path)
    return SessionIR(
        name=caminho.stem if caminho.is_file() else caminho.name,
        sample_rate=sample_rate,
        timecode_rate=rate,
        start_timecode_samples=0,
        tracks=tracks,
        media=media,
        markers=marcadores(blocos, rate, sample_rate),
        source_path=str(caminho),
        source_application="production_audio",
        layout=PRODUCTION_LAYOUT,
        layout_blocks=[bloco.to_dict(rate, sample_rate, circled_only) for bloco in blocos],
    )
