import pytest
from pathlib import Path
from aafton.session_text import process_session_text
from aafton.ir import SessionIR
from aafton.timecode import TimecodeRate
from aafton.report import Report


def test_markers_parsing_spot_and_teste():
    txt_spot = Path("tests/fixtures/aaf/SPOT_AAF_TESTE.txt")
    txt_teste = Path("tests/fixtures/aaf/teste_aaf_protools_txt.txt")

    if not txt_spot.exists() or not txt_teste.exists():
        pytest.skip("Fixtures missing")

    # 1. Test SPOT_AAF_TESTE
    session_spot = SessionIR(
        name="dummy", sample_rate=48000, start_timecode_samples=0, timecode_rate=TimecodeRate(25, 1)
    )
    rep_spot = Report(strict=False)
    process_session_text(session_spot, "dummy.aaf", str(txt_spot), rep_spot)

    # O analisador lê 17 marcadores desta sessão, e FFOA está em 172800000.
    assert len(session_spot.markers) == 17

    ffoa = [m for m in session_spot.markers if m.name == "FFOA"]
    assert len(ffoa) == 1
    assert ffoa[0].position_samples == 172800000

    # Os dois marcadores em 173126400 produzem dois locators distintos no .als, não um.
    markers_at_pos = [m for m in session_spot.markers if m.position_samples == 173126400]
    assert len(markers_at_pos) == 2
    names_at_pos = {m.name for m in markers_at_pos}
    assert "NUVENS" in names_at_pos
    assert "CAIR" in names_at_pos

    # Os quatro LFOA produzem quatro locators, com o mesmo nome e posições diferentes.
    lfoa = [m for m in session_spot.markers if m.name == "LFOA"]
    assert len(lfoa) == 4
    positions_lfoa = {m.position_samples for m in lfoa}
    assert len(positions_lfoa) == 4  # They all have different positions

    # MARKER_COMMENT_IN_ANNOTATION da zero nesta sessao
    trunc_spot = [i for i in rep_spot.issues if i.code == "MARKER_COMMENT_IN_ANNOTATION"]
    assert len(trunc_spot) == 0

    # 2. Test TESTE_AAF_PROTOOLS
    session_teste = SessionIR(
        name="dummy", sample_rate=48000, start_timecode_samples=0, timecode_rate=TimecodeRate(25, 1)
    )
    rep_teste = Report(strict=False)
    process_session_text(session_teste, "dummy.aaf", str(txt_teste), rep_teste)

    # e cinco na anterior. Afirme os dois numeros no mesmo teste, para que a diferenca fique explicita.
    trunc_teste = [i for i in rep_teste.issues if i.code == "MARKER_COMMENT_IN_ANNOTATION"]
    assert len(trunc_teste) == 1
    assert trunc_teste[0].context["count"] == 5
