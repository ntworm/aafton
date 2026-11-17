"""Testes ponta a ponta da aplicacao da reconformacao em sets do Live."""

from __future__ import annotations

import hashlib
import json
import wave
from pathlib import Path

import pytest

from aafton.als_paths import TRACK_FIELDS
from aafton.als_read import read_session
from aafton.als_read_clips import _beats_to_samples, track_name
from aafton.als_write import WriteOptions, write_session
from aafton.als_xml import get_value, load_als
from aafton.cut_ir import CutEvent, CutIR
from aafton.cut_read import read_cut
from aafton.ir import ClipIR, MarkerIR, MediaAsset, SessionIR, TrackIR
from aafton.reconform_apply import ApplyResult, apply_reconform
from aafton.reconform_diff import ChangeList, diff_cuts
from aafton.reconform_map import Lacuna, Saiu, Segment, TimeMap
from aafton.report import Report, StrictModeError
from aafton.sidecar import read_anchor, sidecar_path_for
from aafton.timecode import TimecodeRate
from tests.unit.test_review_robustness import HOUR, make_set

RATE_25 = TimecodeRate(25, 1)
FRAME_25 = 1920
SEC = 48000

_OLD_EDL_25 = "\n".join(
    [
        "TITLE: OLD25",
        "FCM: NON-DROP FRAME",
        "",
        "001  R001     V     C        01:00:00:00 01:00:02:00 01:00:00:00 01:00:02:00",
        "* FROM CLIP NAME: shota.mov",
        "",
        "002  R002     V     C        02:00:00:00 02:00:02:00 01:00:02:00 01:00:04:00",
        "* FROM CLIP NAME: shotb.mov",
        "",
        "003  R003     V     C        03:00:00:00 03:00:02:00 01:00:04:00 01:00:06:00",
        "* FROM CLIP NAME: shotdel.mov",
        "",
        "004  R004     V     C        04:00:00:00 04:00:02:00 01:00:06:00 01:00:08:00",
        "* FROM CLIP NAME: shotc.mov",
        "",
    ]
)

_NEW_EDL_25 = "\n".join(
    [
        "TITLE: NEW25",
        "FCM: NON-DROP FRAME",
        "",
        "001  R001     V     C        01:00:00:00 01:00:02:00 01:00:00:00 01:00:02:00",
        "* FROM CLIP NAME: shota.mov",
        "",
        "002  R005     V     C        05:00:00:00 05:00:01:00 01:00:02:00 01:00:03:00",
        "* FROM CLIP NAME: shotins.mov",
        "",
        "003  R002     V     C        02:00:00:00 02:00:02:00 01:00:03:00 01:00:05:00",
        "* FROM CLIP NAME: shotb.mov",
        "",
        "004  R004     V     C        04:00:00:00 04:00:02:00 01:00:05:00 01:00:07:00",
        "* FROM CLIP NAME: shotc.mov",
        "",
    ]
)

_OLD_TRIM_EDL = "\n".join(
    [
        "TITLE: OLDTRIM",
        "FCM: NON-DROP FRAME",
        "",
        "001  R001     V     C        01:00:00:00 01:00:02:00 01:00:00:00 01:00:02:00",
        "002  R002     V     C        02:00:00:00 02:00:02:00 01:00:02:00 01:00:04:00",
        "003  R003     V     C        03:00:00:00 03:00:02:00 01:00:04:00 01:00:06:00",
        "004  R004     V     C        04:00:00:00 04:00:01:00 01:00:06:00 01:00:07:00",
        "",
    ]
)

_NEW_TRIM_EDL = "\n".join(
    [
        "TITLE: NEWTRIM",
        "FCM: NON-DROP FRAME",
        "",
        "001  R003     V     C        03:00:00:10 03:00:02:00 01:00:00:00 01:00:01:15",
        "002  R001     V     C        01:00:00:00 01:00:02:00 01:00:01:15 01:00:03:15",
        "003  R002     V     C        02:00:00:00 02:00:01:20 01:00:03:15 01:00:05:10",
        "004  R004     V     C        04:00:00:00 04:00:01:00 01:00:05:10 01:00:06:10",
        "",
    ]
)


def _kw(cls, nomes: str, args: tuple, extra: dict):
    """Constroi uma dataclass kw_only a partir de argumentos posicionais do teste."""
    return cls(**dict(zip(nomes.split(), args)), **extra)


def _clip(*args, **extra) -> ClipIR:
    nomes = "id track_id channel_index event_index name media_id"
    nomes += " timeline_start_samples timeline_end_samples source_offset_samples"
    return _kw(ClipIR, nomes, args, extra)


def _asset(*args, **extra) -> MediaAsset:
    nomes = "id filename channels sample_rate duration_samples status"
    nomes += " declared_location resolved_path"
    return _kw(MediaAsset, nomes, args, extra)


def _track(*args, **extra) -> TrackIR:
    nomes = "id source_index original_source_indices name kind channel_count"
    return _kw(TrackIR, nomes, args, extra)


def _session(*args, **extra) -> SessionIR:
    nomes = "name sample_rate timecode_rate start_timecode_samples"
    return _kw(SessionIR, nomes, args, extra)


def _marker(*args, **extra) -> MarkerIR:
    return _kw(MarkerIR, "id name position_samples", args, extra)


def _options(*args, **extra) -> WriteOptions:
    return _kw(WriteOptions, "sample_rate bpm live_origin_samples", args, extra)


def _sha256(path: Path) -> str:
    """Digest SHA-256 em hexadecimal de um arquivo em disco."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_wav(path: Path, channels: int, duration_samples: int) -> None:
    """Grava um WAV PCM silencioso com a duracao informada."""
    with wave.open(str(path), "wb") as handle:
        handle.setparams((channels, 2, SEC, 0, "NONE", "not compressed"))
        handle.writeframes(b"\0\0" * channels * duration_samples)


def _build_spanning_set(tmp_path: Path) -> Path:
    """Monta um set estilo make_set com clips que cruzam cortes, marcadores e ganho."""
    media: list[MediaAsset] = []
    for idx, channels in enumerate((1, 2)):
        wav = tmp_path / f"source{idx}.wav"
        _write_wav(wav, channels, 10 * SEC)
        media.append(
            MediaAsset(
                id=f"m{idx}",
                filename=wav.name,
                channels=channels,
                sample_rate=SEC,
                duration_samples=10 * SEC,
                status="resolved",
                resolved_path=str(wav),
            )
        )
    c0a = _clip("c0a", "t0", 0, 0, "Span", "m0", HOUR + 1 * SEC, HOUR + 5 * SEC, 0)
    end_tail = HOUR + 7 * SEC + 12 * FRAME_25
    c0b = _clip("c0b", "t0", 0, 1, "Tail", "m0", HOUR + 6 * SEC, end_tail, 0)
    t0 = TrackIR(
        id="t0",
        source_index=0,
        original_source_indices=[101],
        name="Track 0",
        kind="audio",
        channel_count=1,
        clips=[c0a, c0b],
        gain_points=[(HOUR + 2 * SEC, 1.0), (HOUR + 2 * SEC + 12 * FRAME_25, 0.5)],
    )
    c1a = _clip(
        "c1a",
        "t1",
        0,
        0,
        "DeletedOnly",
        "m1",
        HOUR + 4 * SEC + 12 * FRAME_25,
        HOUR + 5 * SEC + 12 * FRAME_25,
        0,
    )
    t1 = TrackIR(
        id="t1",
        source_index=1,
        original_source_indices=[102],
        name="Track 1",
        kind="audio",
        channel_count=2,
        clips=[c1a],
    )
    session = SessionIR(
        name="Review",
        sample_rate=SEC,
        timecode_rate=RATE_25,
        start_timecode_samples=HOUR,
        tracks=[t0, t1],
        media=media,
        markers=[
            _marker("k1", "Nota B", HOUR + 2 * SEC + 10 * FRAME_25),
            _marker("k2", "Corte aqui", HOUR + 4 * SEC + 15 * FRAME_25),
        ],
    )
    set_path = tmp_path / "review.als"
    opts = WriteOptions(sample_rate=SEC, bpm=60, live_origin_samples=HOUR)
    write_session(session, Path("templates/live12.als"), set_path, opts, Report())
    return set_path


def _xml_track_clips(root) -> list[tuple[str, str | None, list[list[int]]]]:
    """Extrai nome, mute e intervalos absolutos de clips de cada AudioTrack."""
    rows: list[tuple[str, str | None, list[list[int]]]] = []
    for trk in root.findall("./LiveSet/Tracks/AudioTrack"):
        mute = get_value(trk, TRACK_FIELDS["mute"])
        events = trk.find(TRACK_FIELDS["arrangement_events"])
        ranges: list[list[int]] = []
        if events is not None:
            for clip in events.findall("AudioClip"):
                s_b = float(get_value(clip, "CurrentStart") or "0")
                e_b = float(get_value(clip, "CurrentEnd") or "0")
                s_pos = HOUR + _beats_to_samples(s_b, SEC, 60.0)
                e_pos = HOUR + _beats_to_samples(e_b, SEC, 60.0)
                ranges.append([s_pos, e_pos])
        rows.append((track_name(trk), mute, ranges))
    return rows


def _xml_locators(root) -> list[tuple[str, int, str | None]]:
    """Lista (nome, sample_pos, IsSongStart) dos locators na ordem do XML."""
    out: list[tuple[str, int, str | None]] = []
    for loc in root.findall(".//LiveSet/Locators/Locators/Locator"):
        name = get_value(loc, "Name") or ""
        beats = float(get_value(loc, "Time") or "0")
        out.append(
            (name, HOUR + _beats_to_samples(beats, SEC, 60.0), get_value(loc, "IsSongStart"))
        )
    return out


def _edl_diff(tmp_path: Path, old_text: str, new_text: str) -> ChangeList:
    """Grava dois EDLs a 25 fps, le via cut_read e calcula o ChangeList."""
    old_p = tmp_path / "old.edl"
    new_p = tmp_path / "new.edl"
    old_p.write_text(old_text, encoding="utf-8")
    new_p.write_text(new_text, encoding="utf-8")
    old_cut = read_cut(old_p, Report(), frame_rate=RATE_25)
    new_cut = read_cut(new_p, Report(), frame_rate=RATE_25)
    assert old_cut is not None and new_cut is not None
    cl = diff_cuts(old_cut, new_cut, Report())
    assert cl is not None
    return cl


def _assert_e2e_sidecar(out_path: Path, orig_sidecar) -> None:
    """Confere clip_ranges, track_bindings e preservacao de hashes no sidecar novo."""
    out_anchor = read_anchor(out_path)
    expected_ranges = [
        {
            "track": "Track 0",
            "ranges": [
                [HOUR + 1 * SEC, HOUR + 2 * SEC],
                [HOUR + 3 * SEC, HOUR + 5 * SEC],
                [HOUR + 5 * SEC, HOUR + 6 * SEC + 12 * FRAME_25],
            ],
        },
        {"track": "Track 0 [saiu]", "ranges": [[HOUR + 5 * SEC, HOUR + 6 * SEC]]},
        {"track": "Track 1", "ranges": []},
        {
            "track": "Track 1 [saiu]",
            "ranges": [[HOUR + 5 * SEC + 12 * FRAME_25, HOUR + 6 * SEC + 12 * FRAME_25]],
        },
    ]
    assert out_anchor.clip_ranges == expected_ranges
    assert out_anchor.clip_origins is None
    assert out_anchor.media_hashes == orig_sidecar.media_hashes
    # A copia conformada tem uma track [saiu] depois de cada track de origem:
    # cada mapa do sidecar leva uma entrada por AudioTrack, na ordem do XML.
    assert out_anchor.track_channels == {
        **(orig_sidecar.track_channels or {}),
        "Track 0 [saiu]": 1,
        "Track 1 [saiu]": 2,
    }
    assert out_anchor.audio_source_indices == [[101], [], [102], []]
    tracks = load_als(out_path).findall("./LiveSet/Tracks/AudioTrack")
    ids = {track_name(t): t.get("Id") for t in tracks}
    orig_bindings = orig_sidecar.track_bindings or []
    assert out_anchor.track_bindings == [
        {**orig_bindings[0], "index": 0},
        {
            "index": 1,
            "track_name": "Track 0 [saiu]",
            "live_track_id": ids["Track 0 [saiu]"],
            "channel_count": 1,
            "reconform_saiu": True,
            "source_track": "Track 0",
        },
        {**orig_bindings[1], "index": 2},
        {
            "index": 3,
            "track_name": "Track 1 [saiu]",
            "live_track_id": ids["Track 1 [saiu]"],
            "channel_count": 2,
            "reconform_saiu": True,
            "source_track": "Track 1",
        },
    ]


def _assert_e2e_locators(out_root) -> None:
    """Confere ancora TC, locators movidos, [saiu] e novos marcadores RC."""
    locs = _xml_locators(out_root)
    assert ("TC 01:00:00:00 @ 48000 @ 25", HOUR, "true") in locs
    assert ("01:00:00:00", HOUR, "false") in locs
    assert ("Nota B", HOUR + 3 * SEC + 10 * FRAME_25, "false") in locs
    assert ("Corte aqui [saiu]", HOUR + 5 * SEC, "false") in locs
    rc_locs = [(name, pos) for name, pos, _ in locs if name.startswith("RC ")]
    assert rc_locs == [
        ("RC insert 01:00:02:00", HOUR + 2 * SEC),
        ("RC gap 01:00:02:00", HOUR + 2 * SEC),
        ("RC gap 01:00:03:00", HOUR + 3 * SEC),
        ("RC delete 01:00:04:00", HOUR + 5 * SEC),
    ]
    times = [pos for _, pos, _ in locs]
    assert times == sorted(times)


def test_apply_reconform_end_to_end_edl_25fps(tmp_path: Path) -> None:
    """Fluxo completo a partir de EDLs 25 fps, checando posicoes manuais e sidecar."""
    pytest.importorskip("opentimelineio")
    cl = _edl_diff(tmp_path, _OLD_EDL_25, _NEW_EDL_25)
    set_path = _build_spanning_set(tmp_path)
    sidecar_p = sidecar_path_for(set_path)
    orig_als_sha = _sha256(set_path)
    orig_sidecar_sha = _sha256(sidecar_p)
    orig_sidecar = read_anchor(set_path)

    res = apply_reconform(set_path, cl, Report())
    assert res == ApplyResult(
        output=tmp_path / "review reconform.als",
        written=True,
        clips_moved=1,
        clips_sliced=1,
        saiu_tracks=2,
        locators_added=4,
    )
    assert res.output is not None and res.output.exists()
    assert _sha256(set_path) == orig_als_sha
    assert _sha256(sidecar_p) == orig_sidecar_sha

    out_root = load_als(res.output)
    assert _xml_track_clips(out_root) == [
        (
            "Track 0",
            "true",
            [
                [HOUR + 1 * SEC, HOUR + 2 * SEC],
                [HOUR + 3 * SEC, HOUR + 5 * SEC],
                [HOUR + 5 * SEC, HOUR + 6 * SEC + 12 * FRAME_25],
            ],
        ),
        ("Track 0 [saiu]", "false", [[HOUR + 5 * SEC, HOUR + 6 * SEC]]),
        ("Track 1", "true", []),
        (
            "Track 1 [saiu]",
            "false",
            [[HOUR + 5 * SEC + 12 * FRAME_25, HOUR + 6 * SEC + 12 * FRAME_25]],
        ),
    ]
    _assert_e2e_locators(out_root)
    _assert_e2e_sidecar(res.output, orig_sidecar)
    roundtrip = read_session(res.output, Report())
    assert [t.muted for t in roundtrip.tracks] == [False, True, False, True]
    assert [p[0] for p in roundtrip.tracks[0].gain_points] == [
        HOUR + 3 * SEC,
        HOUR + 3 * SEC + 12 * FRAME_25,
    ]


def test_apply_reconform_move_trim_and_clip_origins_dropped(tmp_path: Path) -> None:
    """Gera RC para move/trim_head/trim_tail sem shift e descarta clip_origins."""
    pytest.importorskip("opentimelineio")
    cl = _edl_diff(tmp_path, _OLD_TRIM_EDL, _NEW_TRIM_EDL)
    assert cl.counts.get("shift", 0) > 0
    set_path = make_set(tmp_path)
    sidecar_p = sidecar_path_for(set_path)
    payload = json.loads(sidecar_p.read_text(encoding="utf-8"))
    payload["clip_origins"] = [{"track": "Track 0", "clips": []}]
    sidecar_p.write_text(json.dumps(payload), encoding="utf-8")

    report = Report(strict=False)
    out_custom = tmp_path / "custom.als"
    res = apply_reconform(set_path, cl, report, output=out_custom)
    assert res.written is True
    assert res.output == out_custom
    assert any(iss.code == "RECONFORM_SIDECAR_FIELD_DROPPED" for iss in report.issues)
    assert read_anchor(out_custom).clip_origins is None

    locs = _xml_locators(load_als(out_custom))
    rc_names = [name for name, _, _ in locs if name.startswith("RC ")]
    assert "RC move 01:00:00:00" in rc_names
    assert "RC trim_head 01:00:00:00" in rc_names
    assert "RC trim_tail 01:00:05:10" in rc_names
    assert not any(name.startswith("RC shift ") for name in rc_names)


def test_apply_reconform_without_sidecar_writes_new_sidecar(tmp_path: Path) -> None:
    """Set sem sidecar usa locator TC ou start_tc e grava sidecar novo na copia."""
    set_path = make_set(tmp_path)
    sidecar_path_for(set_path).unlink()
    tm = TimeMap(RATE_25, [Segment(90000, 90100, 90025)], [], [Lacuna(90000, 90025)], 25, 25)
    cl = ChangeList("old.edl", "new.edl", RATE_25, [], {}, False, tm)

    res = apply_reconform(set_path, cl, Report(strict=False))
    assert res.written is True
    assert res.output is not None and sidecar_path_for(res.output).exists()
    out_anchor = read_anchor(res.output)
    assert out_anchor.live_origin_samples == HOUR
    assert out_anchor.clip_ranges == [
        {"track": "Track 0", "ranges": [[HOUR + 2 * SEC, HOUR + 3 * SEC]]},
        {"track": "Track 1", "ranges": [[HOUR + 2 * SEC, HOUR + 3 * SEC]]},
    ]


def test_apply_reconform_apaga_a_copia_se_o_sidecar_falha(tmp_path: Path, monkeypatch) -> None:
    """Sidecar que nao grava deixa a pasta sem o .als copiado nem o sidecar."""
    set_path = make_set(tmp_path)
    tm = TimeMap(RATE_25, [Segment(90000, 90100, 90025)], [], [Lacuna(90000, 90025)], 25, 25)
    cl = ChangeList("old.edl", "new.edl", RATE_25, [], {}, False, tm)

    def _falha(als_path, anchor) -> None:
        raise OSError("sem espaco em disco")

    monkeypatch.setattr("aafton.reconform_apply.write_sidecar", _falha)
    report = Report(strict=False)
    res = apply_reconform(set_path, cl, report)
    assert res.written is False
    assert not (tmp_path / "review reconform.als").exists()
    assert not sidecar_path_for(tmp_path / "review reconform.als").exists()
    assert any(iss.code == "WRITE_ERROR" for iss in report.issues)
    assert set_path.exists()


def test_apply_reconform_refuses_ambiguous_and_bad_outputs(tmp_path: Path) -> None:
    """Recusa ChangeList ambiguo, pasta diferente e destino ja existente."""
    set_path = make_set(tmp_path)
    orig_als = _sha256(set_path)
    orig_json = _sha256(sidecar_path_for(set_path))
    ev_old = CutEvent(0, "clip", "A", None, 0, 25, 90000, 90025)
    ev_new_a = CutEvent(0, "clip", "A", None, 0, 25, 90000, 90025)
    ev_new_b = CutEvent(1, "clip", "A", None, 0, 25, 90025, 90050)
    amb_cl = diff_cuts(
        CutIR("o.edl", RATE_25, 90000, [ev_old]),
        CutIR("n.edl", RATE_25, 90000, [ev_new_a, ev_new_b]),
        Report(),
    )
    assert amb_cl is not None

    rep_amb = Report(strict=False)
    assert apply_reconform(set_path, amb_cl, rep_amb).written is False
    assert any(iss.code == "RECONFORM_AMBIGUOUS" for iss in rep_amb.issues)
    assert not (tmp_path / "review reconform.als").exists()
    with pytest.raises(StrictModeError):
        apply_reconform(set_path, amb_cl, Report(strict=True))

    clean_cl = ChangeList("o.edl", "n.edl", RATE_25, [], {}, False, TimeMap(RATE_25))
    outside = tmp_path / "other" / "out.als"
    outside.parent.mkdir()
    rep_dir = Report(strict=False)
    assert apply_reconform(set_path, clean_cl, rep_dir, output=outside).written is False
    assert any(iss.code == "LOCATORS_OUTPUT_FOLDER" for iss in rep_dir.issues)
    assert not outside.exists()

    rep_exists = Report(strict=False)
    assert apply_reconform(set_path, clean_cl, rep_exists, output=set_path).written is False
    assert any(iss.code == "OUTPUT_EXISTS" for iss in rep_exists.issues)
    assert _sha256(set_path) == orig_als
    assert _sha256(sidecar_path_for(set_path)) == orig_json


def _make_guard_set(
    folder: Path,
    clip_start: int,
    gain_points: list[tuple[int, float]],
    markers: list[MarkerIR],
) -> Path:
    """Cria set enxuto para testar a guarda de posicao antes da origem."""
    folder.mkdir(parents=True, exist_ok=True)
    wav = folder / "guard.wav"
    _write_wav(wav, 1, 5 * SEC)
    asset = _asset("m0", wav.name, 1, SEC, 5 * SEC, "resolved", str(wav), str(wav))
    clip = _clip("c0", "t0", 0, 0, "Safe", "m0", clip_start, clip_start + SEC, 0)
    track = _track("t0", 0, [101], "Track 0", "audio", 1, clips=[clip], gain_points=gain_points)
    sess = _session("Guard", SEC, RATE_25, HOUR, tracks=[track], media=[asset], markers=markers)
    path = folder / "guard.als"
    opts = _options(SEC, 60, HOUR)
    write_session(sess, Path("templates/live12.als"), path, opts, Report())
    return path


def test_apply_reconform_refuses_before_origin(tmp_path: Path) -> None:
    """Recusa clip, envelope nao-sentinela ou locator que mapeie antes da origem."""
    early_tm = TimeMap(RATE_25, [Segment(90000, 90200, 89950)], [], [], -50, -50)
    early_cl = ChangeList("o.edl", "n.edl", RATE_25, [], {}, False, early_tm)

    clip_set = _make_guard_set(tmp_path / "c", HOUR + 1 * SEC, [], [])
    rep_clip = Report(strict=False)
    assert apply_reconform(clip_set, early_cl, rep_clip).written is False
    assert any(iss.code == "RECONFORM_BEFORE_ORIGIN" for iss in rep_clip.issues)
    assert not (clip_set.parent / "guard reconform.als").exists()

    env_set = _make_guard_set(tmp_path / "e", HOUR + 3 * SEC, [(HOUR + 1 * SEC, 0.8)], [])
    rep_env = Report(strict=False)
    assert apply_reconform(env_set, early_cl, rep_env).written is False
    assert any(iss.code == "RECONFORM_BEFORE_ORIGIN" for iss in rep_env.issues)
    assert not (env_set.parent / "guard reconform.als").exists()

    seg = Segment(90050, 90200, 90050)
    saiu = Saiu(90020, 90040, 89980)
    saiu_tm = TimeMap(RATE_25, [seg], [saiu], [], 0, 0)
    saiu_cl = ChangeList("o.edl", "n.edl", RATE_25, [], {}, False, saiu_tm)
    marker = _marker("k", "Cedo", HOUR + 1 * SEC)
    loc_set = _make_guard_set(tmp_path / "l", HOUR + 3 * SEC, [], [marker])
    rep_loc = Report(strict=False)
    assert apply_reconform(loc_set, saiu_cl, rep_loc).written is False
    assert any(iss.code == "RECONFORM_BEFORE_ORIGIN" for iss in rep_loc.issues)
    assert not (loc_set.parent / "guard reconform.als").exists()
