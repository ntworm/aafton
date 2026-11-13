import pytest
from pathlib import Path
from aafton.cli import main
from tests.aaf_factory import build_linked_aaf, wav_bytes

TEMPLATE = Path("templates/live12.als")


@pytest.fixture
def wav(tmp_path):
    caminho = tmp_path / "cena.wav"
    caminho.write_bytes(wav_bytes(48000 * 120))
    return caminho


@pytest.fixture
def aaf(tmp_path, wav):
    return build_linked_aaf(tmp_path / "cena.aaf", [(0, 96000, 24000)], wav)


@pytest.mark.skipif(not TEMPLATE.exists(), reason="Portao B")
def test_hour_locator_roundtrip(aaf, tmp_path):
    als1 = tmp_path / "1.als"
    main(
        [
            "import",
            str(aaf),
            str(als1),
            "--template",
            str(TEMPLATE),
            "--media",
            "link",
            "--live-origin",
            "00:59:50:00",
        ]
    )

    aaf2 = tmp_path / "2.aaf"
    main(["export", str(als1), str(aaf2)])

    als2 = tmp_path / "2.als"
    main(
        [
            "import",
            str(aaf2),
            str(als2),
            "--template",
            str(TEMPLATE),
            "--media",
            "link",
            "--live-origin",
            "00:59:50:00",
        ]
    )

    from aafton.als_read import read_session
    from aafton.report import Report

    session1 = read_session(als1, Report())
    session2 = read_session(als2, Report())

    assert len(session1.markers) == len(session2.markers)
