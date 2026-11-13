"""Medicao controlada do que o escritor do .als realmente grava.

O schema do Live diz o que o Live aceita; o template diz o que o Live gravou.
Nenhum dos dois diz o que o `aafton` escreve. Para isso o escritor precisa ser
observado, e nao deduzido: a medicao escreve duas sessoes minimas cujas
entradas diferem entre si e do template, e mede o `.als` gerado.

Um caminho so vira `escrito` quando a prova fecha em tres pontos:

1. o valor medido no `.als` gerado bate com o valor previsto a partir da
   entrada, nas duas entradas;
2. as duas entradas produzem valores diferentes, para que o valor venha da
   entrada e nao de uma constante;
3. o valor medido e diferente do que o template ja tinha no mesmo caminho,
   para que a escrita tenha mudado o arquivo.

Presenca sem medicao nao prova escrita, e declaracao em `als_paths` tambem nao.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aafton.als_write import WriteOptions, write_session  # noqa: E402
from aafton.als_xml import load_als  # noqa: E402
from aafton.ir import (  # noqa: E402
    ClipIR,
    MarkerIR,
    MediaAsset,
    SessionIR,
    TrackIR,
)
from aafton.report import Report  # noqa: E402
from aafton.timecode import TimecodeRate  # noqa: E402

# A origem da timeline fica fixa: uma hora em samples a 48 kHz. O que muda
# entre as entradas e a posicao do clip, a duracao, o offset na fonte, o pan e
# os ganhos.
ORIGEM = 172800000
TAXA = 48000
BPM = 60.0
TOLERANCIA = 1e-9

# Onde o envelope de automacao guarda os eventos de ganho da faixa.
CAMINHO_EVENTOS = "AutomationEnvelopes/Envelopes/AutomationEnvelope/Automation/Events/FloatEvent"

# O alvo de automacao do volume e o ponteiro que o envelope carrega.
CAMINHO_ALVO = "DeviceChain/Mixer/Volume/AutomationTarget"
CAMINHO_PONTEE = "AutomationEnvelopes/Envelopes/AutomationEnvelope/EnvelopeTarget/PointeeId"

# O tempo do sentinela do Live, observado no template fixado e nao importado
# do escritor: em templates/live12.als, o FloatEvent de
# AutomationEnvelope/Automation/Events carrega @Time="-63072000". O valor e
# fixado aqui para que a medicao seja independente do escritor: se o escritor
# mudar o sentinela, a prova tem de falhar, e nao acompanhar a mudanca.
SENTINEL_OBSERVADO = -63072000


@dataclass(frozen=True)
class Amostra:
    """Uma entrada controlada do escritor.

    `inicio_relativo`, `duracao` e `offset_fonte` sao samples contados a
    partir da origem da timeline e do comeco da midia. `pontos_ganho` sao
    pares (samples a partir da origem, ganho linear).
    """

    rotulo: str
    pan: float
    ganho_clip: float
    ganho_track: float
    inicio_relativo: int
    duracao: int
    offset_fonte: int
    pontos_ganho: tuple[tuple[int, float], ...]


# Duas entradas controladas: os valores de uma sao todos diferentes dos da
# outra e dos que o template traz. As posicoes caem em multiplos exatos de
# 25 samples, para que a grade de clips do Live e a razao de samples deem o
# mesmo beat.
AMOSTRAS: tuple[Amostra, ...] = (
    Amostra(
        rotulo="A",
        pan=0.25,
        ganho_clip=0.375,
        ganho_track=0.625,
        inicio_relativo=336000,
        duracao=480000,
        offset_fonte=60000,
        pontos_ganho=((336000, 1.0), (480000, 0.5)),
    ),
    Amostra(
        rotulo="B",
        pan=0.75,
        ganho_clip=0.625,
        ganho_track=0.25,
        inicio_relativo=528000,
        duracao=600000,
        offset_fonte=120000,
        pontos_ganho=((528000, 0.25), (600000, 0.75)),
    ),
)


def beats_de(samples: int, bpm: float = BPM, taxa: int = TAXA) -> float:
    """A posicao em beats, pela razao de amostras.

    A previsao nao usa a grade do Live: e a razao exata de samples para beats
    a 60 BPM e 48 kHz, para que o valor esperado seja independente do
    escritor.
    """
    return float(Fraction(samples) * Fraction(bpm) / (60 * taxa))


def _no(root: ET.Element, tag: str) -> ET.Element | None:
    """O primeiro no do tipo pedido no documento."""
    return root.find(f".//{tag}")


def _valor(no: ET.Element, caminho: str) -> str | None:
    """O atributo Value do elemento pedido dentro do no."""
    alvo = no.find(caminho)
    return None if alvo is None else alvo.get("Value")


def _atributo(no: ET.Element, caminho: str) -> str | None:
    """O atributo XML com o nome do caminho, no proprio no."""
    return no.get(caminho.rsplit("/", 1)[-1])


def _eventos(no: ET.Element, caminho: str) -> tuple[tuple[float, float], ...]:
    """Os pares (Time, Value) dos eventos de automacao do envelope."""
    pares = []
    for evento in no.findall(caminho):
        tempo = float(evento.get("Time", "0"))
        valor = float(evento.get("Value", "0"))
        pares.append((tempo, valor))
    return tuple(pares)


def _eventos_esperados(amostra: Amostra) -> tuple[tuple[float, float], ...]:
    """Os pares (Time, Value) que o envelope de ganho deve ter.

    O primeiro evento e o sentinela do Live, antes do primeiro ponto; os
    demais sao os pontos de ganho, na posicao em beats. O tempo do sentinela
    vem do valor observado no template, e nao do escritor.
    """
    primeiro = amostra.pontos_ganho[0][1]
    pontos = []
    for samples, valor in amostra.pontos_ganho:
        pontos.append((beats_de(samples), valor))
    return ((float(SENTINEL_OBSERVADO), primeiro), *pontos)


@dataclass(frozen=True)
class Prova:
    """Uma medida: onde ler o valor no `.als` e qual valor se espera da entrada.

    `leitor` diz como o valor e lido no no raiz ("elemento", "atributo" ou
    "eventos") e `valor` e a chave do valor esperado, em `VALORES_ESPERADOS`.
    """

    raiz: str
    caminho: str
    leitor: str
    valor: str
    novo_no_template: bool = False

    @property
    def chave(self) -> str:
        return f"{self.raiz}/{self.caminho}"


LEITORES: dict[str, Callable[[ET.Element, str], Any]] = {
    "elemento": _valor,
    "atributo": _atributo,
    "eventos": _eventos,
}

VALORES_ESPERADOS: dict[str, Callable[[Amostra], Any]] = {
    "pan": lambda amostra: amostra.pan,
    "ganho_clip": lambda amostra: amostra.ganho_clip,
    "ganho_track": lambda amostra: amostra.ganho_track,
    "inicio": lambda amostra: beats_de(amostra.inicio_relativo),
    "fim": lambda amostra: beats_de(amostra.inicio_relativo + amostra.duracao),
    "offset": lambda amostra: beats_de(amostra.offset_fonte),
    "fonte_fim": lambda amostra: beats_de(amostra.offset_fonte + amostra.duracao),
    "eventos": _eventos_esperados,
}

PROVAS: tuple[Prova, ...] = (
    Prova("AudioTrack", "DeviceChain/Mixer/Pan/Manual", "elemento", "pan"),
    Prova("AudioTrack", "DeviceChain/Mixer/Volume/Manual", "elemento", "ganho_track"),
    Prova("AudioClip", "SampleVolume", "elemento", "ganho_clip"),
    Prova("AudioClip", "CurrentStart", "elemento", "inicio"),
    Prova("AudioClip", "CurrentEnd", "elemento", "fim"),
    Prova("AudioClip", "Time", "atributo", "inicio"),
    Prova("AudioClip", "Loop/LoopStart", "elemento", "offset"),
    Prova("AudioClip", "Loop/LoopEnd", "elemento", "fonte_fim"),
    Prova("AudioClip", "Loop/OutMarker", "elemento", "fonte_fim"),
    Prova("AudioClip", "Loop/HiddenLoopEnd", "elemento", "fonte_fim"),
    Prova("AudioTrack", CAMINHO_EVENTOS, "eventos", "eventos", novo_no_template=True),
)


@dataclass(frozen=True)
class Medicao:
    """O resultado de uma prova: o caminho, se fechou, e o que foi medido.

    `tipo` distingue a prova de escrita, que mede um valor que varia com a
    entrada, da conferencia de integridade, que confere uma ligacao entre
    nos. A conferencia nao prova a escrita de um caminho.
    """

    chave: str
    ok: bool
    medido: tuple[Any, ...] = ()
    previsto: tuple[Any, ...] = ()
    detalhe: str = ""
    tipo: str = "escrita"


def _iguais(medido: Any, previsto: Any) -> bool:
    """Compara valor medido e previsto, com tolerancia para o ponto flutuante."""
    if isinstance(medido, tuple) and isinstance(previsto, tuple):
        if len(medido) != len(previsto):
            return False
        return all(_iguais(a, b) for a, b in zip(medido, previsto))
    if isinstance(previsto, float) or isinstance(medido, float):
        try:
            return abs(float(medido) - float(previsto)) <= TOLERANCIA
        except (TypeError, ValueError):
            return False
    return medido == previsto


def _diferentes(valores: list[Any]) -> bool:
    """Se os valores medidos se distinguem entre si."""
    return len({repr(valor) for valor in valores}) > 1


def sessao(amostra: Amostra, pasta: Path) -> SessionIR:
    """Monta a sessao minima da amostra: uma faixa, um clip e um marcador."""
    wav = pasta / "cena.wav"
    wav.write_bytes(b"RIFF")
    asset = MediaAsset(
        id="m1",
        filename="cena.wav",
        channels=1,
        sample_rate=TAXA,
        status="resolved",
        resolved_path=str(wav),
    )
    faixa = TrackIR(
        id="t0",
        source_index=0,
        name="DIALOGO 1",
        kind="audio",
        channel_count=1,
        pan=amostra.pan,
        gain_linear=amostra.ganho_track,
        gain_points=[(ORIGEM + s, v) for s, v in amostra.pontos_ganho],
    )
    faixa.clips.append(
        ClipIR(
            id="c0",
            track_id=faixa.id,
            channel_index=0,
            event_index=0,
            name="cena.wav",
            media_id="m1",
            timeline_start_samples=ORIGEM + amostra.inicio_relativo,
            timeline_end_samples=ORIGEM + amostra.inicio_relativo + amostra.duracao,
            source_offset_samples=amostra.offset_fonte,
            gain_linear=amostra.ganho_clip,
        )
    )
    return SessionIR(
        name=f"auditoria-{amostra.rotulo}",
        sample_rate=TAXA,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=ORIGEM,
        media=[asset],
        tracks=[faixa],
        markers=[MarkerIR(id="k1", name="corte", position_samples=ORIGEM + 240000)],
    )


def gerar(pasta: Path, amostra: Amostra, template: Path) -> ET.Element:
    """Escreve uma sessao da amostra com o aafton e devolve o documento gerado."""
    destino = pasta / f"gerado-{amostra.rotulo}.als"
    write_session(
        sessao(amostra, pasta),
        template,
        destino,
        WriteOptions(sample_rate=TAXA),
        Report(strict=True),
    )
    return load_als(destino)


def gerar_documentos(pasta: Path, template: Path) -> dict[str, ET.Element]:
    """Escreve um `.als` por amostra e devolve os documentos, por rotulo."""
    documentos = {}
    for amostra in AMOSTRAS:
        documentos[amostra.rotulo] = gerar(pasta, amostra, template)
    return documentos


def _coleta(prova: Prova, documentos: dict[str, ET.Element]) -> tuple[list[Any], list[Any]]:
    """O valor medido e o valor previsto de cada amostra."""
    medidos = []
    previstos = []
    for amostra in AMOSTRAS:
        doc = documentos.get(amostra.rotulo)
        no = None if doc is None else _no(doc, prova.raiz)
        if no is None:
            medidos.append(None)
        else:
            medidos.append(LEITORES[prova.leitor](no, prova.caminho))
        previstos.append(VALORES_ESPERADOS[prova.valor](amostra))
    return (medidos, previstos)


def _confere_template(prova: Prova, base: tuple, template: ET.Element) -> Medicao:
    """A escrita tem que ter mudado o arquivo em relacao ao template."""
    no_tpl = _no(template, prova.raiz)
    if no_tpl is None:
        return Medicao(prova.chave, False, *base, "o template nao tem o no raiz")
    do_template = LEITORES[prova.leitor](no_tpl, prova.caminho)
    if prova.novo_no_template:
        if do_template not in (None, (), []):
            return Medicao(prova.chave, False, *base, "o template ja tinha esse caminho")
    elif any(_iguais(do_template, medido) for medido in base[0]):
        return Medicao(prova.chave, False, *base, "o medido e igual ao do template")
    return Medicao(prova.chave, True, *base, "medido == previsto")


def _mede(prova: Prova, documentos: dict[str, ET.Element], template: ET.Element) -> Medicao:
    """Confere uma prova: o medido bate com o previsto, nas duas entradas."""
    medidos, previstos = _coleta(prova, documentos)
    base = (tuple(medidos), tuple(previstos))
    if any(valor is None for valor in medidos):
        return Medicao(prova.chave, False, *base, "o caminho sumiu do .als gerado")
    if not all(_iguais(m, p) for m, p in zip(medidos, previstos)):
        return Medicao(prova.chave, False, *base, "o medido nao bate com o previsto")
    if not _diferentes(medidos):
        return Medicao(prova.chave, False, *base, "as duas entradas deram o mesmo valor")
    return _confere_template(prova, base, template)


def _ligacao(no: ET.Element) -> tuple[tuple[str | None, str | None], str]:
    """Os dois Ids da ligacao e o motivo de ela nao fechar; vazio se fecha.

    Um Id ausente nao e um Id igual ao outro: a ausencia e diferente do
    valor, e a ligacao so fecha quando os dois nos existem, com Id nao
    vazio, e os Ids sao iguais.
    """
    alvo = no.find(CAMINHO_ALVO)
    ponteiro = no.find(CAMINHO_PONTEE)
    if alvo is None:
        return (None, None), "a faixa nao tem o alvo de automacao do volume"
    if ponteiro is None:
        return (None, None), "o envelope nao tem o EnvelopeTarget/PointeeId"
    id_alvo = (alvo.get("Id") or "").strip()
    id_ponteiro = (ponteiro.get("Value") or "").strip()
    if not id_alvo:
        return (None, None), "o alvo de automacao do volume nao tem Id"
    if not id_ponteiro:
        return (None, None), "o EnvelopeTarget/PointeeId nao tem Value"
    return (id_alvo, id_ponteiro), ""


def _integridade(chave: str, medidos, ok: bool, detalhe: str) -> Medicao:
    """A conferencia de integridade, com o tipo que a distingue da escrita."""
    return Medicao(chave, ok, tuple(medidos), (), detalhe, "integridade")


def _mede_ligacao(documentos: dict[str, ET.Element], template: ET.Element) -> Medicao:
    """Confere que o envelope de ganho aponta para o volume da propria faixa.

    O envelope e novo: o template nao tem evento de automacao de ganho na
    faixa. Sem essa ligacao, os eventos medidos seriam de outra curva.

    E uma conferencia de integridade, e nao uma prova de escrita: ela diz que
    o envelope aponta para o alvo, e nao que o escritor gravou o caminho do
    alvo de automacao. Por isso ela sai com tipo proprio e nao entra em
    `caminhos_provados`.
    """
    chave = "AudioTrack/AutomationEnvelopes/Envelopes/AutomationEnvelope/EnvelopeTarget/PointeeId"
    medidos: list[tuple[str | None, str | None] | None] = []
    problemas: list[str] = []
    for amostra in AMOSTRAS:
        doc = documentos.get(amostra.rotulo)
        no = None if doc is None else _no(doc, "AudioTrack")
        if no is None:
            medidos.append(None)
            problemas.append(f"amostra {amostra.rotulo}: o .als gerado nao tem AudioTrack")
            continue
        ligacao, problema = _ligacao(no)
        medidos.append(ligacao)
        if problema:
            problemas.append(f"amostra {amostra.rotulo}: {problema}")
    if problemas:
        return _integridade(chave, medidos, False, "; ".join(problemas))
    for alvo, ponteiro in medidos:
        if alvo != ponteiro:
            return _integridade(chave, medidos, False, "o envelope nao aponta para o volume")
    no_tpl = _no(template, "AudioTrack")
    if no_tpl is None:
        return _integridade(chave, medidos, False, "o template nao tem AudioTrack")
    if _eventos(no_tpl, CAMINHO_EVENTOS):
        return _integridade(chave, medidos, False, "o template ja tinha envelope de ganho")
    return _integridade(chave, medidos, True, "o envelope aponta para o volume da faixa")


def medir(documentos: dict[str, ET.Element], template: ET.Element) -> list[Medicao]:
    """Mede cada prova contra as duas entradas controladas e o template."""
    medicoes = []
    for prova in PROVAS:
        medicoes.append(_mede(prova, documentos, template))
    medicoes.append(_mede_ligacao(documentos, template))
    return medicoes


def caminhos_provados(medicoes: list[Medicao]) -> set[str]:
    """Os caminhos que a medicao provou escritos.

    A conferencia de integridade da ligacao nao entra: ela prova que o
    envelope aponta para o alvo, e nao que o escritor gravou o caminho do
    alvo de automacao.
    """
    return {m.chave for m in medicoes if m.ok and m.tipo == "escrita"}
