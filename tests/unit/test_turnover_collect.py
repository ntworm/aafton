"""Testes dos defeitos do turnover medidos no plano aafton-turnover-20261115 (P1).

Cada teste expressa o comportamento esperado da nova arquitetura sobre o nucleo
do collect e falha no master d121c93, provando os defeitos apontados no plano.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import threading
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest

import aafton.web_app as web
from aafton.aaf_read import read_aaf
from aafton.als_read import read_session
from aafton.als_xml import load_als, save_als
from aafton.media_extract import resolve_asset
from aafton.media_oracle import verify_session_media
from aafton.report import Report
from aafton.sidecar import Anchor, sidecar_path_for, write_sidecar
from aafton.timecode import TimecodeRate
from aafton.turnover import TurnoverError, create_turnover_package
from tests.aaf_factory import wav_bytes


def _write_wav(path: Path, frames: int = 48000, tag: bytes = b"\x00\x00") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = bytearray(wav_bytes(frames, channels=1, sample_rate=48000))
    if len(raw) > 48:
        raw[44 : 44 + len(tag)] = tag
    path.write_bytes(bytes(raw))
    return path


def _sub(parent: ET.Element, path: str, **attrs: Any) -> ET.Element:
    node = parent
    for part in path.split("/"):
        child = node.find(part)
        node = child if child is not None else ET.SubElement(node, part)
    for k, v in attrs.items():
        node.set(k, str(v))
    return node


def _val(parent: ET.Element, path: str, value: Any) -> None:
    _sub(parent, path, Value=str(value))


def _set_file_ref(
    file_ref: ET.Element,
    *,
    path: str = "",
    rel_path: str = "",
    rel_type: str | None = "1",
    ref_type: str | None = None,
) -> None:
    if rel_type is not None:
        _val(file_ref, "RelativePathType", rel_type)
    _val(file_ref, "RelativePath", rel_path)
    _val(file_ref, "Path", path)
    if ref_type is not None:
        _val(file_ref, "Type", ref_type)
    _val(file_ref, "OriginalFileSize", "96044")
    _val(file_ref, "OriginalCrc", "12345")


def _add_audio_clip(
    events: ET.Element,
    clip_id: int,
    start: float,
    end: float,
    name: str,
    *,
    path: str = "",
    rel_path: str = "",
    rel_type: str | None = "1",
    ref_type: str | None = None,
    orig_refs: list[dict] | None = None,
) -> ET.Element:
    clip = ET.SubElement(events, "AudioClip", Id=str(clip_id), Time=str(start))
    _val(clip, "CurrentStart", start)
    _val(clip, "CurrentEnd", end)
    _val(clip, "Name", name)
    _val(clip, "Loop/LoopStart", 0.0)
    _val(clip, "Loop/LoopEnd", end - start)
    _val(clip, "Loop/LoopOn", "false")
    _val(clip, "Loop/StartRelative", 0)
    _val(clip, "IsWarped", "false")
    _val(clip, "WarpMode", "4")
    ET.SubElement(clip, "WarpMarkers")
    _val(clip, "PitchCoarse", 0)
    _val(clip, "PitchFine", 0)
    _val(clip, "Fades/FadeInLength", 0.0)
    _val(clip, "Fades/FadeOutLength", 0.0)
    _val(clip, "Fades/CrossfadeInState", 0)
    sample_ref = ET.SubElement(clip, "SampleRef")
    fref = ET.SubElement(sample_ref, "FileRef")
    _set_file_ref(fref, path=path, rel_path=rel_path, rel_type=rel_type, ref_type=ref_type)
    if orig_refs:
        ctx = ET.SubElement(sample_ref, "SourceContext")
        for o_idx, spec in enumerate(orig_refs):
            orig = ET.SubElement(ctx, "OriginalFileRef", Id=str(o_idx))
            ofref = ET.SubElement(orig, "FileRef")
            _set_file_ref(ofref, **spec)
    return clip


def _build_als_file(als_path: Path, track_clips: list[list[dict]]) -> Path:
    als_path.parent.mkdir(parents=True, exist_ok=True)
    root = ET.Element("Ableton", Creator="Ableton Live 12.4.6")
    live = ET.SubElement(root, "LiveSet")
    tracks = ET.SubElement(live, "Tracks")
    clip_seq = 0
    for t_idx, clips in enumerate(track_clips):
        track = ET.SubElement(tracks, "AudioTrack", Id=str(10 + t_idx))
        _val(track, "Name/EffectiveName", f"Audio {t_idx + 1}")
        events = _sub(track, "DeviceChain/MainSequencer/Sample/ArrangerAutomation/Events")
        for c in clips:
            _add_audio_clip(
                events,
                clip_seq,
                c.get("start", 0.0),
                c.get("end", 1.0),
                c.get("name", f"clip_{clip_seq}"),
                path=c.get("path", ""),
                rel_path=c.get("rel_path", ""),
                rel_type=c.get("rel_type", "1"),
                ref_type=c.get("ref_type"),
                orig_refs=c.get("orig_refs"),
            )
            clip_seq += 1
    main = ET.SubElement(live, "MainTrack")
    _val(main, "DeviceChain/Mixer/Tempo/Manual", 60.0)
    save_als(root, als_path)
    return als_path


@pytest.fixture
def local_server():
    server = web.run_studio_server(port=0, open_browser=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join()
    server.server_close()


def test_turnover_same_name_wavs_and_junk_wav(tmp_path: Path) -> None:
    folder_a = tmp_path / "folder_a"
    folder_b = tmp_path / "folder_b"
    set_dir = tmp_path / "project"

    wav_a = _write_wav(folder_a / "audio.wav", frames=48000, tag=b"AAA")
    wav_b = _write_wav(folder_b / "audio.wav", frames=48000, tag=b"BBB")
    junk_wav = set_dir / "._junk.wav"
    junk_wav.parent.mkdir(parents=True, exist_ok=True)
    junk_wav.write_bytes(b"\x00\x05\x16\x07AppleDoubleJunkDataNotRIFF")

    set_als = set_dir / "session.als"
    _build_als_file(
        set_als,
        [
            [{"start": 0.0, "end": 1.0, "name": "clip_a", "path": str(wav_a), "rel_type": "0"}],
            [{"start": 2.0, "end": 3.0, "name": "clip_b", "path": str(wav_b), "rel_type": "0"}],
        ],
    )
    anchor = Anchor(
        live_origin_samples=0,
        session_start_samples=172800000,
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1, False),
        bpm=60.0,
    )
    sidecar_orig = write_sidecar(set_als, anchor)

    monitored = [wav_a, wav_b, junk_wav, set_als, sidecar_orig]
    hashes_before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in monitored}

    orig_session = read_session(set_als, Report())
    pkg_dir = tmp_path / "package_turnover"

    result = create_turnover_package(set_als, output_dir=pkg_dir, strict=True)
    assert result.is_valid is True

    media_files = sorted((pkg_dir / "media").iterdir())
    assert len(media_files) == 2
    assert media_files[0].name != media_files[1].name
    assert not (pkg_dir / "._junk.wav").exists()
    assert not (pkg_dir / "media" / "._junk.wav").exists()

    manifest = json.loads((pkg_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["format_version"] == 2
    assert manifest["is_valid"] is True
    assert manifest["counts"]["copied"] == 2
    assert manifest["counts"]["missing"] == 0
    assert manifest["counts"]["corrupt"] == 0
    assert manifest["counts"]["hash_mismatch"] == 0

    for rel_path, meta in manifest["files"].items():
        disk_file = pkg_dir / rel_path
        assert disk_file.is_file()
        assert hashlib.sha256(disk_file.read_bytes()).hexdigest() == meta["sha256"]

    reread_session = read_session(pkg_dir / set_als.name, Report())
    assert len(reread_session.tracks) == len(orig_session.tracks)

    for track_idx in range(len(orig_session.tracks)):
        orig_clip = orig_session.tracks[track_idx].clips[0]
        reread_clip = reread_session.tracks[track_idx].clips[0]
        assert reread_clip.timeline_start_samples == orig_clip.timeline_start_samples
        assert (
            reread_clip.timeline_end_samples - reread_clip.timeline_start_samples
            == orig_clip.timeline_end_samples - orig_clip.timeline_start_samples
        )
        assert reread_clip.source_offset_samples == orig_clip.source_offset_samples

        reread_asset = next(m for m in reread_session.media if m.id == reread_clip.media_id)
        assert Path(reread_asset.resolved_path).is_relative_to(pkg_dir)
        actual_hash = hashlib.sha256(Path(reread_asset.resolved_path).read_bytes()).hexdigest()
        expected_hash = hashes_before[wav_a if track_idx == 0 else wav_b]
        assert actual_hash == expected_hash

    for p, h in hashes_before.items():
        assert hashlib.sha256(p.read_bytes()).hexdigest() == h


def test_turnover_original_file_ref_rewritten(tmp_path: Path) -> None:
    source_dir = tmp_path / "media_src"
    set_dir = tmp_path / "project"

    wav_file = _write_wav(source_dir / "sound.wav", frames=48000)
    set_als = set_dir / "session.als"
    _build_als_file(
        set_als,
        [
            [
                {
                    "start": 0.0,
                    "end": 1.0,
                    "name": "clip_orig",
                    "path": str(wav_file),
                    "rel_type": "0",
                    "orig_refs": [{"path": str(wav_file), "rel_type": "0"}],
                }
            ]
        ],
    )
    write_sidecar(
        set_als,
        Anchor(
            live_origin_samples=0,
            session_start_samples=172800000,
            sample_rate=48000,
            timecode_rate=TimecodeRate(25, 1, False),
            bpm=60.0,
        ),
    )

    pkg_dir = tmp_path / "package_turnover"
    create_turnover_package(set_als, output_dir=pkg_dir, strict=True)

    root = load_als(pkg_dir / set_als.name)
    clip = next(root.iter("AudioClip"))
    main_ref = clip.find("SampleRef/FileRef")
    orig_ref = clip.find("SampleRef/SourceContext/OriginalFileRef/FileRef")
    assert main_ref is not None and orig_ref is not None

    main_rel = main_ref.find("RelativePath").get("Value")
    orig_rel = orig_ref.find("RelativePath").get("Value")
    assert main_rel == orig_rel
    assert (pkg_dir / main_rel).is_file()
    assert str(wav_file) not in orig_ref.find("Path").get("Value")


def test_turnover_mp4_video_clip(tmp_path: Path) -> None:
    source_dir = tmp_path / "outside_media"
    set_dir = tmp_path / "project"

    wav_file = _write_wav(source_dir / "dialog.wav", frames=48000)
    video_file = source_dir / "feature.mp4"
    video_file.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x01" * 500)

    set_als = set_dir / "session.als"
    _build_als_file(
        set_als,
        [
            [{"start": 0.0, "end": 1.0, "name": "audio_clip", "path": str(wav_file)}],
            [
                {
                    "start": 0.0,
                    "end": 2.0,
                    "name": "video_clip",
                    "path": str(video_file),
                    "ref_type": "1",
                }
            ],
        ],
    )
    write_sidecar(
        set_als,
        Anchor(
            live_origin_samples=0,
            session_start_samples=172800000,
            sample_rate=48000,
            timecode_rate=TimecodeRate(25, 1, False),
            bpm=60.0,
        ),
    )

    pkg_dir = tmp_path / "package_turnover"
    result = create_turnover_package(set_als, output_dir=pkg_dir, strict=True)
    assert result.is_valid is True

    assert (pkg_dir / "media" / video_file.name).is_file()
    session = read_session(pkg_dir / set_als.name, Report())
    video_asset = next((m for m in session.media if m.filename == video_file.name), None)
    assert video_asset is not None
    assert Path(video_asset.resolved_path).is_relative_to(pkg_dir)


def test_turnover_linked_aaf_with_same_name_wavs(tmp_path: Path) -> None:
    import aaf2

    folder_1 = tmp_path / "folder_1"
    folder_2 = tmp_path / "folder_2"
    wav_1 = _write_wav(folder_1 / "fx.wav", frames=48000, tag=b"AAF1")
    wav_2 = _write_wav(folder_2 / "fx.wav", frames=48000, tag=b"AAF2")

    aaf_path = tmp_path / "linked.aaf"
    with aaf2.open(str(aaf_path), "w") as f:
        comp = f.create.CompositionMob("linked_turnover")
        f.content.mobs.append(comp)

        meta1 = {
            "format": {"filename": str(wav_1), "format_name": "wav"},
            "streams": [
                {
                    "codec_type": "audio",
                    "codec_name": "pcm_s16le",
                    "sample_rate": 48000,
                    "duration_ts": 48000 * 2,
                    "channels": 1,
                }
            ],
        }
        master1, _s1, _t1 = f.content.link_external_wav(meta1)

        meta2 = {
            "format": {"filename": str(wav_2), "format_name": "wav"},
            "streams": [
                {
                    "codec_type": "audio",
                    "codec_name": "pcm_s16le",
                    "sample_rate": 48000,
                    "duration_ts": 48000 * 2,
                    "channels": 1,
                }
            ],
        }
        master2, _s2, _t2 = f.content.link_external_wav(meta2)

        slot = comp.create_timeline_slot(edit_rate=48000)
        slot.name = "Audio 1"
        seq = f.create.Sequence(media_kind="sound")
        slot.segment = seq

        sc1 = f.create.SourceClip(media_kind="sound", start=0)
        sc1.length = 48000
        sc1.mob = master1
        sc1.slot_id = 1
        seq.components.append(sc1)

        sc2 = f.create.SourceClip(media_kind="sound", start=0)
        sc2.length = 48000
        sc2.mob = master2
        sc2.slot_id = 1
        seq.components.append(sc2)

        tc_slot = comp.create_timeline_slot(edit_rate=25)
        tc_slot.name = "TC"
        tc = f.create.Timecode(fps=25, drop=False)
        tc.start = 90000
        tc.length = 96000 * 25 // 48000
        tc_slot.segment = tc

    orig_hashes = {
        wav_1: hashlib.sha256(wav_1.read_bytes()).hexdigest(),
        wav_2: hashlib.sha256(wav_2.read_bytes()).hexdigest(),
        aaf_path: hashlib.sha256(aaf_path.read_bytes()).hexdigest(),
    }

    orig_session = read_aaf(aaf_path, Report())
    pkg_dir = tmp_path / "package_turnover"

    result = create_turnover_package(aaf_path, output_dir=pkg_dir, strict=True)
    assert result.is_valid is True

    with aaf2.open(str(pkg_dir / aaf_path.name), "r") as f:
        loc_urls = []
        for mob in f.content.sourcemobs():
            desc = getattr(mob, "descriptor", None)
            if desc and desc.get("Locator"):
                for loc in desc["Locator"].value:
                    loc_urls.append(loc["URLString"].value)
        assert len(loc_urls) == 2
        assert all(u.startswith("file:media/") for u in loc_urls)
        assert loc_urls[0] != loc_urls[1]

    reread_session = read_aaf(pkg_dir / aaf_path.name, Report())
    for asset in reread_session.media:
        resolve_asset(asset, [pkg_dir], Report())
        assert asset.status == "resolved"
        assert Path(asset.resolved_path).is_relative_to(pkg_dir)

    for i in range(2):
        orig_clip = orig_session.tracks[0].clips[i]
        reread_clip = reread_session.tracks[0].clips[i]
        assert reread_clip.timeline_start_samples == orig_clip.timeline_start_samples
        assert (
            reread_clip.timeline_end_samples - reread_clip.timeline_start_samples
            == orig_clip.timeline_end_samples - orig_clip.timeline_start_samples
        )
        assert reread_clip.source_offset_samples == orig_clip.source_offset_samples

    manifest = json.loads((pkg_dir / "manifest.json").read_text(encoding="utf-8"))
    for rel_path, meta in manifest["files"].items():
        disk_file = pkg_dir / rel_path
        assert disk_file.is_file()
        assert hashlib.sha256(disk_file.read_bytes()).hexdigest() == meta["sha256"]

    for p, h in orig_hashes.items():
        assert hashlib.sha256(p.read_bytes()).hexdigest() == h


def test_turnover_strict_and_permissive_missing_media(tmp_path: Path) -> None:
    set_dir = tmp_path / "project"
    missing_file = tmp_path / "outside_missing.wav"

    set_als = set_dir / "session.als"
    _build_als_file(
        set_als,
        [[{"start": 0.0, "end": 1.0, "name": "missing_clip", "path": str(missing_file)}]],
    )
    write_sidecar(
        set_als,
        Anchor(
            live_origin_samples=0,
            session_start_samples=172800000,
            sample_rate=48000,
            timecode_rate=TimecodeRate(25, 1, False),
            bpm=60.0,
        ),
    )

    pkg_strict = tmp_path / "pkg_strict"
    with pytest.raises(TurnoverError):
        create_turnover_package(set_als, output_dir=pkg_strict, strict=True)
    assert not pkg_strict.exists()

    pkg_perm = tmp_path / "pkg_permissive"
    result = create_turnover_package(set_als, output_dir=pkg_perm, strict=False)
    assert result.is_valid is False

    manifest = json.loads((pkg_perm / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["format_version"] == 2
    assert manifest["is_valid"] is False
    assert manifest["counts"]["missing"] >= 1
    missing_entries = [m for m in manifest["media"] if m["state"] == "missing"]
    assert len(missing_entries) >= 1


def test_turnover_copied_package_with_originals_on_disk(tmp_path: Path) -> None:
    source_dir = tmp_path / "outside_audio"
    set_dir = tmp_path / "project"

    wav_file = _write_wav(source_dir / "take1.wav", frames=48000)
    set_als = set_dir / "session.als"
    _build_als_file(
        set_als,
        [[{"start": 0.0, "end": 1.0, "name": "clip_take", "path": str(wav_file)}]],
    )
    write_sidecar(
        set_als,
        Anchor(
            live_origin_samples=0,
            session_start_samples=172800000,
            sample_rate=48000,
            timecode_rate=TimecodeRate(25, 1, False),
            bpm=60.0,
        ),
    )

    pkg_dir = tmp_path / "pkg_turnover"
    result = create_turnover_package(set_als, output_dir=pkg_dir, strict=True)
    assert result.is_valid is True

    copied_dir = tmp_path / "copied_turnover_package"
    shutil.copytree(pkg_dir, copied_dir)
    assert wav_file.is_file()

    session = read_session(copied_dir / set_als.name, Report())
    assert session.media
    for asset in session.media:
        assert asset.status == "resolved"
        assert Path(asset.resolved_path).is_relative_to(copied_dir)


def test_turnover_corrupt_wav_strict_and_permissive(tmp_path: Path) -> None:
    source_dir = tmp_path / "outside_audio"
    set_dir = tmp_path / "project"

    corrupt_wav = source_dir / "corrupt.wav"
    corrupt_wav.parent.mkdir(parents=True, exist_ok=True)
    corrupt_wav.write_bytes(b"RIFF\x20\x00\x00\x00NOTWAVEBadHeaderPaddingData")

    set_als = set_dir / "session.als"
    _build_als_file(
        set_als,
        [[{"start": 0.0, "end": 1.0, "name": "corrupt_clip", "path": str(corrupt_wav)}]],
    )
    write_sidecar(
        set_als,
        Anchor(
            live_origin_samples=0,
            session_start_samples=172800000,
            sample_rate=48000,
            timecode_rate=TimecodeRate(25, 1, False),
            bpm=60.0,
        ),
    )

    pkg_strict = tmp_path / "pkg_strict"
    with pytest.raises(TurnoverError):
        create_turnover_package(set_als, output_dir=pkg_strict, strict=True)
    assert not pkg_strict.exists()

    pkg_perm = tmp_path / "pkg_perm"
    result = create_turnover_package(set_als, output_dir=pkg_perm, strict=False)
    assert result.is_valid is False

    manifest = json.loads((pkg_perm / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["format_version"] == 2
    assert manifest["is_valid"] is False
    assert manifest["counts"]["corrupt"] >= 1
    corrupt_entries = [m for m in manifest["media"] if m["state"] == "corrupt"]
    assert len(corrupt_entries) >= 1


def test_turnover_absolute_key_hash_mismatch_and_unlisted_audio(tmp_path: Path) -> None:
    source_dir = tmp_path / "outside_audio"
    set_dir = tmp_path / "project"

    wav_1 = _write_wav(source_dir / "mismatch.wav", frames=48000, tag=b"W1")
    wav_2 = _write_wav(source_dir / "unlisted.wav", frames=48000, tag=b"W2")

    set_als = set_dir / "session.als"
    _build_als_file(
        set_als,
        [
            [{"start": 0.0, "end": 1.0, "name": "clip_1", "path": str(wav_1)}],
            [{"start": 2.0, "end": 3.0, "name": "clip_2", "path": str(wav_2)}],
        ],
    )
    anchor = Anchor(
        live_origin_samples=0,
        session_start_samples=172800000,
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1, False),
        bpm=60.0,
        media_hashes={str(wav_1.resolve()): "f" * 64},
    )
    write_sidecar(set_als, anchor)

    pkg_dir = tmp_path / "pkg_turnover"
    result = create_turnover_package(set_als, output_dir=pkg_dir, strict=False)
    assert result.is_valid is False

    assert (pkg_dir / "media" / wav_1.name).is_file()
    assert (pkg_dir / "media" / wav_2.name).is_file()

    manifest = json.loads((pkg_dir / "manifest.json").read_text(encoding="utf-8"))
    mismatch_entries = [m for m in manifest["media"] if m["state"] == "hash_mismatch"]
    assert len(mismatch_entries) == 1
    assert Path(mismatch_entries[0]["source"]).name == wav_1.name

    pkg_sidecar = json.loads(result.sidecar_path.read_text(encoding="utf-8"))
    assert pkg_sidecar["media_hashes"][wav_1.name] == "f" * 64

    audit = verify_session_media(pkg_dir / "media", result.sidecar_path)
    assert audit.mismatch_count >= 1
    assert not audit.is_valid


def test_turnover_absolute_video_path_in_sidecar(tmp_path: Path) -> None:
    source_dir = tmp_path / "outside_media"
    set_dir = tmp_path / "project"

    wav_file = _write_wav(source_dir / "dialog.wav", frames=48000)
    video_file = source_dir / "picture.mp4"
    video_file.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 300)

    set_als = set_dir / "session.als"
    _build_als_file(
        set_als,
        [
            [{"start": 0.0, "end": 1.0, "name": "dialog_clip", "path": str(wav_file)}],
            [
                {
                    "start": 0.0,
                    "end": 2.0,
                    "name": "pic_clip",
                    "path": str(video_file),
                    "ref_type": "1",
                }
            ],
        ],
    )
    anchor = Anchor(
        live_origin_samples=0,
        session_start_samples=172800000,
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1, False),
        bpm=60.0,
        video_path=str(video_file.resolve()),
    )
    write_sidecar(set_als, anchor)

    pkg_dir = tmp_path / "pkg_turnover"
    result = create_turnover_package(set_als, output_dir=pkg_dir, strict=True)
    assert result.is_valid is True

    sidecar_data = json.loads(result.sidecar_path.read_text(encoding="utf-8"))
    assert sidecar_data["video_path"] == f"media/{video_file.name}"
    assert video_file.name not in sidecar_data["media_hashes"]

    moved_dir = tmp_path / "moved_pkg"
    pkg_dir.rename(moved_dir)
    assert (moved_dir / "media" / video_file.name).is_file()

    session = read_session(moved_dir / set_als.name, Report())
    assert any(t.is_video for t in session.tracks)


def test_turnover_web_api_rejects_media_dir(local_server: str, tmp_path: Path) -> None:
    media_dir = tmp_path / "explicit_media_dir"
    wav_file = _write_wav(media_dir / "audio.wav")
    set_als = tmp_path / "src" / "session.als"
    _build_als_file(
        set_als,
        [[{"start": 0.0, "end": 1.0, "name": "clip", "path": str(wav_file)}]],
    )
    h = hashlib.sha256(wav_file.read_bytes()).hexdigest()
    write_sidecar(
        set_als,
        Anchor(
            live_origin_samples=0,
            session_start_samples=172800000,
            sample_rate=48000,
            timecode_rate=TimecodeRate(25, 1, False),
            bpm=60.0,
            media_hashes={"audio.wav": h},
        ),
    )

    request = urllib.request.Request(
        local_server + "/api/turnover/generate",
        data=json.dumps(
            {
                "session": str(set_als),
                "media_dir": str(media_dir),
                "output": str(tmp_path / "web_pkg"),
            }
        ).encode(),
        headers={"Content-Type": "application/json"},
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(request)
    assert exc.value.code == 400


def test_turnover_invalid_aaf_and_invalid_sidecar_raise_turnover_error(tmp_path: Path) -> None:
    bad_aaf = tmp_path / "corrupt.aaf"
    bad_aaf.write_bytes(b"invalid AAF binary data")
    out_aaf = tmp_path / "pkg_bad_aaf"
    with pytest.raises(TurnoverError):
        create_turnover_package(bad_aaf, output_dir=out_aaf)
    assert not out_aaf.exists()

    set_als = tmp_path / "valid.als"
    wav_file = _write_wav(tmp_path / "sound.wav")
    _build_als_file(
        set_als,
        [[{"start": 0.0, "end": 1.0, "name": "clip", "path": str(wav_file)}]],
    )
    bad_sidecar = sidecar_path_for(set_als)
    bad_sidecar.write_bytes(b"{corrupt json syntax")
    out_als = tmp_path / "pkg_bad_sidecar"
    with pytest.raises(TurnoverError):
        create_turnover_package(set_als, output_dir=out_als)
    assert not out_als.exists()


def test_turnover_library_only_set_with_same_name_wav_beside_it(tmp_path: Path) -> None:
    set_dir = tmp_path / "project"
    beside_wav = _write_wav(set_dir / "sample.wav", frames=48000)

    set_als = set_dir / "lib_only.als"
    _build_als_file(
        set_als,
        [
            [
                {
                    "start": 0.0,
                    "end": 1.0,
                    "name": "lib_clip",
                    "path": "/Library/Audio/sample.wav",
                    "rel_path": "sample.wav",
                    "rel_type": "5",
                }
            ]
        ],
    )
    write_sidecar(
        set_als,
        Anchor(
            live_origin_samples=0,
            session_start_samples=172800000,
            sample_rate=48000,
            timecode_rate=TimecodeRate(25, 1, False),
            bpm=60.0,
        ),
    )

    pkg_strict = tmp_path / "pkg_strict"
    with pytest.raises(TurnoverError):
        create_turnover_package(set_als, output_dir=pkg_strict, strict=True)
    assert not pkg_strict.exists()

    pkg_perm = tmp_path / "pkg_perm"
    result = create_turnover_package(set_als, output_dir=pkg_perm, strict=False)
    assert result.is_valid is False
    manifest = json.loads((pkg_perm / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["counts"]["library"] == 1
    assert manifest["counts"]["copied"] == 0
    assert not (pkg_perm / "media" / beside_wav.name).exists()


def test_turnover_ambiguous_name_key_discarded(tmp_path: Path) -> None:
    folder_a = tmp_path / "dir_a"
    folder_b = tmp_path / "dir_b"
    set_dir = tmp_path / "project"

    wav_a = _write_wav(folder_a / "ambig.wav", frames=48000, tag=b"TAG1")
    wav_b = _write_wav(folder_b / "ambig.wav", frames=48000, tag=b"TAG2")

    set_als = set_dir / "session.als"
    _build_als_file(
        set_als,
        [
            [{"start": 0.0, "end": 1.0, "name": "clip_a", "path": str(wav_a), "rel_type": "0"}],
            [{"start": 2.0, "end": 3.0, "name": "clip_b", "path": str(wav_b), "rel_type": "0"}],
        ],
    )
    anchor = Anchor(
        live_origin_samples=0,
        session_start_samples=172800000,
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1, False),
        bpm=60.0,
        media_hashes={"ambig.wav": "f" * 64},
    )
    write_sidecar(set_als, anchor)

    pkg_dir = tmp_path / "pkg_turnover"
    result = create_turnover_package(set_als, output_dir=pkg_dir, strict=True)
    assert result.is_valid is True

    manifest = json.loads((pkg_dir / "manifest.json").read_text(encoding="utf-8"))
    for item in manifest["media"]:
        assert item["expected_sha256"] is None

    pkg_sidecar = json.loads(result.sidecar_path.read_text(encoding="utf-8"))
    copied_names = {p.name for p in (pkg_dir / "media").iterdir()}
    assert len(copied_names) == 2
    assert set(pkg_sidecar["media_hashes"]) == copied_names
    for name, h in pkg_sidecar["media_hashes"].items():
        assert h == hashlib.sha256((pkg_dir / "media" / name).read_bytes()).hexdigest()

    audit = verify_session_media(pkg_dir / "media", result.sidecar_path)
    assert audit.is_valid is True

    report_text = result.report_path.read_text(encoding="utf-8")
    assert "ambig.wav" in report_text


def test_turnover_moved_package_with_relative_video_path_marks_is_video(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pkg_dir = tmp_path / "original_package"
    media_dir = pkg_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)

    wav_file = _write_wav(media_dir / "dialog.wav", frames=48000)
    video_file = media_dir / "video.mp4"
    video_file.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 200)

    set_als = pkg_dir / "session.als"
    _build_als_file(
        set_als,
        [
            [{"start": 0.0, "end": 1.0, "name": "dialog_clip", "path": str(wav_file)}],
            [
                {
                    "start": 0.0,
                    "end": 2.0,
                    "name": "video_clip",
                    "path": str(video_file),
                    "rel_path": "media/video.mp4",
                    "rel_type": "1",
                    "ref_type": "1",
                }
            ],
        ],
    )
    anchor = Anchor(
        live_origin_samples=0,
        session_start_samples=172800000,
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1, False),
        bpm=60.0,
        video_path="media/video.mp4",
    )
    write_sidecar(set_als, anchor)

    moved_dir = tmp_path / "somewhere_else" / "package"
    moved_dir.parent.mkdir(parents=True, exist_ok=True)
    pkg_dir.rename(moved_dir)

    other_cwd = tmp_path / "other_cwd"
    other_cwd.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(other_cwd)

    session = read_session(moved_dir / set_als.name, Report())
    video_track = next((t for t in session.tracks if t.name == "Audio 2"), None)
    assert video_track is not None
    assert video_track.is_video is True
