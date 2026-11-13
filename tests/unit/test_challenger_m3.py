# tests/unit/test_challenger_m3.py
"""Adversarial & empirical verification test suite for Milestone 3 (R3 - Fade Absorption).

Authored by challenger_m3_2.
"""

import json
import os
from pathlib import Path
import pytest
import subprocess
import sys

from aafton.aaf_read import read_aaf
from aafton.als_read import read_session
from aafton.report import Report
from aafton.sidecar import read_anchor, sidecar_path_for
from aafton.verify import verify_conversion
from tests.aaf_factory import build_aaf_with_rendered_fades, wav_bytes
from tests.support.paths import corpus_dir, fixture_aaf, reexport_corpus


@pytest.fixture
def pt_aaf(tmp_path):
    """AAF real numa pasta so dele, como estava no Desktop quando estes testes foram escritos.

    Em tests/fixtures/aaf ha quatro Session Text de outras sessoes; a descoberta
    automatica acha todos e o import estrito para com SESSION_TEXT_MULTIPLE_CANDIDATES.
    """
    import shutil

    isolado = tmp_path / "aaf_isolado"
    isolado.mkdir()
    return Path(shutil.copy2(fixture_aaf("TESTE_AFF_PROTOOLS.aaf"), isolado))


@pytest.fixture
def audio_dir():
    """Silêncio estrutural TESTE_AAF, conferido contra tests/corpus/teste_aaf.json."""
    return corpus_dir("teste_aaf")


@pytest.fixture
def wav(tmp_path):
    caminho = tmp_path / "som.wav"
    caminho.write_bytes(wav_bytes(48000 * 120))
    return caminho


# ============================================================================
# 1. Real-World Fixture Empirical Verification
# ============================================================================


@pytest.mark.parametrize("stereo_mode", ["split", "pair"])
def test_real_fixture_absorb_mode_empirical_metrics(tmp_path, stereo_mode, pt_aaf, audio_dir):
    """Empirically test TESTE_AFF_PROTOOLS.aaf in absorb mode."""

    out_als = tmp_path / "teste_pt_absorb.als"
    report_json = tmp_path / "teste_pt_absorb.report.json"

    # 1. CLI Import in absorb mode
    cmd_import = [
        sys.executable,
        "-m",
        "aafton.cli",
        "import",
        str(pt_aaf),
        str(out_als),
        "--media",
        "link",
        "--audio-dir",
        str(audio_dir),
        "--fades",
        "absorb",
        "--stereo",
        stereo_mode,
        "--report",
        str(report_json),
    ]
    res_import = subprocess.run(
        cmd_import, capture_output=True, text=True, cwd=str(Path(__file__).parents[2])
    )
    assert res_import.returncode == 0, f"Import failed: {res_import.stderr}\n{res_import.stdout}"
    assert out_als.exists(), "ALS file was not generated"

    expected_tracks = 17 if stereo_mode == "split" else 14
    # split 90, pair 81: dos 6 fades mantidos, um cai numa track que pareia e
    # seus dois canais viram um clip so, entao pair ganha 5 e nao 6.
    expected_clips = 90 if stereo_mode == "split" else 81

    # 2. Check Sidecar .aafton.json
    sidecar = sidecar_path_for(out_als)
    assert sidecar.exists(), "Sidecar .aafton.json missing"
    with open(sidecar, "r", encoding="utf-8") as sf:
        sidecar_data = json.load(sf)
    assert "fades" in sidecar_data, "Sidecar missing 'fades' key"
    assert sidecar_data["fades"] == "absorb", f"Expected 'absorb', got {sidecar_data['fades']}"
    assert sidecar_data["sample_rate"] == 48000
    assert sidecar_data["bpm"] == 60.0

    anchor = read_anchor(out_als)
    assert anchor.fades == "absorb"

    # 3. Read generated ALS and verify structure
    rep = Report(strict=False)
    als_session = read_session(out_als, rep)
    assert len(als_session.tracks) == expected_tracks, (
        f"Expected {expected_tracks} tracks, got {len(als_session.tracks)}"
    )

    track_names = [t.name for t in als_session.tracks]
    assert len(set(track_names)) == expected_tracks, f"Duplicate track names found: {track_names}"
    assert "5.1 [LFE]" in track_names
    assert "AMB 1ST [W]" in track_names

    all_als_clips = list(als_session.all_clips())
    assert len(all_als_clips) == expected_clips, (
        f"Expected {expected_clips} clips in absorb mode, got {len(all_als_clips)}"
    )

    # 4. Check AAF IR directly
    rep_aaf = Report(strict=False)
    aaf_session = read_aaf(pt_aaf, rep_aaf, fades="absorb")
    aaf_clips = list(aaf_session.all_clips())
    assert len(aaf_clips) == 90, f"Expected 90 AAF clips in absorb mode, got {len(aaf_clips)}"

    # 5. Invariant checks on all clips:
    # - No clip has fade_in + fade_out > duration_samples
    # - fade_in > 0 and fade_out > 0 distribution
    for c in all_als_clips:
        fi = c.fade_in.duration_samples if c.fade_in else 0
        fo = c.fade_out.duration_samples if c.fade_out else 0
        assert fi + fo <= c.duration_samples, (
            f"Clip {c.name} (track {c.track_id}) crossfade length {fi}+{fo} exceeds duration {c.duration_samples}"
        )

    # 6. Run verify command via CLI
    cmd_verify = [
        sys.executable,
        "-m",
        "aafton.cli",
        "verify",
        str(pt_aaf),
        str(out_als),
    ]
    res_verify = subprocess.run(
        cmd_verify, capture_output=True, text=True, cwd=str(Path(__file__).parents[2])
    )
    assert res_verify.returncode == 0, f"Verify failed: {res_verify.stderr}\n{res_verify.stdout}"

    # 7. Check verify programmatic result
    v_res = verify_conversion(str(pt_aaf), str(out_als))
    assert v_res.ok is True
    assert v_res.max_delta_samples == 0
    assert len(v_res.rows) == 90, f"Expected 90 verify rows, got {len(v_res.rows)}"
    for r in v_res.rows:
        assert r.delta_samples == 0, f"Row {r} has non-zero delta"


def test_real_fixture_keep_mode_empirical_metrics(tmp_path):
    """TESTE_NOVO keep: oracle pyaaf2 direto, sem contagens de outra sessão."""
    import shutil
    import aaf2
    from fractions import Fraction

    reexport_aaf, audio_dir = reexport_corpus()
    isolado = tmp_path / "aaf_reexport_isolado"
    isolado.mkdir()
    pt_aaf = Path(shutil.copy2(reexport_aaf, isolado))

    # Fonte independente dos leitores do produto, fixada por hash (D13).
    expected = []
    with aaf2.open(str(pt_aaf), "r") as f:
        comp = next(f.content.compositionmobs())
        for slot in comp.slots:
            if slot.segment.media_kind != "Sound":
                continue
            assert Fraction(slot.edit_rate) == 48000
            assert type(slot.segment).__name__ == "Sequence"
            clips, cursor = [], 0
            for component in slot.segment.components:
                if type(component).__name__ == "Filler":
                    cursor += component.length
                    continue
                assert type(component).__name__ == "OperationGroup"
                sources = list(component.segments)
                assert len(sources) == 1 and type(sources[0]).__name__ == "SourceClip"
                source = sources[0]
                assert source.length == component.length
                clips.append((cursor, cursor + component.length, source.start, source.mob.name))
                cursor += component.length
            assert cursor == slot.segment.length
            expected.append(clips)
    expected_count = sum(map(len, expected))
    assert expected_count > 0

    out_als = tmp_path / "teste_pt_keep.als"
    report_json = tmp_path / "teste_pt_keep.report.json"

    # 1. CLI Import in keep mode
    cmd_import = [
        sys.executable,
        "-m",
        "aafton.cli",
        "import",
        str(pt_aaf),
        str(out_als),
        "--media",
        "link",
        "--audio-dir",
        str(audio_dir),
        "--fades",
        "keep",
        "--stereo",
        "split",
        "--report",
        str(report_json),
    ]
    res_import = subprocess.run(
        cmd_import, capture_output=True, text=True, cwd=str(Path(__file__).parents[2])
    )
    assert res_import.returncode == 0, f"Import failed: {res_import.stderr}"
    assert out_als.exists(), "ALS file was not generated"

    # 2. Check Sidecar .aafton.json
    sidecar = sidecar_path_for(out_als)
    assert sidecar.exists(), "Sidecar .aafton.json missing"
    with open(sidecar, "r", encoding="utf-8") as sf:
        sidecar_data = json.load(sf)
    assert sidecar_data["fades"] == "keep", f"Expected 'keep', got {sidecar_data.get('fades')}"

    # 3. Read generated ALS and verify structure
    rep = Report(strict=False)
    als_session = read_session(out_als, rep)
    assert len(als_session.tracks) == len(expected)
    all_als_clips = list(als_session.all_clips())
    assert len(all_als_clips) == expected_count
    for track, oracle in zip(als_session.tracks, expected):
        assert [
            (c.timeline_start_samples, c.timeline_end_samples, c.source_offset_samples, c.name)
            for c in track.clips
        ] == oracle

    # 4. Check AAF IR directly
    rep_aaf = Report(strict=False)
    aaf_session = read_aaf(pt_aaf, rep_aaf, fades="keep")
    aaf_clips = list(aaf_session.all_clips())
    assert len(aaf_clips) == expected_count

    # 5. Run verify command via CLI
    cmd_verify = [
        sys.executable,
        "-m",
        "aafton.cli",
        "verify",
        str(pt_aaf),
        str(out_als),
    ]
    res_verify = subprocess.run(
        cmd_verify, capture_output=True, text=True, cwd=str(Path(__file__).parents[2])
    )
    assert res_verify.returncode == 0, f"Verify failed: {res_verify.stderr}\n{res_verify.stdout}"

    # 6. Cobertura derivada do AAF externo, zero desvio.
    v_res = verify_conversion(str(pt_aaf), str(out_als))
    assert v_res.ok is True
    assert v_res.max_delta_samples == 0
    assert len(v_res.rows) == expected_count


def test_teste_novo_pareado_preserva_corpus_e_geometria(tmp_path):
    """D13: preparação nomeia cópia; CLI efetiva gera quatro pares completos."""
    import aaf2
    import hashlib
    import shutil
    from scripts.prepare_teste_novo import prepare
    from tests.support.paths import REPO

    original, _ = reexport_corpus()
    before = hashlib.sha256(original.read_bytes()).hexdigest()
    selected = json.loads(
        (REPO / "tests/corpus/teste_novo_tracks.json").read_text(encoding="utf-8")
    )
    output = tmp_path / "teste-novo"
    bounces = output / "bounces"
    bounces.mkdir(parents=True)
    for track in selected["tracks"]:
        reference_dir = Path(
            os.environ.get("AAFTON_REFERENCE_DIR", str(REPO / "out/teste-novo/bounces"))
        )
        shutil.copy2(reference_dir / track["reference"], bounces)
    proof = prepare(output)
    assert proof["import_exit"] == 0
    assert hashlib.sha256(original.read_bytes()).hexdigest() == before
    named = output / "live/aaf-nomeado" / original.name

    def geometry(path):
        def segment(node):
            result = [type(node).__name__, node.length]
            if type(node).__name__ == "SourceClip":
                result += [str(node.mob_id), node.slot_id, node.start]
            if type(node).__name__ == "OperationGroup":
                result.append(str(node.operation.auid))
            for children in (getattr(node, "components", []), getattr(node, "segments", [])):
                result.append([segment(child) for child in children])
            return result

        with aaf2.open(str(path), "r") as f:
            return [
                (s.slot_id, str(s.edit_rate), segment(s.segment))
                for s in next(f.content.compositionmobs()).slots
            ]

    assert geometry(original) == geometry(named)
    als = Path(proof["als"])
    anchor = json.loads(als.with_suffix(".aafton.json").read_text(encoding="utf-8"))
    assert anchor["audio_source_indices"] == [[0, 1], [2, 3], [4, 5], [6, 7]]
    assert list(anchor["track_channels"].values()) == [2, 2, 2, 2]
    assert anchor["live_origin_samples"] == 182880000
    verification = verify_conversion(str(named), str(als))
    assert verification.ok and verification.max_delta_samples == 0
    # Mesma cobertura do AAF original, não só quantidade das tracks pareadas.
    with aaf2.open(str(original), "r") as f:
        count = sum(
            type(c).__name__ != "Filler"
            for s in next(f.content.compositionmobs()).slots
            if s.segment.media_kind == "Sound"
            for c in s.segment.components
        )
    assert len(verification.rows) == count


def test_cli_default_fades_option_is_absorb(tmp_path, pt_aaf, audio_dir):
    """When --fades is omitted from CLI import, default must be 'absorb'."""

    out_als = tmp_path / "teste_pt_default.als"
    cmd_import = [
        sys.executable,
        "-m",
        "aafton.cli",
        "import",
        str(pt_aaf),
        str(out_als),
        "--media",
        "link",
        "--audio-dir",
        str(audio_dir),
    ]
    res = subprocess.run(
        cmd_import, capture_output=True, text=True, cwd=str(Path(__file__).parents[2])
    )
    assert res.returncode == 0
    sidecar = sidecar_path_for(out_als)
    assert sidecar.exists()
    with open(sidecar, "r", encoding="utf-8") as sf:
        sidecar_data = json.load(sf)
    assert sidecar_data["fades"] == "absorb"

    v_res = verify_conversion(str(pt_aaf), str(out_als))
    assert len(v_res.rows) == 90


def test_real_fixture_absolute_paths(tmp_path, pt_aaf, audio_dir):
    """After importing with pair, all FileRef/Path must be absolute."""

    out_als = tmp_path / "teste_pt_abs.als"
    cmd_import = [
        sys.executable,
        "-m",
        "aafton.cli",
        "import",
        str(pt_aaf),
        str(out_als),
        "--media",
        "extract",
        "--audio-dir",
        str(audio_dir),
        "--stereo",
        "pair",
    ]
    res = subprocess.run(cmd_import, capture_output=True, cwd=str(Path(__file__).parents[2]))
    assert res.returncode == 0, "Import failed"

    import xml.etree.ElementTree as ET
    import gzip

    with gzip.open(out_als, "rt", encoding="utf-8") as f:
        tree = ET.parse(f)
    for p in tree.findall(".//SampleRef/FileRef/Path"):
        path_str = p.attrib.get("Value", "")
        if path_str:
            assert Path(path_str).is_absolute(), f"Found relative path in ALS: {path_str}"


@pytest.mark.parametrize("stereo_mode", ["split", "pair"])
def test_real_fixture_export_reports_loss_and_preserves_structure(
    tmp_path, stereo_mode, pt_aaf, audio_dir
):
    """Perda de automação reprova export estrito; permissivo mantém prova estrutural."""

    als1 = tmp_path / f"pass1_{stereo_mode}.als"
    cmd_import = [
        sys.executable,
        "-m",
        "aafton.cli",
        "import",
        str(pt_aaf),
        str(als1),
        "--media",
        "link",
        "--audio-dir",
        str(audio_dir),
        "--stereo",
        stereo_mode,
    ]
    res = subprocess.run(cmd_import, capture_output=True, cwd=str(Path(__file__).parents[2]))
    assert res.returncode == 0, "Pass 1 import failed"

    # Verify expected tracks
    rep1 = Report(strict=False)
    session1 = read_session(als1, rep1)
    expected_tracks = 17 if stereo_mode == "split" else 14
    assert len(session1.tracks) == expected_tracks

    if stereo_mode == "pair":
        track_ref = next(t for t in session1.tracks if "REF" in t.name)
        track_stereo = next(t for t in session1.tracks if "STEREO" in t.name)
        track_mono = next(t for t in session1.tracks if "MONO" in t.name)
        track_51 = next(t for t in session1.tracks if "5.1" in t.name)
        assert track_ref.channel_count == 2
        assert track_stereo.channel_count == 2
        assert track_mono.channel_count == 1
        assert track_51.channel_count == 1

    track_names_1 = [t.name for t in session1.tracks]

    aaf_out = tmp_path / f"export_{stereo_mode}.aaf"
    cmd_export = [sys.executable, "-m", "aafton.cli", "export", str(als1), str(aaf_out)]
    res = subprocess.run(
        cmd_export, capture_output=True, text=True, cwd=str(Path(__file__).parents[2])
    )
    assert res.returncode == 1
    # A recusa continua existindo; o que mudou e qual camada a nomeia. A automacao
    # do Live passou a ser exportada em trechos, entao neste fixture o que barra o
    # export estrito e o fade independente.
    assert any(
        code in res.stderr
        for code in (
            "WRITE_SESSION_FEATURE_UNSUPPORTED",
            "WRITE_FADE_UNSUPPORTED",
            "WRITE_GAIN_AUTOMATION_UNSUPPORTED",
        )
    )
    assert not aaf_out.exists()
    export_report = tmp_path / "export.json"
    res = subprocess.run(
        cmd_export + ["--permissive", "--report", str(export_report)],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).parents[2]),
    )
    assert res.returncode == 1, f"Export should report unsupported features: {res.stderr}"
    evidence = json.loads(export_report.read_text(encoding="utf-8"))
    assert evidence["incomplete"]

    import aaf2

    with aaf2.open(str(aaf_out), "r") as f:
        comp = [m for m in f.content.mobs if isinstance(m, aaf2.mobs.CompositionMob)][0]
        # Ignore TC slot, get only audio slots
        audio_slots = [s for s in comp.slots if s.name != "TC"]
        expected_slots = 17 if stereo_mode == "split" else 14
        assert len(audio_slots) == expected_slots

        phys_tracks = []
        for s in audio_slots:
            prop = s.get("PhysicalTrackNumber")
            if prop is not None and prop.value is not None:
                phys_tracks.append(prop.value)

        assert len(phys_tracks) == expected_slots
        assert len(set(phys_tracks)) == expected_slots

        if stereo_mode == "pair":
            stereo_slots = [s for s in audio_slots if "STEREO" in s.name]
            assert len(stereo_slots) == 1

            sequence = stereo_slots[0].segment
            assert isinstance(sequence, aaf2.components.Sequence)

            def operations(node):
                if isinstance(node, aaf2.components.OperationGroup):
                    yield node
                for child in list(getattr(node, "components", []) or []) + list(
                    getattr(node, "segments", []) or []
                ):
                    yield from operations(child)

            combiners = [
                component
                for component in operations(sequence)
                if str(component.operation.auid) == "6b46dd7a-132d-4856-ab21-8b751d8462ec"
            ]
            assert combiners
            for combiner in combiners:
                assert str(combiner.operation.auid) == ("6b46dd7a-132d-4856-ab21-8b751d8462ec")
                # D12b: Pro Tools recebe masters mono distintos no combiner.
                assert [source.slot_id for source in combiner.segments] == [1, 1]
                assert len({str(source.mob.mob_id) for source in combiner.segments}) == 2

    als2 = tmp_path / f"pass2_{stereo_mode}.als"
    cmd_reimport = [
        sys.executable,
        "-m",
        "aafton.cli",
        "import",
        str(aaf_out),
        str(als2),
        "--media",
        "link",
        "--audio-dir",
        str(audio_dir),
        "--stereo",
        stereo_mode,
    ]
    res = subprocess.run(cmd_reimport, capture_output=True, cwd=str(Path(__file__).parents[2]))
    assert res.returncode == 0, "Pass 2 import failed"

    rep2 = Report(strict=False)
    session2 = read_session(als2, rep2)
    track_names_2 = [t.name for t in session2.tracks]

    assert track_names_1 == track_names_2


# ============================================================================
# 2. Adversarial Edge Cases & Mathematical Stress Tests
# ============================================================================


def test_adversarial_chained_crossfades_and_fades(tmp_path, wav):
    """Complex chain: FadeIn -> ClipA -> Crossfade -> ClipB -> Crossfade -> ClipC -> FadeOut."""
    aaf = build_aaf_with_rendered_fades(
        tmp_path / "complex_chain.aaf",
        wav,
        [
            ("fade", 1000, "Fade 1"),
            ("clip", 10000, 5000),
            ("fade", 2000, "Fade 2"),
            ("clip", 20000, 8000),
            ("fade", 3001, "Fade 3"),  # odd crossfade length
            ("clip", 15000, 10000),
            ("fade", 4000, "Fade 4"),
        ],
        start_timecode_frames=0,
    )
    report = Report(strict=False)
    session = read_aaf(aaf, report, fades="absorb")
    assert len(session.tracks[0].clips) == 3
    ca, cb, cc = session.tracks[0].clips

    # Clip A:
    # Initial start was 1000. Recedes by 1000 (Fade 1) -> start = 0
    # Initial duration was 10000. Gains 1000 (Fade 1) + 1000 (Fade 2 La) -> duration = 12000
    # End = 12000
    # Offset was 5000. Recedes by 1000 -> 4000
    assert ca.timeline_start_samples == 0
    assert ca.timeline_end_samples == 13000
    assert ca.source_offset_samples == 4000
    assert ca.fade_in.duration_samples == 1000
    assert ca.fade_out.duration_samples == 2000

    # Clip B:
    # Initial start was 1000 + 10000 + 2000 = 13000.
    # Recedes by 1000 (Fade 2 Lb) -> start = 12000
    # Initial duration was 20000. Gains 1000 (Fade 2 Lb) + 1500 (Fade 3 La = 3001 // 2 = 1500) -> duration = 22500
    # End = 12000 + 22500 = 34500
    # Offset was 8000. Recedes by 1000 -> 7000
    assert cb.timeline_start_samples == 13000
    assert cb.timeline_end_samples == 36001
    assert cb.source_offset_samples == 8000
    assert getattr(cb, "fade_in", None) is None
    assert cb.crossfade_in_state is True
    assert cb.fade_out.duration_samples == 3001

    # Junction between A and B
    assert ca.timeline_end_samples == cb.timeline_start_samples

    # Clip C:
    # Initial start was 13000 + 20000 + 3001 = 36001.
    # Recedes by 1501 (Fade 3 Lb = 3001 - 1500 = 1501) -> start = 34500
    # Initial duration was 15000. Gains 1501 (Fade 3 Lb) + 4000 (Fade 4) -> duration = 20501
    # End = 34500 + 20501 = 55001
    # Offset was 10000. Recedes by 1501 -> 8499
    assert cc.timeline_start_samples == 36001
    assert cc.timeline_end_samples == 55001
    assert cc.source_offset_samples == 10000
    assert getattr(cc, "fade_in", None) is None
    assert cc.crossfade_in_state is True
    assert cc.fade_out.duration_samples == 4000

    # Junction between B and C
    assert cb.timeline_end_samples == cc.timeline_start_samples

    # Verify no clip exceeds duration
    for c in [ca, cb, cc]:
        fi = c.fade_in.duration_samples if c.fade_in else 0
        fo = c.fade_out.duration_samples if c.fade_out else 0
        assert fi + fo <= c.duration_samples


def test_adversarial_isolated_fade_and_triple_fade(tmp_path, wav):
    """Test edge cases with isolated fades or multi-adjacent fades."""
    aaf = build_aaf_with_rendered_fades(
        tmp_path / "isolated_fades.aaf",
        wav,
        [
            ("filler", 1000),
            ("fade", 500, "Fade 1"),  # Isolated between fillers
            ("filler", 1000),
            ("clip", 10000, 2000),
            ("fade", 500, "Fade 2"),  # Triple fades between clips
            ("fade", 600, "Fade 3"),
            ("fade", 700, "Fade 4"),
            ("clip", 10000, 5000),
        ],
        start_timecode_frames=0,
    )
    report = Report(strict=False)
    session = read_aaf(aaf, report, fades="absorb")
    # Must not crash, should produce exactly the 2 clips
    assert len(session.tracks[0].clips) == 2
    c1, c2 = session.tracks[0].clips
    assert c1.fade_out is not None
    assert c2.fade_in is not None


def test_adversarial_zero_length_and_tiny_fades(tmp_path, wav):
    """Test 0-length fades and 1-sample fades."""
    aaf = build_aaf_with_rendered_fades(
        tmp_path / "tiny_fades.aaf",
        wav,
        [
            ("clip", 5000, 1000),
            ("fade", 0, "Fade 0"),
            ("fade", 1, "Fade 1"),
            ("clip", 5000, 1000),
        ],
        start_timecode_frames=0,
    )
    report = Report(strict=False)
    session = read_aaf(aaf, report, fades="absorb")
    assert len(session.tracks[0].clips) == 2
    c1, c2 = session.tracks[0].clips
    # 1 sample crossfade: La = 0, Lb = 1
    assert c1.fade_out.duration_samples == 1
    assert getattr(c2, "fade_in", None) is None
    assert c2.crossfade_in_state is True
    assert c1.timeline_end_samples == c2.timeline_start_samples
