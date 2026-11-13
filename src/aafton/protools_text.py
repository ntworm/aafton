"""Pro Tools Session Text Export Parser (Independent Oracle).

Parses text exports from Avid Pro Tools (Digidesign) to provide an independent
oracle for verifying AAF conversions against native Pro Tools timeline data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Optional


from aafton.timecode import TimecodeRate, label_to_frames


def parse_timecode_to_samples(
    tc_str: str,
    rate: TimecodeRate,
    sample_rate: int = 48000,
) -> int:
    """Converts a Pro Tools timecode string (e.g. '01:00:32:08.00', '01:00:39:22.100')
    to absolute integer samples.

    Pro Tools subframes represent hundredths of a frame (.00 to .100).
    Notice .100 is 1.00 full frame (100 / 100).
    """
    tc_str = tc_str.strip()
    if "." in tc_str:
        label_part, sub_str = tc_str.split(".", 1)
        sub = float(sub_str) / 100.0
    else:
        label_part = tc_str
        sub = 0.0

    frames = label_to_frames(label_part, rate)
    total_frames = frames + sub
    return round(total_frames * sample_rate * rate.den / rate.num)


@dataclass
class ProToolsEventText:
    """Represents a single audio event or fade in a Pro Tools track channel."""

    channel: int
    event_num: int
    clip_name: str
    start_time: str
    end_time: str
    duration: str
    timestamp: str = ""
    state: str = ""
    start_samples: int = 0
    end_samples: int = 0
    duration_samples: int = 0

    @property
    def is_fade(self) -> bool:
        name = self.clip_name.strip()
        return name.startswith("(") and name.endswith(")")

    @property
    def is_fade_in(self) -> bool:
        return self.clip_name.strip().lower() == "(fade in)"

    @property
    def is_fade_out(self) -> bool:
        return self.clip_name.strip().lower() == "(fade out)"

    @property
    def is_crossfade(self) -> bool:
        name = self.clip_name.strip().lower()
        return "cross" in name or name == "(cross fade)"


@dataclass
class ProToolsTrackText:
    """Represents an audio track from the Pro Tools text export."""

    name: str
    comments: str = ""
    user_delay: str = ""
    state: str = ""
    plugins: list[str] = field(default_factory=list)
    channels: dict[int, list[ProToolsEventText]] = field(default_factory=dict)

    def events(self, channel: int = 1) -> list[ProToolsEventText]:
        """Returns all events (clips + fades) for the given channel."""
        return self.channels.get(channel, [])

    def clips(self, channel: int = 1) -> list[ProToolsEventText]:
        """Returns only non-fade clip events for the given channel."""
        return [e for e in self.events(channel) if not e.is_fade]

    def fades(self, channel: int = 1) -> list[ProToolsEventText]:
        """Returns only fade events for the given channel."""
        return [e for e in self.events(channel) if e.is_fade]

    @property
    def all_events(self) -> list[ProToolsEventText]:
        """Returns all events across all channels ordered by channel and event number."""
        result = []
        for ch in sorted(self.channels.keys()):
            result.extend(self.channels[ch])
        return result

    @property
    def all_clips(self) -> list[ProToolsEventText]:
        """Returns all non-fade clips across all channels."""
        return [e for e in self.all_events if not e.is_fade]

    @property
    def all_fades(self) -> list[ProToolsEventText]:
        """Returns all fades across all channels."""
        return [e for e in self.all_events if e.is_fade]


@dataclass
class ProToolsMarkerText:
    """Represents a marker / memory location from the Pro Tools text export."""

    num: int
    location: str
    time_reference_samples: int
    units: str
    name: str
    track_name: str
    track_type: str
    comments: str


@dataclass
class ProToolsTextExport:
    """Parsed Pro Tools Session Text Export."""

    session_name: str
    sample_rate: int
    bit_depth: str
    start_timecode: str
    timecode_format: str
    num_tracks: int = 0
    num_clips: int = 0
    num_files: int = 0
    tracks: dict[str, ProToolsTrackText] = field(default_factory=dict)
    markers: list[ProToolsMarkerText] = field(default_factory=list)

    @property
    def timecode_rate(self) -> TimecodeRate:
        drop = "Drop Frame" in self.timecode_format or ";" in self.start_timecode
        if "23.976" in self.timecode_format:
            return TimecodeRate(24000, 1001, drop)
        elif "29.97" in self.timecode_format:
            return TimecodeRate(30000, 1001, drop)
        elif "59.94" in self.timecode_format:
            return TimecodeRate(60000, 1001, drop)

        match = re.search(r"(\d+)", self.timecode_format)
        if match:
            return TimecodeRate(int(match.group(1)), 1, drop)
        return TimecodeRate(25, 1, False)

    @classmethod
    def parse_file(cls, path: str | Path) -> ProToolsTextExport:
        """Parses a Pro Tools session text export file using UTF-8 encoding."""
        with open(path, "r", encoding="utf-8") as f:
            return cls.parse(f.read())

    @classmethod
    def parse(cls, content: str) -> ProToolsTextExport:
        """Parses Pro Tools session text export content."""
        lines = content.splitlines()

        # 1. Parse header
        header_map: dict[str, str] = {}
        for line in lines[:25]:
            if ":" in line:
                k, v = line.split(":", 1)
                header_map[k.strip()] = v.strip()

        session_name = header_map.get("SESSION NAME", "")
        sample_rate_raw = header_map.get("SAMPLE RATE", "48000.000000")
        try:
            sample_rate = round(float(sample_rate_raw))
        except ValueError:
            sample_rate = 48000
        bit_depth = header_map.get("BIT DEPTH", "24-bit")
        start_timecode = header_map.get("SESSION START TIMECODE", "00:00:00:00.00")
        timecode_format = header_map.get("TIMECODE FORMAT", "25 Frame")

        def _parse_int_header(key: str) -> int:
            val = header_map.get(key, "0")
            try:
                return int(val)
            except ValueError:
                return 0

        num_tracks = _parse_int_header("# OF AUDIO TRACKS")
        num_clips = _parse_int_header("# OF AUDIO CLIPS")
        num_files = _parse_int_header("# OF AUDIO FILES")

        drop = "Drop Frame" in timecode_format or ";" in start_timecode
        if "23.976" in timecode_format:
            rate = TimecodeRate(24000, 1001, drop)
        elif "29.97" in timecode_format:
            rate = TimecodeRate(30000, 1001, drop)
        elif "59.94" in timecode_format:
            rate = TimecodeRate(60000, 1001, drop)
        else:
            fps_match = re.search(r"(\d+)", timecode_format)
            fps_val = int(fps_match.group(1)) if fps_match else 25
            rate = TimecodeRate(fps_val, 1, drop)

        # 2. Parse TRACK LISTING section
        tracks: dict[str, ProToolsTrackText] = {}
        tl_match = re.search(
            r"T R A C K\s+L I S T I N G\n(.*?)(?=\n\s*\n\s*M A R K E R S\s+L I S T I N G|\Z)",
            content,
            re.DOTALL,
        )
        if tl_match:
            tl_text = tl_match.group(1)
            # Split by TRACK NAME:
            track_blocks = re.split(r"\nTRACK NAME:\t", "\n" + tl_text)
            for block in track_blocks:
                if not block.strip():
                    continue
                block_lines = block.strip().splitlines()
                raw_track_name = block_lines[0].strip()

                comments = ""
                user_delay = ""
                state = ""
                plugins: list[str] = []
                channels: dict[int, list[ProToolsEventText]] = {}

                in_events = False
                for blk_line in block_lines[1:]:
                    line_str = blk_line.strip()
                    if not line_str:
                        continue

                    if blk_line.startswith("COMMENTS:\t"):
                        comments = blk_line.split("COMMENTS:\t", 1)[1].strip()
                        continue
                    elif blk_line.startswith("USER DELAY:\t"):
                        user_delay = blk_line.split("USER DELAY:\t", 1)[1].strip()
                        continue
                    elif blk_line.startswith("STATE:\t") or blk_line.startswith("STATE:"):
                        state = blk_line.split("STATE:", 1)[1].strip()
                        continue
                    elif blk_line.startswith("PLUG-INS:\t") or blk_line.startswith("PLUG-INS:"):
                        p_part = blk_line.split("PLUG-INS:", 1)[1].strip()
                        if p_part:
                            plugins = [p.strip() for p in p_part.split("\t") if p.strip()]
                        continue

                    if line_str.startswith("CHANNEL") and "EVENT" in line_str:
                        in_events = True
                        continue

                    if in_events:
                        cols = [c.strip() for c in re.split(r"\t+", blk_line)]
                        if len(cols) >= 6 and cols[0].isdigit() and cols[1].isdigit():
                            chan = int(cols[0])
                            event_num = int(cols[1])
                            clip_name = cols[2]
                            start_time = cols[3]
                            end_time = cols[4]
                            duration = cols[5]
                            timestamp = cols[6] if len(cols) > 6 else ""
                            evt_state = cols[7] if len(cols) > 7 else ""

                            start_s = parse_timecode_to_samples(
                                start_time, rate=rate, sample_rate=sample_rate
                            )
                            end_s = parse_timecode_to_samples(
                                end_time, rate=rate, sample_rate=sample_rate
                            )
                            dur_s = parse_timecode_to_samples(
                                duration, rate=rate, sample_rate=sample_rate
                            )

                            ev = ProToolsEventText(
                                channel=chan,
                                event_num=event_num,
                                clip_name=clip_name,
                                start_time=start_time,
                                end_time=end_time,
                                duration=duration,
                                timestamp=timestamp,
                                state=evt_state,
                                start_samples=start_s,
                                end_samples=end_s,
                                duration_samples=dur_s,
                            )
                            channels.setdefault(chan, []).append(ev)

                tracks[raw_track_name] = ProToolsTrackText(
                    name=raw_track_name,
                    comments=comments,
                    user_delay=user_delay,
                    state=state,
                    plugins=plugins,
                    channels=channels,
                )

        # 3. Parse MARKERS LISTING section
        markers: list[ProToolsMarkerText] = []
        ml_match = re.search(r"M A R K E R S\s+L I S T I N G\n(.*?)(?=\Z)", content, re.DOTALL)
        if ml_match:
            ml_text = ml_match.group(1)
            ml_lines = ml_text.splitlines()
            current_marker: Optional[dict] = None
            for ml_line in ml_lines:
                if not ml_line.strip() or ml_line.strip().startswith("#"):
                    continue

                cols = [c.strip() for c in ml_line.split("\t")]
                if cols and cols[0].isdigit():
                    # If we had a previous marker, finish it
                    if current_marker is not None:
                        markers.append(
                            ProToolsMarkerText(
                                num=current_marker["num"],
                                location=current_marker["location"],
                                time_reference_samples=current_marker["time_reference"],
                                units=current_marker["units"],
                                name=current_marker["name"],
                                track_name=current_marker["track_name"],
                                track_type=current_marker["track_type"],
                                comments="\n".join(current_marker["comments"]),
                            )
                        )

                    num = int(cols[0])
                    loc = cols[1] if len(cols) > 1 else ""
                    time_ref = int(cols[2]) if len(cols) > 2 and cols[2].isdigit() else 0
                    units = cols[3] if len(cols) > 3 else ""
                    name = cols[4] if len(cols) > 4 else ""
                    track_name = cols[5] if len(cols) > 5 else ""
                    track_type = cols[6] if len(cols) > 6 else ""
                    comm = cols[7] if len(cols) > 7 else ""

                    current_marker = {
                        "num": num,
                        "location": loc,
                        "time_reference": time_ref,
                        "units": units,
                        "name": name,
                        "track_name": track_name,
                        "track_type": track_type,
                        "comments": [comm] if comm else [],
                    }
                elif current_marker is not None:
                    # Multiline comment continuation
                    extra_c = cols[-1] if cols else ml_line.strip()
                    if extra_c:
                        current_marker["comments"].append(extra_c)

            if current_marker is not None:
                markers.append(
                    ProToolsMarkerText(
                        num=current_marker["num"],
                        location=current_marker["location"],
                        time_reference_samples=current_marker["time_reference"],
                        units=current_marker["units"],
                        name=current_marker["name"],
                        track_name=current_marker["track_name"],
                        track_type=current_marker["track_type"],
                        comments="\n".join(current_marker["comments"]),
                    )
                )

        return cls(
            session_name=session_name,
            sample_rate=sample_rate,
            bit_depth=bit_depth,
            start_timecode=start_timecode,
            timecode_format=timecode_format,
            num_tracks=num_tracks,
            num_clips=num_clips,
            num_files=num_files,
            tracks=tracks,
            markers=markers,
        )
