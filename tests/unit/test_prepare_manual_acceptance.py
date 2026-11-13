import json
from pathlib import Path

from aafton.aaf_read import read_aaf
from aafton.als_xml import load_als
from aafton.cli import main
from aafton.report import Report
from scripts.prepare_manual_acceptance import prepare_bundle


def _import_generated_combiner(tmp_path):
    bundle_dir = tmp_path / "bundle"
    manifest = prepare_bundle(bundle_dir, Path("templates/live12.als"))
    imported_als = tmp_path / "imported.als"
    import_report = tmp_path / "import-report.json"

    assert (
        main(
            [
                "import",
                manifest["files"]["baseline_aaf"],
                str(imported_als),
                "--stereo",
                "pair",
                "--media",
                "link",
                "--report",
                str(import_report),
            ]
        )
        == 0
    )

    return imported_als


def test_prepare_bundle_cria_sets_audio_aaf_e_manifesto(tmp_path):
    manifest = prepare_bundle(tmp_path, Path("templates/live12.als"))

    for key in (
        "split_als",
        "split_sidecar",
        "pair_als",
        "pair_sidecar",
        "baseline_aaf",
        "stereo_wav",
        "manifest",
    ):
        assert Path(manifest["files"][key]).exists()

    split = load_als(manifest["files"]["split_als"])
    split_tracks = split.findall(".//LiveSet/Tracks/AudioTrack")
    assert [
        float(track.find("DeviceChain/Mixer/Pan/Manual").get("Value")) for track in split_tracks
    ] == [-1.0, 1.0]

    pair = load_als(manifest["files"]["pair_als"])
    pair_tracks = pair.findall(".//LiveSet/Tracks/AudioTrack")
    assert len(pair_tracks) == 1
    assert float(pair_tracks[0].find("DeviceChain/Mixer/Pan/Manual").get("Value")) == 0.0

    reread = read_aaf(manifest["files"]["baseline_aaf"], Report(strict=False))
    assert reread.start_timecode_samples == 3600 * 48000
    assert len(reread.tracks) == 1
    assert reread.tracks[0].channel_count == 2
    assert reread.tracks[0].pan == 0.0
    assert [clip.channel_index for clip in reread.tracks[0].clips] == [0, 1]
    assert sorted(clip.timeline_start_samples for clip in reread.all_clips()) == [
        3601 * 48000,
        3601 * 48000,
    ]

    assert manifest["expected"]["split_pan"] == [-1.0, 1.0]
    assert manifest["expected"]["pair_pan"] == 0.0
    assert manifest["expected"]["left_frequency_hz"] == 440.0
    assert manifest["expected"]["right_frequency_hz"] == 660.0
    assert manifest["reports"]["split"]["errors"] == 0
    assert manifest["reports"]["pair"]["errors"] == 0
    assert manifest["reports"]["aaf"]["errors"] == 0


def test_import_combiner_estereo_emite_um_clip_no_live(tmp_path):
    imported_als = _import_generated_combiner(tmp_path)
    root = load_als(imported_als)

    assert len(root.findall(".//LiveSet/Tracks/AudioTrack//AudioClip")) == 1


def test_import_combiner_estereo_reexporta_sem_overlap(tmp_path):
    imported_als = _import_generated_combiner(tmp_path)
    exported_aaf = tmp_path / "exported.aaf"
    export_report = tmp_path / "export-report.json"

    assert (
        main(
            [
                "export",
                str(imported_als),
                str(exported_aaf),
                "--media",
                "link",
                "--report",
                str(export_report),
            ]
        )
        == 0
    )

    codes = {
        issue["code"] for issue in json.loads(export_report.read_text(encoding="utf-8"))["issues"]
    }
    assert "WRITE_OVERLAP" not in codes
    assert "EXPORT_SELF_CHECK_OK" in codes
