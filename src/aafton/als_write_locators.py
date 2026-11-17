import xml.etree.ElementTree as ET
from typing import Any

from .als_paths import LOCATOR_FIELDS
from .als_write_base import AnchorError, WriteOptions, to_beats
from .als_xml import IdAllocator, clone, renumber_siblings, set_value
from .ir import PRODUCTION_LAYOUT, SessionIR
from .report import Report
from .timecode import TimecodeRate, label_to_samples, samples_to_label


def fps_do_nome(rate: TimecodeRate) -> str:
    """O fps como aparece no locator: `23.976`, `29.97`, `25`."""
    if rate.den == 1:
        return str(rate.num)
    return f"{rate.num / rate.den:.3f}".rstrip("0")


def _locator_ancora(
    locator_prototype: ET.Element,
    allocator: IdAllocator,
    posicionais: list[str],
    unicos: set[str],
    origin: int,
    session: SessionIR,
) -> ET.Element:
    anchor_locator = clone(locator_prototype, allocator, posicionais, unicos)
    set_value(anchor_locator, LOCATOR_FIELDS["time"], 0.0)
    anchor_label = samples_to_label(origin, session.timecode_rate, session.sample_rate)
    # O Set nao tem campo de fps que o SDK leia: a extensao tira daqui a regua do painel.
    fps = fps_do_nome(session.timecode_rate)
    nome = f"TC {anchor_label} @ {session.sample_rate} @ {fps}"
    set_value(anchor_locator, LOCATOR_FIELDS["name"], nome)
    if "is_song_start" in LOCATOR_FIELDS:
        iss = anchor_locator.find(LOCATOR_FIELDS["is_song_start"])
        if iss is not None:
            iss.set("Value", "true")
        else:
            ET.SubElement(anchor_locator, LOCATOR_FIELDS["is_song_start"], {"Value": "true"})
    return anchor_locator


def _locator_hora(
    locator_prototype: ET.Element,
    allocator: IdAllocator,
    posicionais: list[str],
    unicos: set[str],
    origin: int,
    session: SessionIR,
    options: WriteOptions,
    report: Report,
) -> ET.Element | None:
    hour_label = "01:00:00;00" if session.timecode_rate.drop_frame else "01:00:00:00"
    hour_samples = int(label_to_samples(hour_label, session.timecode_rate, session.sample_rate))
    if hour_samples < origin:
        report.info(
            "HOUR_LOCATOR_BEFORE_ORIGIN",
            f"the 1 hour mark ({hour_label}) falls before the origin, locator omitted",
        )
        return None
    hour_locator = clone(locator_prototype, allocator, posicionais, unicos)
    set_value(
        hour_locator,
        LOCATOR_FIELDS["time"],
        to_beats(hour_samples, origin, options.sample_rate, options.bpm),
    )
    set_value(hour_locator, LOCATOR_FIELDS["name"], hour_label)
    return hour_locator


def _locator_marcador(
    marker: Any,
    locator_prototype: ET.Element,
    allocator: IdAllocator,
    posicionais: list[str],
    unicos: set[str],
    options: WriteOptions,
    report: Report,
) -> ET.Element | None:
    try:
        xml_loc = clone(locator_prototype, allocator, posicionais, unicos)
        set_value(
            xml_loc,
            LOCATOR_FIELDS["time"],
            to_beats(
                marker.position_samples,
                options.live_origin_samples,
                options.sample_rate,
                options.bpm,
            ),
        )
        set_value(xml_loc, LOCATOR_FIELDS["name"], marker.name)
        set_value(xml_loc, LOCATOR_FIELDS["annotation"], marker.comment or "")
    except AnchorError:
        report.error(
            "MARKER_BEFORE_ORIGIN",
            f"the marker {marker.name!r} starts before the origin",
            {"marker_name": marker.name},
        )
        return None
    return xml_loc


def write_locators(
    root: ET.Element,
    session: SessionIR,
    options: WriteOptions,
    report: Report,
    allocator: IdAllocator,
    posicionais: list[str],
    unicos: set[str],
    locator_prototype: ET.Element,
    origin: int,
) -> None:
    locators = root.find(".//LiveSet/Locators/Locators")
    if locators is None:
        report.warning("NO_LOCATORS", "the template has no Locators container")
        return

    for velho in list(locators):
        locators.remove(velho)

    # A ordem dos clone() importa: os ids sao alocados na ordem das chamadas.
    # Som direto nao tem timecode continuo: sem ancoragem nem 1 h, so os blocos.
    if session.layout != PRODUCTION_LAYOUT:
        locators.append(
            _locator_ancora(locator_prototype, allocator, posicionais, unicos, origin, session)
        )
        hour_locator = _locator_hora(
            locator_prototype, allocator, posicionais, unicos, origin, session, options, report
        )
        if hour_locator is not None:
            locators.append(hour_locator)

    for marker in session.markers:
        xml_loc = _locator_marcador(
            marker,
            locator_prototype,
            allocator,
            posicionais,
            unicos,
            options,
            report,
        )
        if xml_loc is not None:
            locators.append(xml_loc)

    renumber_siblings(locators, "Locator")
