"""Testes unitarios do collect para sets do Ableton Live (.als) e sidecar."""

from __future__ import annotations

import hashlib
import os
import xml.etree.ElementTree as ET
from pathlib import Path

from aafton.als_ancora import start_padrao
from aafton.als_read import read_session
from aafton.als_xml import get_value, load_als, save_als
from aafton.collect import plan_collect, run_collect
from aafton.report import Report
from aafton.sidecar import Anchor, read_anchor, sidecar_path_for, write_sidecar
from aafton.timecode import TimecodeRate
from aafton.collect_als import is_video_file_ref, resolve_file_ref
from tests.aaf_factory import wav_bytes


def _write_wav(path: Path, frames: int = 48000, tag: bytes = b"\x00\x00") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = bytearray(wav_bytes(frames, channels=1, sample_rate=48000))
    if len(raw) > 48:
        raw[44 : 44 + len(tag)] = tag
    path.write_bytes(bytes(raw))
    return path


def _sub(parent: ET.Element, path: str, **attrs) -> ET.Element:
    node = parent
    parts = path.split("/")
    for part in parts[:-1]:
        child = node.find(part)
        node = child if child is not None else ET.SubElement(node, part)
    return ET.SubElement(node, parts[-1], **attrs)


def _val(parent: ET.Element, path: str, value) -> None:
    _sub(parent, path, Value=str(value))


def _set_file_ref(
    file_ref: ET.Element,
    *,
    path: str = "",
    rel_path: str = "",
    rel_type: str | None = "1",
    ref_type: str | None = None,
    crc: str = "12345",
    size: str = "96044",
) -> None:
    if rel_type is not None:
        _val(file_ref, "RelativePathType", rel_type)
    _val(file_ref, "RelativePath", rel_path)
    _val(file_ref, "Path", path)
    if ref_type is not None:
        _val(file_ref, "Type", ref_type)
    _val(file_ref, "OriginalFileSize", size)
    _val(file_ref, "OriginalCrc", crc)


def _add_audio_clip(
    events: ET.Element,
    idx: int,
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
    clip = ET.SubElement(events, "AudioClip", Id=str(idx), Time=str(start))
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


def _build_synthetic_als_tree(tmp_path: Path) -> dict:
    """Monta a arvore de pastas e o set sintetico completo exigido no aceite 1."""
    proj_root = tmp_path / "My Live Project"
    (proj_root / "Ableton Project Info").mkdir(parents=True)
    als_dir = proj_root / "Subsets" / "Edit"
    als_dir.mkdir(parents=True)

    folder_a = tmp_path / "media_a"
    folder_b = tmp_path / "media_b"
    proj_samples = proj_root / "Samples" / "Imported"

    wav_a_same = _write_wav(folder_a / "collide.wav", frames=48000, tag=b"\x01\x02")
    wav_b_same = _write_wav(folder_b / "collide.wav", frames=48000, tag=b"\x03\x04")
    wav_rel_only = _write_wav(als_dir / "local_rel.wav", frames=48000, tag=b"\x05\x06")
    wav_type3 = _write_wav(proj_samples / "from_project.wav", frames=48000, tag=b"\x07\x08")
    wav_simpler = _write_wav(folder_b / "simpler_hit.wav", frames=48000, tag=b"\x09\x0a")
    wav_old_hist = _write_wav(folder_a / "before_consolidate.wav", frames=48000, tag=b"\x0b\x0c")
    video_file = folder_a / "picture.mp4"
    video_file.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\xaa" * 200)
    missing_file = folder_b / "offline_missing.wav"

    root = ET.Element("Ableton", Creator="Ableton Live 12.4.6")
    live = ET.SubElement(root, "LiveSet")
    tracks = ET.SubElement(live, "Tracks")

    track_dx = ET.SubElement(tracks, "AudioTrack", Id="10")
    _val(track_dx, "Name/EffectiveName", "DX")
    events_dx = _sub(track_dx, "DeviceChain/MainSequencer/Sample/ArrangerAutomation/Events")

    # 1. File in folder A + OriginalFileRef pointing to the same file
    _add_audio_clip(
        events_dx,
        0,
        0.0,
        1.0,
        "collide_a",
        path=str(wav_a_same),
        rel_type="0",
        orig_refs=[{"path": str(wav_a_same), "rel_type": "0"}],
    )
    # 2. Same-name file in folder B + OriginalFileRef pointing to another file (history_kept)
    _add_audio_clip(
        events_dx,
        1,
        2.0,
        3.0,
        "collide_b",
        path=str(wav_b_same),
        rel_type="0",
        orig_refs=[{"path": str(wav_old_hist), "rel_type": "0"}],
    )
    # 3. FileRef with nonexistent Mac absolute Path and valid RelativePath
    _add_audio_clip(
        events_dx,
        2,
        4.0,
        5.0,
        "mac_moved",
        path="/Users/editor/Nonexistent/local_rel.wav",
        rel_path="local_rel.wav",
        rel_type="1",
    )
    # 4. RelativePathType 3 resolved against project ancestor ('Ableton Project Info')
    _add_audio_clip(
        events_dx,
        3,
        6.0,
        7.0,
        "proj_rel",
        path="/nonexistent/from_project.wav",
        rel_path="Samples/Imported/from_project.wav",
        rel_type="3",
    )

    # Track 2: Video clip (Type 1 and .mp4) + Missing clip + Core Library clip (Type 5)
    track_fx = ET.SubElement(tracks, "AudioTrack", Id="11")
    _val(track_fx, "Name/EffectiveName", "FX")
    events_fx = _sub(track_fx, "DeviceChain/MainSequencer/Sample/ArrangerAutomation/Events")
    _add_audio_clip(
        events_fx,
        0,
        0.0,
        2.0,
        "video_clip",
        path=str(video_file),
        rel_type="0",
        ref_type="1",
    )
    _add_audio_clip(
        events_fx,
        1,
        2.0,
        3.0,
        "missing_clip",
        path=str(missing_file),
        rel_type="0",
    )
    _add_audio_clip(
        events_fx,
        2,
        4.0,
        5.0,
        "core_lib",
        path="/Applications/Ableton/Core Library/Samples/kick.wav",
        rel_path="Samples/kick.wav",
        rel_type="5",
    )

    # Track 3: MIDI track with Simpler MultiSamplePart/SampleRef/FileRef + PresetRef
    track_midi = ET.SubElement(tracks, "MidiTrack", Id="20")
    _val(track_midi, "Name/EffectiveName", "Simpler")
    simpler_path = (
        "DeviceChain/DeviceChain/Devices/OriginalSimpler/"
        "Player/MultiSampleMap/SampleParts/MultiSamplePart"
    )
    part = _sub(track_midi, simpler_path)
    simpler_sr = ET.SubElement(part, "SampleRef")
    simpler_fref = ET.SubElement(simpler_sr, "FileRef")
    _set_file_ref(simpler_fref, path=str(wav_simpler), rel_type="0")

    preset_fref = _sub(track_midi, "DeviceChain/PresetRef/FileRef")
    _set_file_ref(preset_fref, path="/Presets/MyPreset.adv", rel_path="MyPreset.adv", rel_type="1")

    main = ET.SubElement(live, "MainTrack")
    _val(main, "DeviceChain/Mixer/Tempo/Manual", 60.0)

    als_path = als_dir / "session.als"
    save_als(root, als_path)
    return {
        "als_path": als_path,
        "wav_a_same": wav_a_same,
        "wav_b_same": wav_b_same,
        "wav_rel_only": wav_rel_only,
        "wav_type3": wav_type3,
        "wav_simpler": wav_simpler,
        "wav_old_hist": wav_old_hist,
        "video_file": video_file,
        "missing_file": missing_file,
    }


def _snapshot(paths: list[Path]) -> dict[Path, tuple[bytes, int]]:
    snap: dict[Path, tuple[bytes, int]] = {}
    for idx, p in enumerate(paths):
        if p.is_file():
            mtime_ns = 1_700_000_000_000_000_000 + idx * 1_000_000_000
            os.utime(p, ns=(mtime_ns, mtime_ns))
            snap[p] = (p.read_bytes(), p.stat().st_mtime_ns)
    return snap


def test_als_collect_full_synthetic_matrix(tmp_path):
    """Cobre todos os requisitos do item 1 de aceite em um set sintetico."""
    fx = _build_synthetic_als_tree(tmp_path)
    als_path: Path = fx["als_path"]

    # Criar um sidecar com metadados completos para testar preservacao de original
    anchor = Anchor(
        live_origin_samples=0,
        session_start_samples=172800000,
        sample_rate=48000,
        timecode_rate=TimecodeRate(24, 1, False),
        bpm=60.0,
        source_path="C:/orig/input.aaf",
        video_path=str(fx["video_file"]),
        media_hashes={
            str(fx["wav_a_same"]): hashlib.sha256(fx["wav_a_same"].read_bytes()).hexdigest(),
            "collide.wav": hashlib.sha256(fx["wav_a_same"].read_bytes()).hexdigest(),
        },
    )
    sidecar_orig = write_sidecar(als_path, anchor)

    monitored = [
        als_path,
        sidecar_orig,
        fx["wav_a_same"],
        fx["wav_b_same"],
        fx["wav_rel_only"],
        fx["wav_type3"],
        fx["wav_simpler"],
        fx["wav_old_hist"],
        fx["video_file"],
    ]
    before = _snapshot(monitored)

    # Plan check
    plan = plan_collect(als_path)
    assert plan.history_kept == 1
    assert sum(1 for i in plan.items if i.kind == "video") == 1

    # Run collect with video included
    dest_dir = tmp_path / "collected_out"
    res = run_collect(plan, dest_dir)
    assert res.exit_code == 3  # 1 missing file
    assert res.report.counts["copied"] == 6
    assert res.report.counts["missing"] == 1
    assert res.report.counts["library"] == 1
    assert res.report.counts["video_skipped"] == 0
    assert res.report.counts["history_kept"] == 1

    # Verificar que todos os 6 arquivos copiados existem em 'session Media/' com sha256 identico
    media_dir = dest_dir / "session Media"
    assert media_dir.is_dir()
    expected_sources = [
        fx["wav_a_same"],
        fx["wav_b_same"],
        fx["wav_rel_only"],
        fx["wav_type3"],
        fx["wav_simpler"],
        fx["video_file"],
    ]
    src_hashes = {hashlib.sha256(p.read_bytes()).hexdigest() for p in expected_sources}
    copied_files = list(media_dir.iterdir())
    assert len(copied_files) == 6
    dest_hashes = {hashlib.sha256(p.read_bytes()).hexdigest() for p in copied_files}
    assert dest_hashes == src_hashes
    assert not (media_dir / "before_consolidate.wav").exists()

    # Verificar os FileRefs reescritos no XML coletado
    collected_root = load_als(res.session_path)
    rewritten_count = 0
    for sr in collected_root.iter("SampleRef"):
        fref = sr.find("FileRef")
        assert fref is not None
        rtype = get_value(fref, "RelativePathType")
        rpath = get_value(fref, "RelativePath") or ""
        apath = get_value(fref, "Path") or ""
        if rtype == "5":
            assert rpath == "Samples/kick.wav"
            continue
        if "offline_missing.wav" in apath:
            continue
        rewritten_count += 1
        assert rtype == "1"
        assert rpath.startswith("session Media/")
        target_f = dest_dir / rpath
        assert target_f.is_file()
        assert apath == target_f.resolve().as_posix()
        assert get_value(fref, "OriginalCrc") == "12345"
        assert get_value(fref, "OriginalFileSize") == "96044"
    assert rewritten_count == 6

    # Verificar OriginalFileRef: wav_a_same reescrito; wav_old_hist intacto
    orig_paths = []
    for sr in collected_root.iter("SampleRef"):
        ctx = sr.find("SourceContext")
        if ctx is not None:
            for orig in ctx.iter("OriginalFileRef"):
                ofref = orig.find("FileRef")
                orig_paths.append(
                    (
                        get_value(ofref, "RelativePathType"),
                        get_value(ofref, "RelativePath"),
                        get_value(ofref, "Path"),
                    )
                )
    assert len(orig_paths) == 2
    assert orig_paths[0][0] == "1" and orig_paths[0][1].startswith("session Media/collide")
    assert orig_paths[1] == ("0", "", str(fx["wav_old_hist"]))

    # PresetRef nao foi tocado
    preset_node = collected_root.find(".//PresetRef/FileRef")
    assert get_value(preset_node, "Path") == "/Presets/MyPreset.adv"

    # Reler com read_session no modo permissivo
    orig_ir = read_session(als_path, Report(strict=False), start=start_padrao())
    coll_ir = read_session(res.session_path, Report(strict=False), start=start_padrao())
    orig_clips = [
        (t.name, c.name, c.timeline_start_samples, c.timeline_end_samples, c.source_offset_samples)
        for t in orig_ir.tracks
        for c in t.clips
    ]
    coll_clips = [
        (t.name, c.name, c.timeline_start_samples, c.timeline_end_samples, c.source_offset_samples)
        for t in coll_ir.tracks
        for c in t.clips
    ]
    assert coll_clips == orig_clips

    # Run collect com include_video=False: conta video_skipped
    dest_no_vid = tmp_path / "collected_no_video"
    res_nv = run_collect(plan, dest_no_vid, include_video=False)
    assert res_nv.report.counts["video_skipped"] == 1
    assert res_nv.report.counts["copied"] == 5
    assert not any(p.suffix == ".mp4" for p in (dest_no_vid / "session Media").iterdir())

    # Prova de que o set original, sidecar e midias continuam com mesmos bytes e mtime
    for p, (raw_bytes, mtime_ns) in before.items():
        assert p.read_bytes() == raw_bytes
        assert p.stat().st_mtime_ns == mtime_ns


def test_sidecar_rewrite_and_video_deduplication(tmp_path):
    """Cobre o item 4 do aceite e os dois achados BAIXO da revisao fiscal do plano."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    wav1 = _write_wav(src_dir / "dx.wav", frames=48000, tag=b"\x11\x22")
    vid = src_dir / "guide.mp4"
    vid.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\xbb" * 128)

    root = ET.Element("Ableton", Creator="Ableton Live 12.4.6")
    live = ET.SubElement(root, "LiveSet")
    tracks = ET.SubElement(live, "Tracks")
    t0 = ET.SubElement(tracks, "AudioTrack", Id="10")
    _val(t0, "Name/EffectiveName", "VIDEO")
    ev0 = _sub(t0, "DeviceChain/MainSequencer/Sample/ArrangerAutomation/Events")
    _add_audio_clip(ev0, 0, 0.0, 2.0, "guide", path=str(vid), rel_type="0", ref_type="1")

    t1 = ET.SubElement(tracks, "AudioTrack", Id="11")
    _val(t1, "Name/EffectiveName", "DX")
    ev1 = _sub(t1, "DeviceChain/MainSequencer/Sample/ArrangerAutomation/Events")
    _add_audio_clip(ev1, 0, 0.0, 2.0, "dx", path=str(wav1), rel_type="0")

    main = ET.SubElement(live, "MainTrack")
    _val(main, "DeviceChain/Mixer/Tempo/Manual", 60.0)
    als_path = src_dir / "filme.als"
    save_als(root, als_path)

    wav1_sha = hashlib.sha256(wav1.read_bytes()).hexdigest()
    # Chave absoluta com caixa diferente no Windows para testar casamento por caminho resolvido
    abs_key_variant = str(wav1.resolve()).upper() if os.name == "nt" else str(wav1.resolve())
    orig_anchor = Anchor(
        live_origin_samples=48000,
        session_start_samples=172800000,
        sample_rate=48000,
        timecode_rate=TimecodeRate(24, 1, False),
        bpm=60.0,
        source_path="D:/orig_editor/filme.aaf",
        fades="absorb",
        stereo="pair",
        video_path=str(vid.resolve()),
        video_anchor_source="VIDEO_ANCHOR_FROM_AAF",
        track_channels={"VIDEO": 2, "DX": 1},
        audio_source_indices=[0],
        clip_ranges=[{"track": "DX", "ranges": [[172800000, 172896000]]}],
        track_bindings=[
            {
                "index": 0,
                "live_track_id": "11",
                "track_name": "DX",
                "origin_slot_id": 1,
                "mob_id": "urn:smpte:umid:0102",
                "channel_count": 1,
            }
        ],
        media_hashes={
            abs_key_variant: wav1_sha,
            "legacy_name.wav": "a" * 64,
        },
        origin_slots=[{"slot_id": 1, "name": "DX"}],
        expanded={
            "folder": "D:/ rushes/day1",
            "editor": "D:/orig_editor/filme.aaf",
            "editor_sha256": "b" * 64,
            "tracks": {"DX_2": {"mark": "expanded", "owner": "DX", "clips": []}},
        },
    )
    write_sidecar(als_path, orig_anchor)

    # Achado 2: video consta no SampleRef/FileRef e em anchor.video_path -> listado 1 vez
    plan = plan_collect(als_path)
    video_items = [it for it in plan.items if it.kind == "video"]
    assert len(video_items) == 1
    assert len(plan.items) == 2

    dest_dir = tmp_path / "out_filme"
    res = run_collect(plan, dest_dir)
    assert res.sidecar_path == sidecar_path_for(res.session_path)

    # Achado 1 + Aceite 4: read_anchor preserva todos os campos exceto video_path e media_hashes
    new_anchor = read_anchor(res.session_path)
    assert new_anchor.live_origin_samples == orig_anchor.live_origin_samples
    assert new_anchor.session_start_samples == orig_anchor.session_start_samples
    assert new_anchor.sample_rate == orig_anchor.sample_rate
    assert new_anchor.timecode_rate == orig_anchor.timecode_rate
    assert new_anchor.bpm == orig_anchor.bpm
    assert new_anchor.source_path == orig_anchor.source_path
    assert new_anchor.track_channels == orig_anchor.track_channels
    assert new_anchor.audio_source_indices == orig_anchor.audio_source_indices
    assert new_anchor.clip_ranges == orig_anchor.clip_ranges
    assert new_anchor.track_bindings == orig_anchor.track_bindings
    assert new_anchor.origin_slots == orig_anchor.origin_slots
    assert new_anchor.expanded == orig_anchor.expanded

    copied_vid = dest_dir / "filme Media" / "guide.mp4"
    copied_wav = dest_dir / "filme Media" / "dx.wav"
    assert new_anchor.video_path == str(copied_vid.resolve())
    assert abs_key_variant not in new_anchor.media_hashes
    assert new_anchor.media_hashes[str(copied_wav.resolve())] == wav1_sha
    assert new_anchor.media_hashes["dx.wav"] == wav1_sha
    assert new_anchor.media_hashes["guide.mp4"] == hashlib.sha256(vid.read_bytes()).hexdigest()
    assert new_anchor.media_hashes["legacy_name.wav"] == "a" * 64


def _file_ref_solto(path: str, rel_path: str, rel_type: str, ref_type: str) -> ET.Element:
    file_ref = ET.Element("FileRef")
    for tag, valor in (
        ("RelativePathType", rel_type),
        ("RelativePath", rel_path),
        ("Path", path),
        ("Type", ref_type),
    ):
        ET.SubElement(file_ref, tag, Value=valor)
    return file_ref


def test_tipo_5_com_caminho_absoluto_do_aafton_e_midia_do_usuario(tmp_path):
    # O template do aafton grava RelativePathType 5 e o caminho absoluto nos
    # dois campos (medido num set de AAF -> Live em 15/11/2026).
    wav = tmp_path / "origem" / "dialogo.wav"
    wav.parent.mkdir()
    wav.write_bytes(b"RIFF")
    als = tmp_path / "set" / "cena.als"
    absoluto = wav.as_posix()
    estado, resolvido, _ = resolve_file_ref(_file_ref_solto(absoluto, absoluto, "5", "1"), als)
    assert estado == "resolved"
    assert resolvido == wav.resolve()


def test_tipo_5_com_caminho_relativo_e_da_biblioteca(tmp_path):
    ref = _file_ref_solto(
        "C:/ProgramData/Ableton/Live 12/Resources/Core Library/Samples/kick.wav",
        "Samples/kick.wav",
        "5",
        "1",
    )
    estado, resolvido, _ = resolve_file_ref(ref, tmp_path / "x.als")
    assert (estado, resolvido) == ("library", None)


def test_type_do_file_ref_nao_marca_video():
    # Live 12.4.15b1 grava Type 1 em todo arquivo; Live 12.4.6 grava 2 no .mp4.
    assert not is_video_file_ref(_file_ref_solto("a.wav", "a.wav", "3", "1"), "a.wav")
    assert is_video_file_ref(_file_ref_solto("v.mp4", "v.mp4", "3", "2"), "v.mp4")
