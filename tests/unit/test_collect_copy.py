"""Testes unitarios da regra de nomes, copia verificada, staging e collect_into_output."""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from aafton.als_xml import get_value, load_als, save_als
from aafton.collect import (
    CollectError,
    collect_into_output,
    norm_path_key,
    plan_collect,
    run_collect,
)
from aafton.collect_copy import assign_dest_names, copy_file_verified, resolved_str
from aafton.sidecar import Anchor, read_anchor, write_sidecar
from aafton.timecode import TimecodeRate
from tests.aaf_factory import build_linked_aaf, wav_bytes


def _write_wav(path: Path, frames: int = 48000, tag: bytes = b"\x00\x00") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = bytearray(wav_bytes(frames, channels=1, sample_rate=48000))
    raw[44 : 44 + len(tag)] = tag
    path.write_bytes(bytes(raw))
    return path


def _make_als_with_clips(als_path: Path, clip_paths: list[Path]) -> Path:
    root = ET.Element("Ableton", Creator="Ableton Live 12.4.6")
    live = ET.SubElement(root, "LiveSet")
    tracks = ET.SubElement(live, "Tracks")
    track = ET.SubElement(tracks, "AudioTrack", Id="10")
    name_el = ET.SubElement(track, "Name")
    ET.SubElement(name_el, "EffectiveName", Value="DX")
    dc = ET.SubElement(track, "DeviceChain")
    ms = ET.SubElement(dc, "MainSequencer")
    samp = ET.SubElement(ms, "Sample")
    arr = ET.SubElement(samp, "ArrangerAutomation")
    events = ET.SubElement(arr, "Events")
    for idx, cp in enumerate(clip_paths):
        clip = ET.SubElement(events, "AudioClip", Id=str(idx), Time=str(idx * 2.0))
        ET.SubElement(clip, "CurrentStart", Value=str(idx * 2.0))
        ET.SubElement(clip, "CurrentEnd", Value=str(idx * 2.0 + 1.0))
        ET.SubElement(clip, "Name", Value=cp.stem)
        sr = ET.SubElement(clip, "SampleRef")
        fref = ET.SubElement(sr, "FileRef")
        ET.SubElement(fref, "RelativePathType", Value="0")
        ET.SubElement(fref, "RelativePath", Value="")
        ET.SubElement(fref, "Path", Value=str(cp))
    main = ET.SubElement(live, "MainTrack")
    m_dc = ET.SubElement(main, "DeviceChain")
    mixer = ET.SubElement(m_dc, "Mixer")
    tempo = ET.SubElement(mixer, "Tempo")
    ET.SubElement(tempo, "Manual", Value="60.0")
    save_als(root, als_path)
    return als_path


def test_name_rule_collision_8_and_12_hex(tmp_path):
    """Mesma origem duas vezes vira 1; mesmo nome ganha __<8 hex>, e se tomado cresce para 12 hex."""
    dir_a = tmp_path / "a_folder"
    dir_b = tmp_path / "b_folder"
    src_a = _write_wav(dir_a / "boom.wav", tag=b"\x01\x01")
    src_b = _write_wav(dir_b / "boom.wav", tag=b"\x02\x02")

    # Ordenado por norm_path_key: dir_a vem antes de dir_b
    res_b_str = resolved_str(src_b)
    hash_b = hashlib.sha256(res_b_str.encode("utf-8")).hexdigest()
    expected_8 = f"boom__{hash_b[:8]}.wav"
    expected_12 = f"boom__{hash_b[:12]}.wav"

    # Sem existentes: src_a fica 'boom.wav', src_b fica com 8 hex; src_a repetido nao duplica
    mapping = assign_dest_names([src_b, src_a, src_a])
    assert len(mapping) == 2
    assert mapping[norm_path_key(src_a)] == "boom.wav"
    assert mapping[norm_path_key(src_b)] == expected_8

    # Quando o nome de 8 hex ja existe na pasta (existing_names), forca 12 hex
    mapping_12 = assign_dest_names([src_a, src_b], existing_names={expected_8})
    assert mapping_12[norm_path_key(src_a)] == "boom.wav"
    assert mapping_12[norm_path_key(src_b)] == expected_12


def test_same_source_referenced_twice_copied_once_in_als(tmp_path):
    """Mesmo arquivo referenciado em dois clips e copiado apenas uma vez."""
    src_dir = tmp_path / "src"
    wav = _write_wav(src_dir / "shared.wav", frames=48000, tag=b"\x55\x66")
    als_path = _make_als_with_clips(src_dir / "double.als", [wav, wav])

    plan = plan_collect(als_path)
    assert len(plan.items) == 1

    progress_calls: list[tuple[int, int, str]] = []
    dest_dir = tmp_path / "out_double"
    res = run_collect(plan, dest_dir, progress=lambda c, t, n: progress_calls.append((c, t, n)))
    assert res.exit_code == 0
    assert res.report.counts["copied"] == 1
    assert len(list(res.media_dir.iterdir())) == 1
    assert progress_calls
    assert progress_calls[-1][0] == progress_calls[-1][1]
    assert progress_calls[-1][2] == "shared.wav"

    # Ambos os clips apontam para a mesma copia
    root = load_als(res.session_path)
    frefs = list(root.iter("FileRef"))
    assert len(frefs) == 2
    for fref in frefs:
        assert get_value(fref, "RelativePath") == "double Media/shared.wav"


def test_collect_into_output_existing_media_and_sidecar_preservation(tmp_path):
    """Variante collect_into_output: already_there nao e copiado, alheio fica intacto, origem ganha sufixo."""
    out_dir = tmp_path / "export_dir"
    media_dir = out_dir / "exported Media"
    media_dir.mkdir(parents=True)

    # 1. Um arquivo da sessao que ja esta dentro de '<saida> Media/'
    already_file = _write_wav(media_dir / "in_media.wav", frames=48000, tag=b"\xaa\xbb")
    already_bytes = already_file.read_bytes()
    already_mtime = already_file.stat().st_mtime_ns

    # 2. Um arquivo alheio na pasta Media com nome 'take.wav'
    alien_file = _write_wav(media_dir / "take.wav", frames=24000, tag=b"\xcc\xdd")
    alien_bytes = alien_file.read_bytes()
    alien_mtime = alien_file.stat().st_mtime_ns

    # 3. Um arquivo da sessao em outra pasta tambem chamado 'take.wav', mais um que ja forca 12 hex
    ext_dir = tmp_path / "external_audio"
    ext_take = _write_wav(ext_dir / "take.wav", frames=48000, tag=b"\xee\xff")
    ext_take_hash = hashlib.sha256(resolved_str(ext_take).encode("utf-8")).hexdigest()
    collision_8 = media_dir / f"take__{ext_take_hash[:8]}.wav"
    collision_8.write_bytes(b"pre-existing 8-hex file")

    als_path = _make_als_with_clips(out_dir / "exported.als", [already_file, ext_take])

    # Sidecar com todos os bindings para provar Achado 1 em collect_into_output tambem
    orig_anchor = Anchor(
        live_origin_samples=0,
        session_start_samples=48000,
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1, False),
        bpm=60.0,
        source_path="C:/orig/session.aaf",
        video_path=None,
        clip_ranges=[{"track": "DX", "ranges": [[48000, 96000]]}],
        track_bindings=[
            {
                "index": 0,
                "live_track_id": "10",
                "track_name": "DX",
                "origin_slot_id": 1,
                "mob_id": "umid-1",
                "channel_count": 1,
            }
        ],
        media_hashes={
            str(ext_take.resolve()): hashlib.sha256(ext_take.read_bytes()).hexdigest(),
        },
        expanded={"folder": "C:/rushes", "editor": "C:/orig/session.aaf", "tracks": {}},
    )
    write_sidecar(als_path, orig_anchor)

    res = collect_into_output(als_path)
    assert res.exit_code == 0
    assert res.report.counts["already_there"] == 1
    assert res.report.counts["copied"] == 1

    # O arquivo already_there nao foi reaberto nem modificado
    assert already_file.read_bytes() == already_bytes
    assert already_file.stat().st_mtime_ns == already_mtime

    # O arquivo alheio e o pre-existente de 8 hex continuam intactos
    assert alien_file.read_bytes() == alien_bytes
    assert alien_file.stat().st_mtime_ns == alien_mtime
    assert collision_8.read_bytes() == b"pre-existing 8-hex file"

    # A origem ext_take ganhou o sufixo de 12 hex
    expected_12_name = f"take__{ext_take_hash[:12]}.wav"
    copied_12 = media_dir / expected_12_name
    assert copied_12.is_file()
    assert copied_12.read_bytes() == ext_take.read_bytes()

    # As referencias no XML de exported.als foram atualizadas
    root = load_als(als_path)
    rel_paths = [get_value(fref, "RelativePath") for fref in root.iter("FileRef")]
    assert rel_paths == [
        "exported Media/in_media.wav",
        f"exported Media/{expected_12_name}",
    ]

    # Sidecar preservou bindings, clip_ranges, expanded e atualizou media_hashes
    after_anchor = read_anchor(als_path)
    assert after_anchor.track_bindings == orig_anchor.track_bindings
    assert after_anchor.clip_ranges == orig_anchor.clip_ranges
    assert after_anchor.expanded == orig_anchor.expanded
    assert after_anchor.source_path == orig_anchor.source_path
    assert str(copied_12.resolve()) in after_anchor.media_hashes
    ext_sha = hashlib.sha256(ext_take.read_bytes()).hexdigest()
    assert after_anchor.media_hashes[expected_12_name] == ext_sha
    assert after_anchor.media_hashes["in_media.wav"] == hashlib.sha256(already_bytes).hexdigest()


def test_collect_into_output_aaf_variant(tmp_path):
    """Testa collect_into_output em um AAF recem-exportado."""
    ext_dir = tmp_path / "ext"
    wav = _write_wav(ext_dir / "sfx.wav", frames=48000, tag=b"\x77\x88")
    out_dir = tmp_path / "aaf_out"
    out_dir.mkdir()
    aaf_path = build_linked_aaf(out_dir / "mix.aaf", [(0, 48000, 0)], wav)

    res = collect_into_output(aaf_path)
    assert res.exit_code == 0
    assert res.report.counts["copied"] == 1
    assert (out_dir / "mix Media" / "sfx.wav").is_file()


def test_never_overwrites_existing_file_and_validates_destination(tmp_path):
    """Nenhuma escrita abre arquivo existente ('xb'); destino nao-vazio recusa sem tocar em nada."""
    src = _write_wav(tmp_path / "src.wav", frames=1000)
    existing_dest = _write_wav(tmp_path / "dest.wav", frames=500)
    with pytest.raises(FileExistsError):
        copy_file_verified(src, existing_dest)

    als = _make_als_with_clips(tmp_path / "set.als", [src])

    # Destino com arquivo dentro -> erro e nada escrito
    non_empty = tmp_path / "non_empty"
    non_empty.mkdir()
    marker = non_empty / "keep.txt"
    marker.write_bytes(b"stay")
    with pytest.raises(CollectError, match="not empty"):
        run_collect(als, non_empty)
    assert list(non_empty.iterdir()) == [marker]
    assert marker.read_bytes() == b"stay"

    # Destino sendo uma pasta vazia -> permitido e sucede
    empty_dir = tmp_path / "empty_ok"
    empty_dir.mkdir()
    run_collect(als, empty_dir, name="custom_name")
    assert (empty_dir / "custom_name.als").is_file()
    assert (empty_dir / "custom_name Media" / "src.wav").is_file()


def test_staging_atomicity_and_report_formats(tmp_path):
    """A pasta de destino so aparece depois do rename do staging; testa to_markdown e to_json."""
    src = _write_wav(tmp_path / "tone.wav", frames=4800)
    als = _make_als_with_clips(tmp_path / "song.als", [src])
    target = tmp_path / "atomic_target"

    seen_during_progress: list[bool] = []

    def on_progress(copied: int, total: int, filename: str) -> None:
        # Enquanto a copia acontece no staging, target ainda nao existe
        seen_during_progress.append(target.exists())

    res = run_collect(als, target, progress=on_progress)
    assert seen_during_progress == [False]
    assert target.is_dir()
    assert res.report_path is not None and res.report_path.is_file()

    md = res.report.to_markdown()
    assert "# Collect media report" in md
    assert "tone.wav" in md
    assert res.report_path.read_text(encoding="utf-8") == md

    js = json.loads(res.report.to_json())
    assert js["counts"]["copied"] == 1
    assert js["total_bytes"] == src.stat().st_size
    assert js["items"][0]["state"] == "copied"


def test_video_path_relativo_do_sidecar_segue_a_copia(tmp_path, monkeypatch):
    from aafton.collect_copy import norm_path_key, rewrite_sidecar_data
    from aafton.sidecar import Anchor, read_anchor, write_sidecar
    from aafton.timecode import TimecodeRate

    pasta = tmp_path / "projeto"
    (pasta / "videos").mkdir(parents=True)
    video = pasta / "videos" / "corte.mp4"
    video.write_bytes(b"mp4")
    als = pasta / "cena.als"
    anchor = Anchor(
        live_origin_samples=0,
        session_start_samples=0,
        sample_rate=48000,
        timecode_rate=TimecodeRate(24, 1, False),
        bpm=60.0,
        video_path="videos/corte.mp4",
    )
    write_sidecar(als, anchor)
    copia = tmp_path / "entrega" / "cena Media" / "corte.mp4"
    copia.parent.mkdir(parents=True)
    copia.write_bytes(b"mp4")
    destino = tmp_path / "entrega" / "cena.als"
    # O cwd fora da pasta do set nao pode mudar a resolucao do caminho relativo.
    monkeypatch.chdir(tmp_path)
    rewrite_sidecar_data(als, destino, {norm_path_key(video): (copia, "x")}, None)
    assert read_anchor(destino).video_path == str(copia.resolve())


def test_video_relativo_nao_copiado_aponta_para_o_original(tmp_path, monkeypatch):
    from aafton.collect_copy import rewrite_sidecar_data
    from aafton.sidecar import Anchor, read_anchor, write_sidecar
    from aafton.timecode import TimecodeRate

    pasta = tmp_path / "projeto"
    (pasta / "videos").mkdir(parents=True)
    video = pasta / "videos" / "corte.mp4"
    video.write_bytes(b"mp4")
    als = pasta / "cena.als"
    write_sidecar(
        als,
        Anchor(
            live_origin_samples=0,
            session_start_samples=0,
            sample_rate=48000,
            timecode_rate=TimecodeRate(24, 1, False),
            bpm=60.0,
            video_path="videos/corte.mp4",
        ),
    )
    destino = tmp_path / "entrega" / "cena.als"
    destino.parent.mkdir()
    monkeypatch.chdir(tmp_path)
    rewrite_sidecar_data(als, destino, {}, None)
    assert read_anchor(destino).video_path == str(video.resolve())


def test_sidecar_antigo_mantem_versao_e_campos_desconhecidos(tmp_path):
    import json

    from aafton.collect_copy import rewrite_sidecar_data

    pasta = tmp_path / "projeto"
    pasta.mkdir()
    als = pasta / "cena.als"
    original = {
        "format_version": 1,
        "sample_rate": 48000,
        "bpm": 60.0,
        "timecode_rate": {"num": 24, "den": 1, "drop_frame": False},
        "live_origin_samples": 0,
        "session_start_samples": 0,
        "campo_futuro": {"a": 1},
    }
    (pasta / "cena.aafton.json").write_text(json.dumps(original), encoding="utf-8")
    destino = tmp_path / "entrega" / "cena.als"
    destino.parent.mkdir()

    escrito = rewrite_sidecar_data(als, destino, {}, None)

    assert json.loads(escrito.read_text(encoding="utf-8")) == original
