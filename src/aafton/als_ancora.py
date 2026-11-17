"""Ancoragem de um set do Live sem o registro do aafton (o ``.aafton.json``).

Sem sidecar, o compasso 1 do Live (o tempo 0) cai no timecode que o usuario
da: ``StartSpec`` traz o texto do timecode, a taxa de frames e o drop frame.
O tempo vem do set e a taxa de amostragem da sessao e a da maioria dos clips de
audio do arranjo, lida numa varredura antes da ancora (empate ou nenhum
arquivo legivel: 48 000).

Sem inicio dado, vale ``01:00:00:00`` a 24 fps, com o aviso
``ALS_START_TC_ASSUMED``. O locator ``TC ...`` do aafton so preenche esse
valor padrao: um inicio explicito vence, com ``ALS_START_TC_OVERRIDES_LOCATOR``
quando difere. O texto do locator e lido na taxa de frames escolhida.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from .als_read_clips import _media_path
from .als_render import e_video
from .ltc import RATES_LTC
from .report import Report
from .sidecar import Anchor, anchor_locator_label, sidecar_missing
from .timecode import TimecodeRate, label_to_samples

INICIO_PADRAO = "01:00:00:00"
TAXA_DE_FRAMES_PADRAO = Fraction(24)
TAXA_DE_AMOSTRAGEM_PADRAO = 48000
TEMPO_PADRAO = 120.0

_CLIPS_DO_ARRANJO = (
    "./LiveSet/Tracks/AudioTrack/DeviceChain/MainSequencer/Sample/ArrangerAutomation/Events/"
    "AudioClip"
)


@dataclass(frozen=True)
class StartSpec:
    """O inicio de um set sem registro: timecode do compasso 1 e a taxa de frames.

    ``assumed`` marca o valor padrao, que ninguem deu: o locator ``TC`` do
    aafton pode substitui-lo, e sem locator ele vira o aviso
    ``ALS_START_TC_ASSUMED``.
    """

    start_tc: str = INICIO_PADRAO
    frame_rate: Fraction = TAXA_DE_FRAMES_PADRAO
    drop_frame: bool = False
    assumed: bool = False

    @property
    def timecode_rate(self) -> TimecodeRate:
        taxa = Fraction(self.frame_rate)
        return TimecodeRate(taxa.numerator, taxa.denominator, self.drop_frame)


def start_padrao(
    frame_rate: Fraction = TAXA_DE_FRAMES_PADRAO, drop_frame: bool = False
) -> StartSpec:
    """O inicio que ninguem deu: 01:00:00:00 na taxa dada, padrao 24 fps."""
    return StartSpec(INICIO_PADRAO, frame_rate, drop_frame, assumed=True)


def start_da_linha(start_tc: str | None, rate: TimecodeRate | None) -> StartSpec:
    """O inicio de ``--start-tc`` e ``--frame-rate``; o que falta fica no padrao."""
    taxa = Fraction(rate.num, rate.den) if rate else TAXA_DE_FRAMES_PADRAO
    drop = bool(rate and rate.drop_frame)
    if not start_tc:
        return start_padrao(taxa, drop)
    return StartSpec(start_tc.strip(), taxa, drop)


def start_do_pedido(opcoes: dict) -> StartSpec:
    """O inicio que o Studio manda nas opcoes do export ("start_tc", "frame_rate")."""
    texto = str(opcoes.get("frame_rate") or "").strip().lower()
    rate = RATES_LTC.get(texto) if texto else None
    if texto and rate is None:
        raise ValueError(f"unknown frame rate: {texto!r}; use {', '.join(RATES_LTC)}")
    return start_da_linha(opcoes.get("start_tc") or None, rate)


def _taxa_do_arquivo(caminho: str) -> int | None:
    from .media_extract import read_audio_metadata

    arquivo = Path(caminho)
    if not arquivo.is_file():
        return None
    try:
        return int(read_audio_metadata(arquivo)["sample_rate"])
    except Exception:  # noqa: BLE001 - arquivo ilegivel nao vota
        return None


def taxa_da_maioria(root: ET.Element, set_path: Path) -> int:
    """A taxa de amostragem da maioria dos clips de audio do arranjo.

    Contam os clips de audio que nao sao video e cujo arquivo e legivel,
    inclusive os que serao deixados de fora. Empate ou nenhum: 48 000.
    """
    taxas: dict[str, int | None] = {}
    contagem: Counter[int] = Counter()
    for node in root.findall(_CLIPS_DO_ARRANJO):
        try:
            caminho = _media_path(node, set_path)
        except ValueError:
            continue
        if e_video(caminho):
            continue
        if caminho not in taxas:
            taxas[caminho] = _taxa_do_arquivo(caminho)
        if taxas[caminho]:
            contagem[taxas[caminho]] += 1
    ordem = contagem.most_common()
    if not ordem or (len(ordem) > 1 and ordem[0][1] == ordem[1][1]):
        return TAXA_DE_AMOSTRAGEM_PADRAO
    return int(ordem[0][0])


def _locator_tc(root: ET.Element) -> tuple[str, str] | None:
    """(nome, timecode) do locator 'TC ' do aafton, quando o set tem um."""
    for locator in root.findall(".//LiveSet/Locators/Locators/Locator"):
        nome_node = locator.find("Name")
        nome = nome_node.get("Value", "") if nome_node is not None else ""
        rotulo = anchor_locator_label(nome) if nome.startswith("TC ") else None
        if rotulo is not None:
            return nome, rotulo
    return None


def _fps(rate: TimecodeRate) -> str:
    texto = f"{float(rate.fps):g}"
    return f"{texto}df" if rate.drop_frame else texto


def _escolher_rotulo(
    report: Report, start: StartSpec, locator: tuple[str, str] | None, taxa: int
) -> str:
    """O timecode do compasso 1: o inicio dado, o locator ou o padrao."""
    rate = start.timecode_rate
    if locator is None:
        if start.assumed:
            report.warning(
                "ALS_START_TC_ASSUMED",
                f"this set was not made by aafton: bar 1 is taken as {start.start_tc} at "
                f"{_fps(rate)} fps; give --start-tc and --frame-rate to change it",
                {"start_tc": start.start_tc, "frame_rate": _fps(rate)},
            )
        return start.start_tc
    nome, rotulo = locator
    if start.assumed:
        report.warning(
            "SIDECAR_RECOVERED",
            "the anchoring came from the locator because the sidecar was not there",
            {"locator": nome},
        )
        return rotulo
    if label_to_samples(rotulo, rate, taxa) != label_to_samples(start.start_tc, rate, taxa):
        report.warning(
            "ALS_START_TC_OVERRIDES_LOCATOR",
            f"the start {start.start_tc} given replaces the locator {nome!r} of the set",
            {"start_tc": start.start_tc, "locator": nome},
        )
    return start.start_tc


def ancora_sem_sidecar(
    path: Path, root: ET.Element, report: Report, start: StartSpec | None, bpm: float
) -> Anchor:
    """A ancoragem de um set sem sidecar.

    Sem ``start`` e sem o locator ``TC`` do aafton nao ha ancora: a recusa e a
    de sempre (``SidecarMissingError``), para quem precisa do registro.
    """
    locator = _locator_tc(root)
    if start is None:
        if locator is None:
            raise sidecar_missing(path)
        start = start_padrao()
    taxa = taxa_da_maioria(root, path)
    rotulo = _escolher_rotulo(report, start, locator, taxa)
    inicio = round(label_to_samples(rotulo, start.timecode_rate, taxa))
    return Anchor(
        live_origin_samples=inicio,
        session_start_samples=inicio,
        sample_rate=taxa,
        timecode_rate=start.timecode_rate,
        bpm=bpm,
        source_path=str(path),
    )
