"""Diarias em blocos no som direto: data de cada diaria, ordem e posicao dos takes.

Data da diaria: OriginationDate do bext; senao o prefixo ``AAMMDD_`` do nome dos
arquivos, aceito so com ano 2001 ou depois e a menos de 366 dias da mediana das
datas firmes (do bext; sem nenhuma, dos prefixos com ano valido); senao o nome
da pasta ``DD_MM`` ou ``DD-MM-AA``, com o ano da mediana das datas firmes
(bext e prefixos aceitos). Diaria sem data vai para o fim, em ordem alfabetica.

O bloco 1 comeca em 0. O take com timecode fica em ``inicio + 10 s +
(TimeReference - menor TimeReference do bloco)``; os sem timecode entram na
ordem do nome, 2 s um do outro, depois do ultimo take com timecode. O bloco
seguinte comeca 10 s depois do fim do ultimo take, arredondado para cima ao
segundo. Na diaria que passa da meia-noite, o take com OriginationDate depois
da menor data da diaria (ou, sem datas, com timecode mais de 12 h abaixo do
maior anterior na ordem do nome) ganha 24 h por dia antes da posicao; o
locator mantem o timecode real do primeiro take.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from .ir import MarkerIR
from .report import Report
from .timecode import TimecodeRate, exact_edit_rate, samples_to_label

if TYPE_CHECKING:
    from .som_direto import Take

PRE_ROLL_SEGUNDOS = 10  # do inicio do bloco ate o primeiro take
INTERVALO_SEGUNDOS = 2  # entre takes sem timecode
LIMITE_DIAS = 366  # distancia maxima da mediana para aceitar a data do nome
ANO_MINIMO = 2001  # ano minimo da data do nome do arquivo
DIA_SEGUNDOS = 24 * 3600  # somado por dia ao timecode depois da meia-noite
MEIO_DIA_SEGUNDOS = 12 * 3600  # queda de timecode que indica a meia-noite
TAXA_PADRAO = TimecodeRate(24, 1)
SEPARADOR = "·"

_PREFIXO_RE = re.compile(r"^(\d{2})(\d{2})(\d{2})_")
_PASTA_RE = re.compile(r"(?<!\d)(\d{1,2})[_-](\d{1,2})(?:[_-](\d{4}|\d{2}))?(?!\d)")
_BEXT_RE = re.compile(r"^(\d{4})[-:/.](\d{2})[-:/.](\d{2})$")


@dataclass
class Bloco:
    """Uma diaria no set: data, takes na ordem do set e posicoes."""

    day: Path
    date: date | None
    takes: list[Take] = field(default_factory=list)
    start: int = 0
    locator: int = 0
    timecode: int | None = None

    @property
    def rotulo(self) -> str:
        """Data como YYYY-MM-DD, ou o nome da pasta quando nao ha data."""
        return self.date.isoformat() if self.date else self.day.name

    def nome_do_locator(self, rate: TimecodeRate, sample_rate: int) -> str:
        if self.timecode is None:
            return f"{self.rotulo} {SEPARADOR} no TC"
        rotulo_tc = samples_to_label(self.timecode, rate, sample_rate)
        return f"{self.rotulo} {SEPARADOR} TC {rotulo_tc}"

    def to_dict(self, rate: TimecodeRate, sample_rate: int, circled_only: bool) -> dict:
        tc = self.timecode
        return {
            "start_samples": self.start,
            "locator_samples": self.locator,
            "timecode_samples": tc,
            "timecode": samples_to_label(tc, rate, sample_rate) if tc is not None else None,
            "day": self.day.name,
            "date": self.date.isoformat() if self.date else None,
            "takes": len(self.takes),
            "circled_only": circled_only,
        }


def _data(ano: int, mes: int, dia: int) -> date | None:
    try:
        return date(ano, mes, dia)
    except ValueError:
        return None


def data_do_bext(bext: dict | None) -> date | None:
    """OriginationDate valida do bext; None quando falta ou nao e uma data."""
    texto = ((bext or {}).get("origination_date") or "").strip()
    achado = _BEXT_RE.match(texto)
    return _data(*map(int, achado.groups())) if achado else None


def _data_do_take(take: Take) -> date | None:
    return next((d for arq in take.arquivos if (d := data_do_bext(arq.info.bext))), None)


def _data_bext(takes: list[Take]) -> date | None:
    """Menor OriginationDate valida do bext nos arquivos da diaria: o dia em que ela comecou."""
    datas = [d for take in takes for arq in take.arquivos if (d := data_do_bext(arq.info.bext))]
    return min(datas, default=None)


def _data_prefixo(takes: list[Take]) -> tuple[date | None, bool]:
    """Data mais comum do prefixo AAMMDD_ e se algum arquivo tinha o prefixo."""
    contagem: Counter[date] = Counter()
    casou = False
    for take in takes:
        for arq in take.arquivos:
            achado = _PREFIXO_RE.match(arq.path.name)
            if achado is None:
                continue
            casou = True
            valor = _data(2000 + int(achado[1]), int(achado[2]), int(achado[3]))
            if valor is not None:
                contagem[valor] += 1
    return (contagem.most_common(1)[0][0] if contagem else None), casou


def _data_pasta(nome: str, ano: int | None) -> date | None:
    """Data do nome da pasta, DD_MM ou DD-MM-AA; o ano vem do nome ou de ``ano``."""
    achados = list(_PASTA_RE.finditer(nome))
    if not achados:
        return None
    dia, mes, ano_texto = achados[-1].groups()
    if ano_texto:
        ano = int(ano_texto) + (2000 if len(ano_texto) == 2 else 0)
    return _data(ano, int(mes), int(dia)) if ano is not None else None


def _mediana(datas: list[date]) -> int | None:
    """Mediana das datas como ordinal; None sem datas."""
    if not datas:
        return None
    return statistics.median_low(d.toordinal() for d in datas)


def _aceita(valor: date | None, mediana: int | None) -> bool:
    if valor is None or valor.year < ANO_MINIMO:
        return False
    return mediana is None or abs(valor.toordinal() - mediana) < LIMITE_DIAS


def _datas_firmes(dias: dict[Path, list[Take]], report: Report) -> dict[Path, date | None]:
    """Data do bext ou do prefixo aceito de cada diaria; None quando nao ha."""
    bext = {dia: _data_bext(takes) for dia, takes in dias.items()}
    prefixos = {dia: _data_prefixo(takes) for dia, takes in dias.items()}
    base = [d for d in bext.values() if d] or [
        d for d, _ in prefixos.values() if d and d.year >= ANO_MINIMO
    ]
    mediana = _mediana(base)
    datas: dict[Path, date | None] = {}
    for dia in dias:
        prefixo, casou = prefixos[dia]
        if bext[dia] is not None:
            datas[dia] = bext[dia]
        elif _aceita(prefixo, mediana):
            datas[dia] = prefixo
        else:
            datas[dia] = None
            if casou:
                report.info(
                    "PRODUCTION_CLOCK_UNSET",
                    f"{dia.name}: the date in the file names was refused; "
                    "the recorder clock looks unset",
                    {"day": dia.name},
                )
    return datas


def _datas_dos_dias(dias: dict[Path, list[Take]], report: Report) -> dict[Path, date | None]:
    """Data de cada diaria: as firmes e, para as outras, a do nome da pasta."""
    datas = _datas_firmes(dias, report)
    mediana = _mediana([d for d in datas.values() if d is not None])
    ano = date.fromordinal(mediana).year if mediana is not None else None
    for dia, valor in datas.items():
        if valor is None:
            datas[dia] = _data_pasta(dia.name, ano)
    return datas


def _menor_tc(takes: list[Take]) -> float:
    valores = [t.timecode for t in takes if t.timecode is not None]
    return min(valores) if valores else float("inf")


def ordenar_dias(takes: list[Take], report: Report) -> list[Bloco]:
    """Um bloco por diaria, na ordem das datas; as sem data no fim, pelo nome."""
    dias: dict[Path, list[Take]] = {}
    for take in takes:
        dias.setdefault(take.day, []).append(take)
    datas = _datas_dos_dias(dias, report)
    datados = [dia for dia in dias if datas[dia] is not None]
    sem_data = sorted((dia for dia in dias if datas[dia] is None), key=lambda d: d.name.casefold())
    datados.sort(key=lambda d: (datas[d], _menor_tc(dias[d]), d.name.casefold()))
    for dia in sem_data:
        report.info(
            "PRODUCTION_DAY_UNDATED",
            f"{dia.name}: no date found; the day goes to the end, in name order",
            {"day": dia.name},
        )
    return [Bloco(day=dia, date=datas[dia], takes=dias[dia]) for dia in datados + sem_data]


def _desdobrar_pelo_timecode(takes: list[Take], sample_rate: int) -> None:
    """Sem datas no bext: na ordem do nome, o timecode mais de 12 h abaixo do maior
    timecode anterior passou da meia-noite e ganha mais um dia."""
    dia, meio = DIA_SEGUNDOS * sample_rate, MEIO_DIA_SEGUNDOS * sample_rate
    dias, maior = 0, None
    for take in sorted(takes, key=lambda t: t.name_key):
        if maior is not None and take.timecode + dias * dia < maior - meio:
            dias += 1
        take.dias = dias
        valor = take.timecode + dias * dia
        maior = valor if maior is None else max(maior, valor)


def _desdobrar_meia_noite(
    bloco: Bloco, com_tc: list[Take], sample_rate: int, report: Report
) -> None:
    """Soma 24 h por dia ao timecode dos takes depois da meia-noite da diaria.

    Com a OriginationDate do bext em todos os takes, os dias sao a diferenca para
    a menor data da diaria; senao vale a queda de mais de 12 h na ordem do nome.
    """
    datas = [_data_do_take(take) for take in com_tc]
    if datas and all(datas):
        base = min(datas)
        for take, valor in zip(com_tc, datas):
            take.dias = (valor - base).days
    else:
        _desdobrar_pelo_timecode(com_tc, sample_rate)
    depois = sum(1 for take in com_tc if take.dias)
    if depois:
        report.info(
            "PRODUCTION_MIDNIGHT",
            f"{bloco.day.name}: takes after midnight placed after the evening takes",
            {"day": bloco.day.name, "takes": depois},
        )


def tc_desdobrado(take: Take, sample_rate: int) -> int:
    """Timecode do take com os dias somados depois da meia-noite."""
    return take.timecode + take.dias * DIA_SEGUNDOS * sample_rate


def _posicionar_com_tc(bloco: Bloco, com_tc: list[Take], inicio: int, sample_rate: int) -> int:
    """Posiciona os takes com timecode a partir do primeiro e devolve o fim do ultimo."""
    fim = inicio
    if not com_tc:
        return fim
    bloco.timecode = com_tc[0].timecode
    base = tc_desdobrado(com_tc[0], sample_rate)
    for take in com_tc:
        take.start = inicio + (tc_desdobrado(take, sample_rate) - base)
        fim = max(fim, take.start + take.frames)
    return fim


def _posicionar(bloco: Bloco, sample_rate: int, report: Report) -> int:
    """Posiciona os takes do bloco e devolve o fim do ultimo take."""
    com_tc = [t for t in bloco.takes if t.timecode is not None]
    _desdobrar_meia_noite(bloco, com_tc, sample_rate, report)
    com_tc.sort(key=lambda t: (tc_desdobrado(t, sample_rate), t.name_key))
    sem_tc = sorted((t for t in bloco.takes if t.timecode is None), key=lambda t: t.name_key)
    inicio = bloco.start + PRE_ROLL_SEGUNDOS * sample_rate
    bloco.locator = inicio
    fim = _posicionar_com_tc(bloco, com_tc, inicio, sample_rate)
    intervalo = INTERVALO_SEGUNDOS * sample_rate
    cursor = fim + intervalo if com_tc else inicio
    for take in sem_tc:
        take.start = cursor
        fim = max(fim, take.start + take.frames)
        cursor = take.start + take.frames + intervalo
    if sem_tc:
        report.info(
            "PRODUCTION_NO_TIMECODE",
            f"{bloco.day.name}: no timecode in these files; takes placed in name order",
            {"day": bloco.day.name, "takes": len(sem_tc)},
        )
    bloco.takes = com_tc + sem_tc
    return fim


def montar_blocos(takes: list[Take], sample_rate: int, report: Report) -> list[Bloco]:
    """Ordena as diarias e posiciona os takes de cada bloco, em samples exatos."""
    blocos = ordenar_dias(takes, report)
    inicio = 0
    for bloco in blocos:
        bloco.start = inicio
        fim = _posicionar(bloco, sample_rate, report)
        proximo = fim + PRE_ROLL_SEGUNDOS * sample_rate
        inicio = -(-proximo // sample_rate) * sample_rate
    return blocos


def _primeiro_ixml_real(takes: list[Take]):
    """Primeiro Ixml vindo de um chunk iXML real, pulando o fallback do bext."""
    for take in takes:
        for arq in take.arquivos:
            if arq.ixml is not None and arq.ixml.source != "bext":
                return arq.ixml
    return None


def taxa_de_timecode(takes: list[Take], report: Report) -> TimecodeRate:
    """TIMECODE_RATE e FLAG do primeiro iXML real dos takes; senao 24 fps NDF."""
    ixml = _primeiro_ixml_real(takes)
    if ixml is not None and ixml.timecode_rate:
        try:
            fps = exact_edit_rate(ixml.timecode_rate)
            return TimecodeRate(fps.numerator, fps.denominator, ixml.timecode_flag == "DF")
        except (ValueError, ZeroDivisionError):
            pass
    report.info(
        "PRODUCTION_TC_RATE_ASSUMED",
        "no timecode rate in these files; 24 fps assumed for display",
    )
    return TAXA_PADRAO


def marcadores(blocos: list[Bloco], rate: TimecodeRate, sample_rate: int) -> list[MarkerIR]:
    """Um locator por bloco nos 10 s do bloco, com a data e o timecode real."""
    return [
        MarkerIR(
            id=f"b{indice}",
            name=bloco.nome_do_locator(rate, sample_rate),
            position_samples=bloco.locator,
        )
        for indice, bloco in enumerate(blocos)
    ]
