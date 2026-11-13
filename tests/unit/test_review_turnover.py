from pathlib import Path

import pytest

from aafton.aaf_read import read_aaf
from aafton.aaf_write import write_aaf
from aafton.als_read import read_session
from aafton.media_extract import resolve_asset
from aafton.media_oracle import verify_session_media
from aafton.report import Report
from aafton.turnover import TurnoverError, create_turnover_package
from tests.unit.test_review_robustness import make_set


@pytest.mark.parametrize("kind", ["als", "aaf"])
def test_package_reopens_after_relocation_without_originals(tmp_path, kind):
    original = tmp_path / "original"
    original.mkdir()
    source = make_set(original)
    if kind == "aaf":
        session = read_session(source, Report())
        source = original / "review.aaf"
        write_aaf(session, source, Report())
    package = tmp_path / "package"
    result = create_turnover_package(source, output_dir=package)
    assert result.is_valid
    moved = tmp_path / "moved"
    package.rename(moved)
    original.rename(tmp_path / "original-offline")
    path = moved / source.name
    if kind == "als":
        session = read_session(path, Report())
    else:
        session = read_aaf(path, Report())
        for asset in session.media:
            resolve_asset(asset, [moved], Report())
    assert session.media
    for asset in session.media:
        assert asset.status == "resolved"
        assert Path(asset.resolved_path).is_relative_to(moved)


def test_turnover_never_overwrites_existing_delivery(tmp_path):
    source = tmp_path / "source.als"
    source.write_bytes(b"invalid")
    destination = tmp_path / "package"
    destination.mkdir()
    marker = destination / "keep.txt"
    marker.write_bytes(b"original delivery")
    with pytest.raises(TurnoverError, match="already exists"):
        create_turnover_package(source, output_dir=destination)
    assert marker.read_bytes() == b"original delivery"


def test_failed_turnover_leaves_no_published_directory(tmp_path):
    source = tmp_path / "source.als"
    source.write_bytes(b"invalid")
    destination = tmp_path / "package"
    with pytest.raises(TurnoverError):
        create_turnover_package(source, media_dir=tmp_path / "missing", output_dir=destination)
    assert not destination.exists()


def test_empty_media_is_not_a_valid_audit(tmp_path):
    assert not verify_session_media(tmp_path).is_valid


def test_permissive_turnover_keeps_failed_hash_expectation(tmp_path):
    import json
    from aafton.sidecar import sidecar_path_for

    source_dir = tmp_path / "original"
    source_dir.mkdir()
    source = make_set(source_dir)
    sidecar = sidecar_path_for(source)
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    data["media_hashes"] = {
        "source0.wav": "f" * 64,
        "source1.wav": data["media_hashes"]["source1.wav"],
    }
    sidecar.write_text(json.dumps(data), encoding="utf-8")
    result = create_turnover_package(source, output_dir=tmp_path / "package", strict=False)
    assert not result.is_valid
    audit = verify_session_media(result.package_dir / "media", result.sidecar_path)
    assert audit.mismatch_count == 1 and not audit.is_valid


def test_same_basename_with_conflicting_hashes_is_not_deduplicated(tmp_path):
    import hashlib
    from tests.aaf_factory import wav_bytes

    raw = wav_bytes(10)
    (tmp_path / "audio.wav").write_bytes(raw)
    report = verify_session_media(
        tmp_path,
        media_hashes={
            "audio.wav": hashlib.sha256(raw).hexdigest(),
            str(tmp_path / "other" / "audio.wav"): "f" * 64,
        },
    )
    assert report.total_files == 2
    assert not report.is_valid


def test_multiple_descriptor_locators_are_relinked(tmp_path):
    import aaf2
    from tests.aaf_factory import build_linked_aaf, wav_bytes

    source_dir = tmp_path / "original"
    source_dir.mkdir()
    wav = source_dir / "sound.wav"
    wav.write_bytes(wav_bytes(96000))
    aaf = build_linked_aaf(source_dir / "sound.aaf", [(0, 48000, 0)], wav)
    with aaf2.open(str(aaf), "rw") as f:
        mob = next(
            m for m in f.content.sourcemobs() if type(m.descriptor).__name__ == "WAVEDescriptor"
        )
        child = mob.descriptor
        multi = f.create.MultipleDescriptor()
        multi["SampleRate"].value = 48000
        multi["Length"].value = 96000
        mob.descriptor = multi
        multi["FileDescriptors"].append(child)
    result = create_turnover_package(aaf, output_dir=tmp_path / "package")
    with aaf2.open(str(result.session_path), "r") as f:
        multi = next(
            m.descriptor
            for m in f.content.sourcemobs()
            if type(m.descriptor).__name__ == "MultipleDescriptor"
        )
        url = multi["FileDescriptors"].value[0]["Locator"].value[0]["URLString"].value
        assert url == "file:media/sound.wav"
