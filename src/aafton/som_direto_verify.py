"""Verify da ida do som direto: cada mono do set contra o canal de origem.

A pasta do gravador e lida de novo pelas mesmas regras do import (com o filtro
de circled que o sidecar registra) e o set e lido pelo leitor do .als. Cada
clip do set e pareado com o clip esperado da mesma track, na ordem do set. A
posicao tem de ser igual a regra dos blocos, com 0 samples de desvio, e as
amostras do mono tem de ser iguais as daquele canal do arquivo original, lidas
em blocos, sem carregar o arquivo inteiro. Sem depender da regra dos blocos,
cada clip tem de estar do locator do bloco a distancia que o TimeReference do
bext do proprio mono da ao primeiro take do bloco, e os locators lidos do
.als (nome e posicao) tem de ser os blocos do sidecar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .als_read import read_session
from .bwf_meta import read_bwf
from .ir import PRODUCTION_LAYOUT, ClipIR, SessionIR
from .media_extract import read_audio_metadata
from .report import Report
from .sidecar import read_anchor
from .som_direto import read_production
from .som_direto_blocos import DIA_SEGUNDOS, SEPARADOR, data_do_bext
from .timecode import samples_to_label

FRAMES_POR_LEITURA = 65536  # frames lidos por vez de cada arquivo
MAX_CITADOS = 5  # clips citados na mensagem de erro
CAMPOS_DO_BLOCO = ("start_samples", "locator_samples", "timecode_samples", "day", "date")


@dataclass
class ProductionRow:
    """Um clip comparado: posicoes no set, timecode real e o estado."""

    track: str
    clip: str
    expected_in: str
    set_in: str
    timecode: str
    delta_samples: int | None
    delta_end_samples: int | None
    samples_equal: bool
    status: str


@dataclass
class ProductionVerifyResult:
    """Resultado do verify do som direto, com contagens como o verify do AAF."""

    rows: list[ProductionRow] = field(default_factory=list)
    expected: int = 0
    compared: int = 0
    missing: int = 0
    extra: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def max_delta_samples(self) -> int:
        deltas = [
            abs(valor)
            for row in self.rows
            for valor in (row.delta_samples, row.delta_end_samples)
            if valor is not None
        ]
        return max(deltas, default=0)

    @property
    def samples_equal(self) -> int:
        return sum(1 for row in self.rows if row.samples_equal)

    @property
    def ok(self) -> bool:
        contagens = self.expected > 0 and self.compared == self.expected
        sem_sobra = self.missing == 0 and self.extra == 0 and not self.errors
        return contagens and sem_sobra and all(row.status == "ok" for row in self.rows)

    def _resumo(self) -> list[str]:
        linhas = [
            "Regime: Production audio (sample-exact)",
            f"Result: {'passed' if self.ok else 'failed'}",
            f"{self.compared} of {self.expected} audio clips compared "
            f"({self.missing} missing, {self.extra} extra)",
            f"largest deviation {self.max_delta_samples} samples",
            f"samples equal to the source channel in {self.samples_equal} of {self.compared}",
        ]
        if self.errors:
            linhas.append(f"Errors: {'; '.join(self.errors)}")
        return linhas

    def to_table(self) -> str:
        linhas = self._resumo()
        linhas.append(
            f"{'TRACK':<20}{'CLIP':<28}{'SET IN':<14}{'TIMECODE':<14}{'D START':>8}  STATUS"
        )
        for row in self.rows:
            delta = "---" if row.delta_samples is None else str(row.delta_samples)
            linhas.append(
                f"{row.track[:19]:<20}{row.clip[:27]:<28}{row.set_in:<14}"
                f"{row.timecode:<14}{delta:>8}  {row.status}"
            )
        return "\n".join(linhas) + "\n"

    def to_markdown(self) -> str:
        linhas = ["# Verification production audio -> ALS", ""]
        linhas.extend(f"- {linha}" for linha in self._resumo())
        linhas.extend(["", "| Track | Clip | Set in | Timecode | Start deviation | Status |"])
        linhas.append("| --- | --- | --- | --- | ---: | --- |")
        for row in self.rows:
            delta = "---" if row.delta_samples is None else str(row.delta_samples)
            linhas.append(
                f"| {row.track} | {row.clip} | {row.set_in} | {row.timecode} | {delta} | "
                f"{row.status} |"
            )
        return "\n".join(linhas) + "\n"


def _canal_dos_dados(bruto: bytes, canal: int, largura: int, quadro: int) -> bytes:
    """Bytes de um canal de um bloco intercalado."""
    mono = bytearray(len(bruto) // quadro * largura)
    for byte in range(largura):
        mono[byte::largura] = bruto[(canal - 1) * largura + byte :: quadro]
    return bytes(mono)


def amostras_iguais(mono: Path, fonte: Path, canal: int) -> bool:
    """O mono tem as amostras do canal ``canal`` (a partir de 1) da fonte, byte a byte."""
    m, f = read_audio_metadata(mono), read_audio_metadata(fonte)
    mesmos = all(m[chave] == f[chave] for chave in ("format", "bits", "duration_samples"))
    if not mesmos or m["channels"] != 1 or not 1 <= canal <= f["channels"]:
        return False
    largura = f["bits"] // 8
    quadro = largura * f["channels"]
    with mono.open("rb") as a, fonte.open("rb") as b:
        a.seek(m["data_offset"])
        b.seek(f["data_offset"])
        restante = f["duration_samples"]
        while restante:
            n = min(restante, FRAMES_POR_LEITURA)
            bruto, lido = b.read(n * quadro), a.read(n * largura)
            if len(bruto) != n * quadro or _canal_dos_dados(bruto, canal, largura, quadro) != lido:
                return False
            restante -= n
    return True


def _ordenados(session: SessionIR) -> dict[str, list[ClipIR]]:
    return {
        track.name: sorted(track.clips, key=lambda c: (c.timeline_start_samples, c.name))
        for track in session.tracks
    }


def _comparar(esperado: ClipIR, real: ClipIR, destino: SessionIR) -> tuple[int, int, bool]:
    """Desvios de inicio e fim e a igualdade das amostras do clip do set."""
    delta = real.timeline_start_samples - esperado.timeline_start_samples
    delta_fim = real.timeline_end_samples - esperado.timeline_end_samples
    asset = destino.media_by_id(real.media_id) if real.media_id else None
    fonte = esperado.raw["production"]
    iguais = False
    if asset is not None and asset.resolved_path and not real.source_offset_samples:
        mono = Path(asset.resolved_path)
        iguais = mono.is_file() and amostras_iguais(mono, Path(fonte["source"]), fonte["channel"])
    return delta, delta_fim, iguais


def _status(delta: int, delta_fim: int, iguais: bool) -> str:
    if delta or delta_fim:
        return "position differs"
    return "ok" if iguais else "samples differ"


def _rotulo(samples: int | None, sessao: SessionIR) -> str:
    if samples is None:
        return "---"
    return samples_to_label(samples, sessao.timecode_rate, sessao.sample_rate)


def _inicio(clip: ClipIR | None) -> int | None:
    return clip.timeline_start_samples if clip is not None else None


def _linha(
    track: str,
    esperado: ClipIR | None,
    real: ClipIR | None,
    origem: SessionIR,
    destino: SessionIR,
) -> ProductionRow:
    """Linha de um par; sem par, o clip fica faltando ou sobrando."""
    clip = esperado if esperado is not None else real
    tc = esperado.raw.get("timecode_samples") if esperado is not None else None
    row = ProductionRow(
        track=track,
        clip=clip.name,
        expected_in=_rotulo(_inicio(esperado), origem),
        set_in=_rotulo(_inicio(real), origem),
        timecode=_rotulo(tc, origem),
        delta_samples=None,
        delta_end_samples=None,
        samples_equal=False,
        status="missing in the ALS" if real is None else "extra in the ALS",
    )
    if esperado is None or real is None:
        return row
    row.delta_samples, row.delta_end_samples, row.samples_equal = _comparar(esperado, real, destino)
    row.status = _status(row.delta_samples, row.delta_end_samples, row.samples_equal)
    return row


def _blocos_iguais(origem: SessionIR, blocos_do_set: list[dict] | None) -> bool:
    esperados = [tuple(b.get(k) for k in CAMPOS_DO_BLOCO) for b in origem.layout_blocks]
    gravados = [tuple(b.get(k) for k in CAMPOS_DO_BLOCO) for b in blocos_do_set or []]
    return esperados == gravados


def _parear(resultado: ProductionVerifyResult, origem: SessionIR, destino: SessionIR) -> None:
    """Pareia os clips por track, na ordem do set, e conta os faltando e sobrando."""
    esperados, reais = _ordenados(origem), _ordenados(destino)
    for nome in list(esperados) + [n for n in reais if n not in esperados]:
        lista_e, lista_r = esperados.get(nome, []), reais.get(nome, [])
        for indice in range(max(len(lista_e), len(lista_r))):
            esperado = lista_e[indice] if indice < len(lista_e) else None
            real = lista_r[indice] if indice < len(lista_r) else None
            resultado.rows.append(_linha(nome, esperado, real, origem, destino))
            resultado.compared += esperado is not None and real is not None
            resultado.missing += real is None
            resultado.extra += esperado is None


# --- Conferencias independentes da regra dos blocos ---


def _nome_do_locator(bloco: dict) -> str:
    """Nome do locator do bloco a partir do sidecar: data (ou pasta) e timecode."""
    rotulo = bloco.get("date") or bloco.get("day") or ""
    tc = bloco.get("timecode")
    return f"{rotulo} {SEPARADOR} TC {tc}" if tc else f"{rotulo} {SEPARADOR} no TC"


def _locators_iguais(destino: SessionIR, blocos: list[dict]) -> bool:
    """Os locators lidos do .als, nome e posicao, sao os dos blocos do sidecar."""
    esperados = [(_nome_do_locator(b), b.get("locator_samples")) for b in blocos]
    lidos = sorted(((m.name, m.position_samples) for m in destino.markers), key=lambda p: p[1])
    return lidos == esperados


def _indice_do_bloco(blocos: list[dict], inicio: int) -> int | None:
    """Bloco do set em que o clip comeca: o ultimo que comeca antes dele."""
    achado = None
    for indice, bloco in enumerate(blocos):
        if int(bloco.get("start_samples") or 0) <= inicio:
            achado = indice
    return achado


def _tempo_do_mono(clip: ClipIR, destino: SessionIR, cache: dict) -> tuple | None:
    """TimeReference e OriginationDate do bext do proprio mono; None sem TimeReference."""
    asset = destino.media_by_id(clip.media_id) if clip.media_id else None
    if asset is None or not asset.resolved_path:
        return None
    caminho = asset.resolved_path
    if caminho not in cache:
        try:
            cache[caminho] = read_bwf(caminho).bext
        except (ValueError, OSError):
            cache[caminho] = None
    bext = cache[caminho]
    tr = bext.get("time_reference") if bext else None
    return None if tr is None else (tr, data_do_bext(bext))


def _fora_no_bloco(bloco: dict, itens: list[tuple], sample_rate: int) -> list[str]:
    """Clips do bloco fora da distancia que o TimeReference do mono da ao locator.

    Com OriginationDate em todos os monos do bloco, o take depois da meia-noite
    conta 24 h por dia de diferenca para a menor data; sem as datas, a
    diferenca so pode ser um numero inteiro de dias, nunca negativo.
    """
    dia = DIA_SEGUNDOS * sample_rate
    datas = [data for *_, data in itens]
    base = min(datas) if all(datas) else None
    fora = []
    for nome, clip, tr, data in itens:
        no_set = clip.timeline_start_samples - int(bloco["locator_samples"])
        desvio = no_set - (tr - int(bloco["timecode_samples"]))
        if base is not None:
            certo = desvio == (data - base).days * dia
        else:
            certo = desvio >= 0 and desvio % dia == 0
        if not certo:
            fora.append(f"{nome} {clip.name}")
    return fora


def _fora_do_timecode(destino: SessionIR, blocos: list[dict]) -> list[str]:
    """Clips do set cuja distancia ao locator do bloco nao e a do bext do proprio mono."""
    cache: dict = {}
    por_bloco: dict[int, list[tuple]] = {}
    for track in destino.tracks:
        for clip in track.clips:
            indice = _indice_do_bloco(blocos, clip.timeline_start_samples)
            lido = _tempo_do_mono(clip, destino, cache)
            if indice is None or lido is None or blocos[indice].get("timecode_samples") is None:
                continue
            por_bloco.setdefault(indice, []).append((track.name, clip, *lido))
    fora: list[str] = []
    for indice, itens in sorted(por_bloco.items()):
        fora.extend(_fora_no_bloco(blocos[indice], itens, destino.sample_rate))
    return fora


def _conferir_independente(resultado, destino: SessionIR, blocos: list[dict]) -> None:
    """Posicao pelo bext de cada mono e locators lidos do .als contra o sidecar."""
    fora = _fora_do_timecode(destino, blocos)
    if fora:
        citados = ", ".join(fora[:MAX_CITADOS])
        resultado.errors.append(
            f"{len(fora)} clips are not at the distance of their TimeReference "
            f"from the block locator: {citados}"
        )
    if not _locators_iguais(destino, blocos):
        resultado.errors.append("the locators of the set differ from the blocks of the sidecar")


def verify_production(folder, als_path) -> ProductionVerifyResult:
    """Compara a pasta do gravador com o set gerado dela, clip a clip."""
    anchor = read_anchor(als_path)
    if anchor.layout != PRODUCTION_LAYOUT:
        raise ValueError(f"{Path(als_path).name} was not built from production audio")
    blocos = anchor.blocks or []
    circled_only = any(bloco.get("circled_only") for bloco in blocos)
    origem = read_production(folder, Report(strict=False), circled_only=circled_only)
    destino = read_session(als_path, Report(strict=False), allow_production=True)
    resultado = ProductionVerifyResult(expected=sum(len(t.clips) for t in origem.tracks))
    if not _blocos_iguais(origem, blocos):
        resultado.errors.append("the blocks of the sidecar differ from the blocks of the folder")
    _conferir_independente(resultado, destino, blocos)
    _parear(resultado, origem, destino)
    return resultado
