"""Pan estatico de track no caminho AAF -> Ableton Live."""

from __future__ import annotations

from pathlib import Path

import aaf2
import pytest
from aaf2.auid import AUID
from aaf2.rational import AAFRational

from aafton.aaf_read import read_aaf
from aafton.als_write import WriteOptions, write_session
from aafton.als_xml import (
    classify_id_tags,
    load_als,
    save_als,
    validate_document,
)
from aafton.ir import ClipIR, SessionIR, TrackIR
from aafton.report import Report
from aafton.stereo import process_stereo_pairing
from aafton.timecode import TimecodeRate
from tests.support.paths import private_fixture


@pytest.fixture
def premiere_aaf() -> Path:
    return private_fixture("dialect_premiere.aaf")


TEMPLATE = Path("templates/live12.als")

MONO_AUDIO_PAN_AUID = AUID("9d2ea893-0968-11d3-8a38-0050040ef7d2")
PAN_PARAMETER_AUID = AUID("e4962322-2267-11d3-8a4c-0050040ef7d2")


def _build_track_pan_aaf(
    path: Path,
    *,
    value: tuple[int, int] = (1, 2),
    varying: bool = False,
    with_sequence: bool = True,
) -> Path:
    """AAF minimo com Mono Audio Pan envolvendo a Sequence da track."""
    with aaf2.open(str(path), "w") as f:
        composition = f.create.CompositionMob("pan")
        f.content.mobs.append(composition)

        parameter_def = f.create.ParameterDef(
            PAN_PARAMETER_AUID,
            "Pan value",
            "",
            f.dictionary.lookup_typedef("Rational"),
        )
        f.dictionary["ParameterDefinitions"].append(parameter_def)

        operation_def = f.create.OperationDef(
            MONO_AUDIO_PAN_AUID,
            "Mono Audio Pan",
            "",
        )
        operation_def["IsTimeWarp"].value = False
        operation_def["NumberInputs"].value = 1
        operation_def["DataDefinition"].value = f.dictionary.lookup_datadef("Sound")
        operation_def["ParametersDefined"].value.append(parameter_def)
        f.dictionary["OperationDefinitions"].append(operation_def)

        sequence = f.create.Sequence(media_kind="sound")
        sequence.components.append(f.create.Filler("sound", length=48000))

        operation = f.create.OperationGroup(operation_def)
        operation.media_kind = "sound"
        operation.length = 48000
        if with_sequence:
            operation.segments.append(sequence)

        if varying:
            interpolation_def = f.create.InterpolationDef(
                AUID("5b7199c0-6744-11d2-8326-00609719b0fb"),
                "Linear",
                "",
            )
            f.dictionary["InterpolationDefinitions"].append(interpolation_def)
            parameter = f.create.VaryingValue(parameter_def, interpolation_def)
            for time, pan in (((0, 1), (0, 1)), ((1, 1), (1, 1))):
                point = f.create.ControlPoint()
                point["Time"].value = AAFRational(*time)
                point["Value"].value = AAFRational(*pan)
                parameter["PointList"].append(point)
        else:
            parameter = f.create.ConstantValue(parameter_def, AAFRational(*value))
        operation.parameters.append(parameter)

        slot = composition.create_timeline_slot(edit_rate=48000)
        slot.name = "PAN TEST"
        slot.segment = operation

        timecode_slot = composition.create_timeline_slot(edit_rate=25)
        timecode_slot.name = "TC"
        timecode = f.create.Timecode(fps=25, drop=False)
        timecode.start = 0
        timecode.length = 25
        timecode_slot.segment = timecode

    return path


def _session_with_tracks(*tracks: TrackIR) -> SessionIR:
    return SessionIR(
        name="pan",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=0,
        tracks=list(tracks),
    )


def test_premiere_real_importa_pan_estatico_hard_left_right(premiere_aaf):
    report = Report(strict=False)

    session = read_aaf(premiere_aaf, report, fades="keep")

    assert [track.pan for track in session.tracks] == [-1.0, 1.0] * 4
    imported = [issue for issue in report.issues if issue.code == "AAF_STATIC_PAN_IMPORTED"]
    assert len(imported) == 1
    assert imported[0].context["count"] == 8


def test_pan_racional_de_metade_importa_como_centro(tmp_path):
    source = _build_track_pan_aaf(tmp_path / "pan_center.aaf", value=(7, 14))
    report = Report(strict=False)

    session = read_aaf(source, report)

    assert session.tracks[0].pan == pytest.approx(0.0)
    assert [issue.code for issue in report.issues].count("AAF_STATIC_PAN_IMPORTED") == 1


def test_pan_varying_value_nao_e_rebaixado_para_constante(tmp_path):
    source = _build_track_pan_aaf(tmp_path / "pan_varying.aaf", varying=True)
    report = Report(strict=False)

    session = read_aaf(source, report)

    assert session.tracks[0].pan == 0.0
    unsupported = [
        issue for issue in report.issues if issue.code == "AAF_PAN_AUTOMATION_UNSUPPORTED"
    ]
    assert len(unsupported) == 1
    assert unsupported[0].context["track"] == "PAN TEST"
    assert not [issue for issue in report.issues if issue.code == "AAF_STATIC_PAN_IMPORTED"]


def test_pan_aaf_fora_do_intervalo_e_erro_sem_clamp_silencioso(tmp_path):
    source = _build_track_pan_aaf(tmp_path / "pan_invalid.aaf", value=(3, 2))
    report = Report(strict=False)

    session = read_aaf(source, report)

    assert session.tracks[0].pan == 0.0
    invalid = [issue for issue in report.issues if issue.code == "AAF_PAN_VALUE_INVALID"]
    assert len(invalid) == 1
    assert invalid[0].severity == "error"
    assert report.incomplete is True


def test_pan_de_slot_sem_sequence_nao_e_contado_como_importado(tmp_path):
    source = _build_track_pan_aaf(
        tmp_path / "pan_without_sequence.aaf",
        value=(0, 1),
        with_sequence=False,
    )
    report = Report(strict=False)

    session = read_aaf(source, report)

    assert session.tracks == []
    assert not [issue for issue in report.issues if issue.code == "AAF_STATIC_PAN_IMPORTED"]


def test_writer_escreve_pan_manual_do_ir(tmp_path):
    track = TrackIR(
        id="t1",
        source_index=0,
        name="PAN",
        kind="audio",
        channel_count=1,
        pan=-0.25,
    )
    destination = tmp_path / "pan.als"
    report = Report(strict=False)

    write_session(
        _session_with_tracks(track),
        TEMPLATE,
        destination,
        WriteOptions(sample_rate=48000),
        report,
    )

    root = load_als(destination)
    posicionais, _ = classify_id_tags(root)
    validate_document(root, posicionais)
    manual = root.find(".//LiveSet/Tracks/AudioTrack/DeviceChain/Mixer/Pan/Manual")
    assert manual is not None
    assert float(manual.get("Value")) == pytest.approx(-0.25)


def test_writer_pan_invalido_registra_erro_e_centraliza_em_permissivo(tmp_path):
    track = TrackIR(
        id="t1",
        source_index=0,
        name="PAN INVALIDO",
        kind="audio",
        channel_count=1,
        pan=1.5,
    )
    destination = tmp_path / "pan_invalid.als"
    report = Report(strict=False)

    write_session(
        _session_with_tracks(track),
        TEMPLATE,
        destination,
        WriteOptions(sample_rate=48000, permissive=True),
        report,
    )

    root = load_als(destination)
    manual = root.find(".//LiveSet/Tracks/AudioTrack/DeviceChain/Mixer/Pan/Manual")
    assert manual is not None
    assert float(manual.get("Value")) == 0.0
    invalid = [issue for issue in report.issues if issue.code == "PAN_VALUE_INVALID"]
    assert len(invalid) == 1
    assert invalid[0].severity == "error"


def test_writer_sobrescreve_pan_do_prototipo_mesmo_quando_ir_esta_no_centro(tmp_path):
    prototype = load_als(TEMPLATE)
    prototype_pan = prototype.find(".//LiveSet/Tracks/AudioTrack/DeviceChain/Mixer/Pan/Manual")
    assert prototype_pan is not None
    prototype_pan.set("Value", "0.75")
    custom_template = tmp_path / "template_with_pan.als"
    save_als(prototype, custom_template)

    destination = tmp_path / "center.als"
    write_session(
        _session_with_tracks(
            TrackIR(
                id="t1",
                source_index=0,
                name="CENTER",
                kind="audio",
                channel_count=1,
                pan=0.0,
            )
        ),
        custom_template,
        destination,
        WriteOptions(sample_rate=48000),
        Report(strict=False),
    )

    root = load_als(destination)
    manual = root.find(".//LiveSet/Tracks/AudioTrack/DeviceChain/Mixer/Pan/Manual")
    assert manual is not None
    assert float(manual.get("Value")) == 0.0


def test_pair_hard_left_right_centraliza_so_depois_do_sucesso():
    left = TrackIR(
        id="l",
        source_index=0,
        name="DX_L",
        kind="audio",
        channel_count=1,
        pan=-1.0,
    )
    right = TrackIR(
        id="r",
        source_index=1,
        name="DX_R",
        kind="audio",
        channel_count=1,
        pan=1.0,
    )
    session = _session_with_tracks(left, right)
    report = Report(strict=False)

    process_stereo_pairing(session, "pair", report, None, dry_run=True)

    assert len(session.tracks) == 1
    assert session.tracks[0].name == "DX"
    assert session.tracks[0].pan == 0.0
    centered = [issue for issue in report.issues if issue.code == "STEREO_PAN_CENTERED"]
    assert len(centered) == 1


def test_pair_com_pan_incompativel_preserva_as_duas_tracks():
    left = TrackIR(
        id="l",
        source_index=0,
        name="DX_L",
        kind="audio",
        channel_count=1,
        pan=-0.5,
    )
    right = TrackIR(
        id="r",
        source_index=1,
        name="DX_R",
        kind="audio",
        channel_count=1,
        pan=0.5,
    )
    session = _session_with_tracks(left, right)
    report = Report(strict=False)

    process_stereo_pairing(session, "pair", report, None, dry_run=True)

    assert session.tracks == [left, right]
    assert (left.pan, right.pan) == (-0.5, 0.5)
    rejected = [issue for issue in report.issues if issue.code == "STEREO_PAIR_REJECTED"]
    assert len(rejected) == 1
    assert rejected[0].context["reason"] == "pan"


@pytest.mark.parametrize(
    ("left_pan", "right_pan"),
    [
        (-1.0000000005, 1.0000000005),
        (None, 1.0),
    ],
)
def test_pair_pan_invalido_nao_e_mascarado_por_tolerancia(left_pan, right_pan):
    left = TrackIR(
        id="l",
        source_index=0,
        name="DX_L",
        kind="audio",
        channel_count=1,
        pan=left_pan,
    )
    right = TrackIR(
        id="r",
        source_index=1,
        name="DX_R",
        kind="audio",
        channel_count=1,
        pan=right_pan,
    )
    session = _session_with_tracks(left, right)
    report = Report(strict=False)

    process_stereo_pairing(session, "pair", report, None, dry_run=True)

    assert session.tracks == [left, right]
    rejected = [issue for issue in report.issues if issue.code == "STEREO_PAIR_REJECTED"]
    assert len(rejected) == 1
    assert rejected[0].context["reason"] == "pan_invalid"


def test_pair_hard_lr_rejeitado_por_midia_preserva_pans():
    left_clip = ClipIR(
        id="lc",
        track_id="l",
        channel_index=0,
        event_index=0,
        name="L",
        media_id="missing-left",
        timeline_start_samples=0,
        timeline_end_samples=48000,
        source_offset_samples=0,
    )
    right_clip = ClipIR(
        id="rc",
        track_id="r",
        channel_index=0,
        event_index=0,
        name="R",
        media_id="missing-right",
        timeline_start_samples=0,
        timeline_end_samples=48000,
        source_offset_samples=0,
    )
    left = TrackIR(
        id="l",
        source_index=0,
        name="DX_L",
        kind="audio",
        channel_count=1,
        pan=-1.0,
        clips=[left_clip],
    )
    right = TrackIR(
        id="r",
        source_index=1,
        name="DX_R",
        kind="audio",
        channel_count=1,
        pan=1.0,
        clips=[right_clip],
    )
    session = _session_with_tracks(left, right)

    process_stereo_pairing(session, "pair", Report(strict=False), None, dry_run=True)

    assert session.tracks == [left, right]
    assert (left.pan, right.pan) == (-1.0, 1.0)


def test_pair_central_central_preserva_comportamento_atual():
    left = TrackIR(
        id="l",
        source_index=0,
        name="DX_L",
        kind="audio",
        channel_count=1,
    )
    right = TrackIR(
        id="r",
        source_index=1,
        name="DX_R",
        kind="audio",
        channel_count=1,
    )
    session = _session_with_tracks(left, right)

    process_stereo_pairing(session, "pair", Report(strict=False), None, dry_run=True)

    assert len(session.tracks) == 1
    assert session.tracks[0].pan == 0.0


def test_premiere_real_pair_escreve_quatro_tracks_centrais_no_als(premiere_aaf, tmp_path):
    report = Report(strict=False)
    session = read_aaf(premiere_aaf, report, fades="keep")
    shared_path = str(tmp_path / "resolved-by-test.wav")
    for asset in session.media:
        asset.status = "resolved"
        asset.resolved_path = shared_path

    process_stereo_pairing(session, "pair", report, tmp_path, dry_run=True)

    assert len(session.tracks) == 4
    assert all(track.pan == 0.0 for track in session.tracks)
    assert sum(len(track.clips) for track in session.tracks) == 174

    destination = tmp_path / "premiere_pair.als"
    write_session(
        session,
        TEMPLATE,
        destination,
        WriteOptions(sample_rate=48000, stereo="pair"),
        report,
    )
    root = load_als(destination)
    tracks = root.findall(".//LiveSet/Tracks/AudioTrack")
    assert len(tracks) == 4
    assert all(
        float(track.find("DeviceChain/Mixer/Pan/Manual").get("Value")) == 0.0 for track in tracks
    )
