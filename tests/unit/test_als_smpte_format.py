from pathlib import Path
import pytest

from aafton.als_write import WriteOptions, write_session
from aafton.als_xml import load_als
from aafton.ir import SessionIR
from aafton.report import Report
from aafton.timecode import TimecodeRate

TEMPLATE = Path("templates/live12.als")

pytestmark = pytest.mark.skipif(
    not TEMPLATE.exists(), reason="templates/live12.als ainda nao existe"
)


@pytest.mark.parametrize(
    "tc_rate, expected_smpte",
    [
        (TimecodeRate(24, 1), "1"),
        (TimecodeRate(25, 1), "2"),
        (TimecodeRate(30, 1, drop_frame=False), "3"),
        (TimecodeRate(30000, 1001, drop_frame=True), "4"),
        (TimecodeRate(30000, 1001, drop_frame=False), "5"),
        (TimecodeRate(24000, 1001), "1"),
        (TimecodeRate(50, 1), "0"),
    ],
)
def test_write_session_smpte_format(tmp_path, tc_rate, expected_smpte):
    session = SessionIR(
        name="test_smpte",
        sample_rate=48000,
        timecode_rate=tc_rate,
        start_timecode_samples=48000 * 3600,
        tracks=[],
        markers=[],
        source_path="fonte.aaf",
    )
    destino = tmp_path / f"smpte_{expected_smpte}.als"
    report = Report()
    write_session(
        session,
        TEMPLATE,
        destino,
        WriteOptions(sample_rate=48000, bpm=60.0, live_origin_samples=48000 * 3600),
        report,
    )

    root = load_als(destino)
    smpte_elem = root.find(".//LiveSet/SmpteFormat")
    assert smpte_elem is not None, "Elemento LiveSet/SmpteFormat nao encontrado"
    assert smpte_elem.get("Value") == expected_smpte


def test_anchor_locator_receives_is_song_start(tmp_path):
    session = SessionIR(
        name="test_anchor_is_song_start",
        sample_rate=48000,
        timecode_rate=TimecodeRate(24, 1),
        start_timecode_samples=48000 * 3600,
        tracks=[],
        markers=[],
        source_path="fonte.aaf",
    )
    destino = tmp_path / "anchor_song_start.als"
    report = Report()
    write_session(
        session,
        TEMPLATE,
        destino,
        WriteOptions(sample_rate=48000, bpm=60.0, live_origin_samples=48000 * 3600),
        report,
    )

    root = load_als(destino)
    locators = root.findall(".//LiveSet/Locators/Locators/Locator")
    assert len(locators) >= 1

    # O locator de ancoragem esta no tempo 0.0
    anchor = None
    for loc in locators:
        time_elem = loc.find("Time")
        if time_elem is not None and float(time_elem.get("Value", -1)) == 0.0:
            name_elem = loc.find("Name")
            if name_elem is not None and name_elem.get("Value", "").startswith("TC "):
                anchor = loc
                break

    assert anchor is not None, "Locator de ancoragem nao encontrado"
    song_start_elem = anchor.find("IsSongStart")
    assert song_start_elem is not None, "Elemento IsSongStart nao encontrado no locator"
    assert song_start_elem.get("Value") == "true"
