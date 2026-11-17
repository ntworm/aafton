"""Testes de unidade e integracao do sidecar clip_origins e do verify organizado (P3c)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from aafton.als_read import read_session
from aafton.als_xml import load_als, save_als, set_value
from aafton.pipeline import ImportOptions, import_session
from aafton.report import Report
from aafton.sidecar import Anchor, read_anchor, sidecar_path_for
from aafton.timecode import TimecodeRate
from aafton.verify import verify_conversion
from aafton.verify_organize import rebuild_organized_destination
from tests.aaf_factory import build_calibration_aaf, wav_bytes
from tests.support.paths import corpus_dir, fixture_aaf


@pytest.fixture
def pt_aaf(tmp_path: Path) -> Path:
    """Copia TESTE_AFF_PROTOOLS.aaf para pasta isolada sem Session Text concorrentes."""
    isolado = tmp_path / "aaf_isolado"
    isolado.mkdir()
    return Path(shutil.copy2(fixture_aaf("TESTE_AFF_PROTOOLS.aaf"), isolado))


@pytest.fixture
def audio_dir() -> Path:
    """Pasta do corpus de silencio TESTE_AAF conferida pelo manifesto."""
    return corpus_dir("teste_aaf")


def _build_synthetic_organized_aaf(tmp_path: Path) -> tuple[Path, Path]:
    """Constroi AAF sintetico onde a organizacao move clips entre tracks e cria track [-1]."""
    media_folder = tmp_path / "synth_media"
    media_folder.mkdir()

    wav_files = [
        "A017_A013_0514N0_001.wav",
        "A024_A021_0519VG_001.wav",
        "043CENA35APL1TK4-043.wav",
        "026CENA37APL3TK1-026.wav",
        "058CENA33APL1TK4-058.wav",
        "A010_A009_0505HJ_001.wav",
        "009CENA29BOFF-001.wav",
    ]
    paths: dict[str, Path] = {}
    for fname in wav_files:
        p = media_folder / fname
        p.write_bytes(wav_bytes(48000 * 10, channels=1, sample_rate=48000))
        paths[fname] = p

    # Track 1: CAMERA dominante (2 clips rolo A017/A024) + 1 clip PRODUCTION que move em [48000, 96000]
    # Track 2: PRODUCTION dominante (2 clips CENA) + 1 clip CAMERA que sobrepoe Track 1 em [0, 48000]
    #          (cria nova track CAM 2 com original_source_indices [-1]) + 1 clip VO que cria VO 1 [-1]
    tracks_spec = [
        {
            "name": "Audio 1",
            "clips": [
                {
                    "path": paths["A017_A013_0514N0_001.wav"],
                    "mob_name": "A017_A013_0514N0_001.r3d",
                    "start": 0,
                    "duration": 24000,
                    "offset": 0,
                },
                {
                    "path": paths["A024_A021_0519VG_001.wav"],
                    "mob_name": "A024_A021_0519VG_001.r3d",
                    "start": 24000,
                    "duration": 24000,
                    "offset": 0,
                },
                {
                    "path": paths["043CENA35APL1TK4-043.wav"],
                    "mob_name": "043CENA35APL1TK4-043.WAV",
                    "start": 48000,
                    "duration": 24000,
                    "offset": 0,
                },
            ],
        },
        {
            "name": "Audio 2",
            "clips": [
                {
                    "path": paths["A010_A009_0505HJ_001.wav"],
                    "mob_name": "A010_A009_0505HJ_001.r3d",
                    "start": 0,
                    "duration": 24000,
                    "offset": 0,
                },
                {
                    "path": paths["026CENA37APL3TK1-026.wav"],
                    "mob_name": "026CENA37APL3TK1-026.WAV",
                    "start": 24000,
                    "duration": 20000,
                    "offset": 0,
                },
                {
                    "path": paths["058CENA33APL1TK4-058.wav"],
                    "mob_name": "058CENA33APL1TK4-058.WAV",
                    "start": 72000,
                    "duration": 24000,
                    "offset": 0,
                },
                {
                    "path": paths["009CENA29BOFF-001.wav"],
                    "mob_name": "009CENA29BOFF-001.m4a",
                    "start": 96000,
                    "duration": 24000,
                    "offset": 0,
                },
            ],
        },
    ]
    aaf_path = build_calibration_aaf(
        tmp_path / "synth_organize.aaf",
        tracks_spec,
        frames=48000 * 5,
        start_timecode_frames=0,
    )
    return aaf_path, media_folder


@pytest.mark.parametrize("level", ["permissive", "balanced", "strict"])
def test_real_fixture_organize_type_writes_clip_origins_and_verifies_zero_delta(
    tmp_path: Path,
    pt_aaf: Path,
    audio_dir: Path,
    level: str,
) -> None:
    """Import de TESTE_AFF_PROTOOLS.aaf com organize='type' grava clip_origins e passa no verify."""
    out_als = tmp_path / f"pt_{level}.als"
    opts = ImportOptions(
        input=str(pt_aaf),
        output=str(out_als),
        media="link",
        audio_dir=str(audio_dir),
        stereo="pair",
        organize="type",
        certainty=level,
    )
    import_session(opts, Report(strict=False))
    assert out_als.exists()

    sidecar_file = sidecar_path_for(out_als)
    assert sidecar_file.exists()
    raw_sidecar = json.loads(sidecar_file.read_text(encoding="utf-8"))
    assert "clip_origins" in raw_sidecar
    assert isinstance(raw_sidecar["clip_origins"], list)
    assert len(raw_sidecar["clip_origins"]) > 0

    anchor = read_anchor(out_als)
    assert anchor.clip_origins is not None
    assert anchor.clip_origins == raw_sidecar["clip_origins"]

    # Pelo menos uma track pareada tem clip com duas chaves [src_l, order], [src_r, order]
    all_clip_entries = [c for entry in anchor.clip_origins for c in entry["clips"]]
    assert len(all_clip_entries) > 0
    assert any(len(c[2]) == 2 for c in all_clip_entries)

    res = verify_conversion(pt_aaf, out_als)
    assert res.ok is True, res.to_table()
    assert res.max_delta_samples == 0
    assert res.audio_expected == 90
    assert res.audio_compared == 90
    assert res.audio_missing == 0
    assert res.audio_extra == 0
    assert res.errors == []


def test_real_fixture_organize_off_writes_no_clip_origins_and_verifies(
    tmp_path: Path,
    pt_aaf: Path,
    audio_dir: Path,
) -> None:
    """O mesmo import com organize='off' nao escreve clip_origins e o verify continua ok."""
    out_als = tmp_path / "pt_off.als"
    opts = ImportOptions(
        input=str(pt_aaf),
        output=str(out_als),
        media="link",
        audio_dir=str(audio_dir),
        stereo="pair",
        organize="off",
    )
    import_session(opts, Report(strict=False))

    sidecar_file = sidecar_path_for(out_als)
    raw_sidecar = json.loads(sidecar_file.read_text(encoding="utf-8"))
    assert "clip_origins" not in raw_sidecar

    anchor = read_anchor(out_als)
    assert anchor.clip_origins is None

    res = verify_conversion(pt_aaf, out_als)
    assert res.ok is True, res.to_table()
    assert res.max_delta_samples == 0
    assert res.audio_expected == 90
    assert res.audio_compared == 90


def test_synthetic_session_with_moved_clips_and_new_tracks_verifies_and_detects_tampering(
    tmp_path: Path,
) -> None:
    """Caso sintetico com clips movidos entre tracks, track [-1] e teste de adulteracao."""
    aaf_path, media_folder = _build_synthetic_organized_aaf(tmp_path)
    out_als = tmp_path / "synth_organized.als"
    opts = ImportOptions(
        input=str(aaf_path),
        output=str(out_als),
        media="link",
        audio_dir=str(media_folder),
        stereo="pair",
        organize="type",
        certainty="permissive",
    )
    import_session(opts, Report(strict=False))

    anchor = read_anchor(out_als)
    assert anchor.clip_origins is not None
    assert anchor.audio_source_indices is not None

    # Confere no sidecar que ao menos uma track reune clips de source_index diferentes
    # e que ao menos uma track nova tem audio_source_indices [-1]
    mixed_source_tracks = [
        entry["track"]
        for entry in anchor.clip_origins
        if len({key[0] for _, _, keys in entry["clips"] for key in keys}) > 1
    ]
    assert len(mixed_source_tracks) >= 1
    assert [-1] in anchor.audio_source_indices

    res = verify_conversion(aaf_path, out_als)
    assert res.ok is True, res.to_table()
    assert res.max_delta_samples == 0
    assert res.audio_expected == 7
    assert res.audio_compared == 7
    assert res.audio_missing == 0
    assert res.audio_extra == 0

    # Tampering: desloca o inicio de um clip em 10 samples no XML do .als
    # Com sample_rate=48000 e bpm=60.0, 1 beat = 48000 samples -> 10 samples = 10 / 48000 beats
    root = load_als(out_als)
    first_clip = root.find(
        ".//LiveSet/Tracks/AudioTrack/DeviceChain/MainSequencer/Sample/"
        "ArrangerAutomation/Events/AudioClip"
    )
    assert first_clip is not None
    cur_start_node = first_clip.find("CurrentStart")
    assert cur_start_node is not None
    old_beats = float(cur_start_node.get("Value", "0"))
    new_beats = f"{old_beats + 10.0 / 48000.0:.12f}"
    set_value(first_clip, "CurrentStart", new_beats)
    first_clip.set("Time", new_beats)
    save_als(root, out_als)

    tampered = verify_conversion(aaf_path, out_als)
    assert tampered.ok is False
    assert tampered.max_delta_samples == 10
    divergent = [r for r in tampered.rows if r.delta_samples == 10]
    assert len(divergent) == 1


def test_rebuild_organized_destination_with_other_file_rebuilds_like_the_source(
    tmp_path: Path,
) -> None:
    """O AAF de origem copiado para outro caminho e remontado como no source_path."""
    aaf_path, media_folder = _build_synthetic_organized_aaf(tmp_path)
    out_als = tmp_path / "synth_pos.als"
    opts = ImportOptions(
        input=str(aaf_path),
        output=str(out_als),
        media="link",
        audio_dir=str(media_folder),
        stereo="pair",
        organize="type",
        certainty="permissive",
    )
    import_session(opts, Report(strict=False))

    destino = read_session(out_als, Report(strict=False))
    anchor = read_anchor(out_als)
    other_aaf = tmp_path / "exported_return.aaf"

    rebuilt, indices = rebuild_organized_destination(destino, anchor, other_aaf)
    na_origem, indices_na_origem = rebuild_organized_destination(destino, anchor, aaf_path)
    assert indices == indices_na_origem
    assert [t.name for t in rebuilt.tracks] == [t.name for t in na_origem.tracks]

    # E quando o sidecar nao tem clip_origins, devolve destino e audio_source_indices intactos
    anchor_no_origins = Anchor(
        live_origin_samples=anchor.live_origin_samples,
        session_start_samples=anchor.session_start_samples,
        sample_rate=anchor.sample_rate,
        timecode_rate=TimecodeRate(25, 1),
        bpm=anchor.bpm,
        source_path=anchor.source_path,
        audio_source_indices=[[0], [1]],
        clip_origins=None,
    )
    same_dest, same_indices = rebuild_organized_destination(destino, anchor_no_origins, aaf_path)
    assert same_dest is destino
    assert same_indices == [[0], [1]]
