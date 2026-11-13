from aafton.report import Report
from aafton.aaf_read import read_aaf
from tests.support.paths import fixture_aaf


def test_hummer_gain_curve_oracle():
    aaf_path = fixture_aaf("TESTE_DF_2997_correto.aaf")

    report = Report(strict=False)
    session = read_aaf(aaf_path, report)

    track = session.tracks[0]

    assert len(track.gain_points) == 14

    assert track.gain_points[0][0] == 184016896
    assert track.gain_points[1][0] == 184141824
    assert track.gain_points[2][0] == 184262656
    assert track.gain_points[3][0] == 184395776

    instantes_184417280 = [p for p in track.gain_points if p[0] == 184417280]
    assert len(instantes_184417280) == 3

    v0 = round(instantes_184417280[0][1], 6)
    v1 = round(instantes_184417280[1][1], 6)
    v2 = round(instantes_184417280[2][1], 6)

    assert v0 == 0.058210
    assert v1 == 0.057544
    assert v2 == 0.058210
