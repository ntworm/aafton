from __future__ import annotations

from pathlib import Path

from aafton.ir import MarkerIR, SessionIR
from aafton.protools_text import ProToolsTextExport
from aafton.report import Report


def find_session_text(aaf_path: Path, explicit_text: str | None) -> Path | None | list[Path]:
    if explicit_text:
        return Path(explicit_text).resolve()

    candidates = []
    dir_path = aaf_path.parent
    if dir_path.exists() and dir_path.is_dir():
        for txt_file in dir_path.glob("*.txt"):
            try:
                with open(txt_file, "r", encoding="utf-8") as f:
                    first_line = f.readline().strip()
                    if first_line.startswith("SESSION NAME:"):
                        candidates.append(txt_file.resolve())
            except Exception:
                pass

    if len(candidates) > 1:
        return candidates
    if len(candidates) == 1:
        return candidates[0]
    return None


def process_session_text(
    session: SessionIR, aaf_path: Path, explicit_text: str | None, report: Report
) -> None:
    text_path_or_candidates = find_session_text(aaf_path, explicit_text)

    if isinstance(text_path_or_candidates, list):
        if len(text_path_or_candidates) > 1:
            report.error(
                "SESSION_TEXT_MULTIPLE_CANDIDATES",
                "Multiple text exports found; specify which one to use with --session-text",
                {"candidates": [str(c) for c in text_path_or_candidates]},
            )
            return

    text_path = text_path_or_candidates
    if not text_path or not text_path.exists():
        return

    try:
        pt_text = ProToolsTextExport.parse_file(text_path)
    except Exception as exc:
        report.error(
            "SESSION_TEXT_PARSE_ERROR", f"Error reading text: {exc}", {"path": str(text_path)}
        )
        return

    # Validate session match
    if pt_text.sample_rate != session.sample_rate:
        report.error(
            "SESSION_TEXT_MISMATCH",
            f"The sample rate of the text ({pt_text.sample_rate}) does not match the one of the AAF ({session.sample_rate}).",
            {},
        )
        return

    aaf_rate = session.timecode_rate
    txt_rate = pt_text.timecode_rate

    if (aaf_rate.nominal_fps != txt_rate.nominal_fps) or (
        aaf_rate.drop_frame != txt_rate.drop_frame
    ):
        report.error(
            "SESSION_TEXT_MISMATCH",
            f"The timecode of the text (nominal {txt_rate.nominal_fps}, drop={txt_rate.drop_frame}) does not match the one of the AAF (nominal {aaf_rate.nominal_fps}, drop={aaf_rate.drop_frame}).",
            {},
        )
        return

    if (aaf_rate.num, aaf_rate.den) != (txt_rate.num, txt_rate.den):
        report.info(
            "TIMECODE_RATE_FROM_TEXT",
            f"The AAF declares a nominal rate of {aaf_rate.nominal_fps} fps (drop={aaf_rate.drop_frame}), but the text specifies the exact rate {txt_rate.num}/{txt_rate.den}. The rate of the text will be adopted.",
        )
        session.timecode_rate = txt_rate

    markers_with_comments = 0
    for pt_marker in pt_text.markers:
        name = pt_marker.name
        comments_lines = [line.strip() for line in pt_marker.comments.splitlines() if line.strip()]

        found_existing = False
        for existing in session.markers:
            if (
                existing.position_samples == pt_marker.time_reference_samples
                and existing.name == pt_marker.name
            ):
                if comments_lines:
                    existing.annotation = "\n".join(comments_lines)
                    markers_with_comments += 1
                found_existing = True
                break

        if found_existing:
            continue

        full_comment = None
        if comments_lines:
            full_comment = "\n".join(comments_lines)
            markers_with_comments += 1
            if name:
                name = f"{name} — {comments_lines[0]}"
            else:
                name = comments_lines[0]

        # Handle anchor collision
        if name.startswith("TC "):
            name = f"{name} (imported)"
            report.warning(
                "MARKER_RENAMED_TO_AVOID_ANCHOR",
                "Marker renamed to avoid colliding with an anchoring locator",
                {"name": name},
            )

        # Disambiguate with track if exact same pos and name
        for existing in session.markers:
            if (
                existing.position_samples == pt_marker.time_reference_samples
                and existing.name == name
            ):
                name = f"{name} ({pt_marker.track_name})"
                break

        # Append marker
        marker = MarkerIR(
            id=f"pt_marker_{pt_marker.num}",
            name=name,
            position_samples=pt_marker.time_reference_samples,
            comment=full_comment,
        )
        session.markers.append(marker)

    if markers_with_comments > 0:
        report.info(
            "MARKER_COMMENT_IN_ANNOTATION",
            f"{markers_with_comments} markers had their comment kept in the annotation",
            {"count": markers_with_comments},
        )

    missing_issues = [i for i in report.issues if i.code == "TRACK_NAME_MISSING"]
    if missing_issues and pt_text.tracks:
        text_tracks = list(pt_text.tracks.keys())
        aaf_idx = 0
        renamed_count = 0
        for text_name in text_tracks:
            channels = 1
            base_name = text_name

            import re

            m = re.search(r"\s*\((Stereo|Quad|5\.1)\)$", text_name)
            if m:
                fmt = m.group(1)
                if fmt == "Stereo":
                    channels = 2
                elif fmt == "Quad":
                    channels = 4
                elif fmt == "5.1":
                    channels = 6
                base_name = text_name[: m.start()].strip()

            suffixes = []
            if channels == 2:
                suffixes = ["[L]", "[R]"]
            elif channels == 4:
                suffixes = ["[L]", "[R]", "[Ls]", "[Rs]"]
            elif channels == 6:
                suffixes = ["[L]", "[R]", "[C]", "[LFE]", "[Ls]", "[Rs]"]

            for c in range(channels):
                if aaf_idx < len(session.tracks):
                    t = session.tracks[aaf_idx]
                    suffix = suffixes[c] if suffixes else ""
                    t.name = f"{base_name} {suffix}".strip()
                    renamed_count += 1
                    aaf_idx += 1

        if renamed_count > 0:
            report.info(
                "TRACK_NAMES_FROM_TEXT", f"{renamed_count} track names recovered from the text", {}
            )
            report.issues = [i for i in report.issues if i.code != "TRACK_NAME_MISSING"]

    import re

    pt_base_to_state = {}
    pt_base_to_plugins = {}
    for pt_name, pt_track in pt_text.tracks.items():
        base = re.sub(r"\s*\([^)]*\)$", "", pt_name).strip()
        pt_base_to_state[base] = pt_track.state
        if getattr(pt_track, "plugins", None):
            pt_base_to_plugins[base] = pt_track.plugins

    tracks_with_plugins = 0
    for track in session.tracks:
        base = re.sub(r"\s*\[[^]]*\]$", "", track.name).strip()

        # 1. Map state
        if base in pt_base_to_state:
            state = pt_base_to_state[base]
            if state:
                state_words = state.split()
                if "Inactive" in state_words:
                    track.muted = True
                    report.info(
                        "TRACK_INACTIVE_AS_MUTED",
                        f"Track {track.name} was Inactive in Pro Tools and was muted",
                        {"track": track.name},
                    )
                elif "Muted" in state_words:
                    track.muted = True

        # 2. Map plugins
        if base in pt_base_to_plugins and pt_base_to_plugins[base]:
            plugin_lines = ["Plugins in Pro Tools:"] + pt_base_to_plugins[base]
            track.annotation = "\n".join(plugin_lines)
            tracks_with_plugins += 1
        else:
            track.annotation = ""

    if tracks_with_plugins > 0:
        report.info(
            "TRACK_PLUGINS_NOTED",
            f"{tracks_with_plugins} tracks received an annotation with the plugin list",
            {"count": tracks_with_plugins},
        )
