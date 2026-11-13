import subprocess
import json
import urllib.parse
import re
import shutil
from pathlib import Path
from typing import Optional, Tuple, Union
import aaf2

from .ir import SessionIR
from .mxf import probe_arquivo, tem_video
from .report import Report
from .timecode import label_to_samples, samples_to_label, samples_per_frame


def probe_video_codec_fast(path: Path) -> Optional[str]:
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
            if b"avc1" in head:
                return "h264"
            elif b"hev1" in head:
                return "h265"
            elif b"apcn" in head or b"ap4h" in head:
                return "prores"
            elif b"dnxh" in head:
                return "dnxhd"
            elif path.suffix.lower() == ".mxf":
                return "mxf"
    except Exception:
        pass
    return None


def analyze_video(path: Path) -> Tuple[Optional[float], Optional[str], Optional[str]]:
    duration = None
    timecode = None
    codec = probe_video_codec_fast(path)

    if shutil.which("ffprobe"):
        try:
            cmd = [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
            ]
            res = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")
            if res.returncode == 0:
                data = json.loads(res.stdout)

                if "format" in data and "duration" in data["format"]:
                    duration = float(data["format"]["duration"])
                else:
                    for s in data.get("streams", []):
                        if "duration" in s:
                            duration = float(s["duration"])
                            break

                if "format" in data and "tags" in data["format"]:
                    timecode = data["format"]["tags"].get("timecode")
                if not timecode:
                    for s in data.get("streams", []):
                        if "tags" in s and "timecode" in s["tags"]:
                            timecode = s["tags"]["timecode"]
                            break

                if not codec:
                    for s in data.get("streams", []):
                        if s.get("codec_type") == "video":
                            codec = s.get("codec_name")
                            break
        except Exception:
            pass

    return duration, timecode, codec


def video_has_audio(video_path: Path) -> bool:
    """Retorna True se o arquivo contem pelo menos uma stream de audio, False se comprovadamente nao contem."""
    data = probe_arquivo(video_path)
    if data is None:
        return True
    streams = data.get("streams", [])
    if streams:
        return any(s.get("codec_type") == "audio" for s in streams)
    return False


def transcode_to_live_proxy(video_path: Path, report: Report) -> Optional[Path]:
    if not shutil.which("ffmpeg"):
        return None

    stem = video_path.stem
    base_stem = stem if stem.endswith("_LiveProxy") else f"{stem}_LiveProxy"
    proxy_path = video_path.parent / f"{base_stem}.mp4"

    if proxy_path.exists() and proxy_path.stat().st_size > 0:
        if video_has_audio(proxy_path):
            report.info(
                "VIDEO_PROXY_REUSED",
                f"Existing H.264 proxy reused: {proxy_path.name}",
                {"path": str(proxy_path)},
            )
            return proxy_path

    has_audio = video_has_audio(video_path)
    codec = probe_video_codec_fast(video_path)
    is_mp4_h264 = video_path.suffix.lower() == ".mp4" and codec == "h264"

    if not has_audio and is_mp4_h264:
        report.info(
            "VIDEO_PROXY_SILENT_AUDIO",
            f"Video {video_path.name} has no audio track; muxing silent audio compatible with Live...",
            {"source": str(video_path), "target": str(proxy_path)},
        )
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-shortest",
            str(proxy_path),
        ]
    else:
        report.info(
            "VIDEO_PROXY_TRANSCODING",
            f"Transcoding {video_path.name} to an H.264/AAC proxy compatible with Live on Windows...",
            {"source": str(video_path), "target": str(proxy_path)},
        )
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            *(
                ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
                if not has_audio
                else []
            ),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            *(["-shortest"] if not has_audio else []),
            str(proxy_path),
        ]
    try:
        res = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")
        if res.returncode == 0 and proxy_path.exists() and proxy_path.stat().st_size > 0:
            report.info(
                "VIDEO_PROXY_GENERATED",
                f"H.264 proxy generated: {proxy_path.name}",
                {"path": str(proxy_path)},
            )
            return proxy_path
        else:
            report.warning(
                "VIDEO_PROXY_FAILED",
                f"Failure to transcode the H.264 proxy with ffmpeg: {res.stderr[:200] if res.stderr else ''}",
                {},
            )
    except Exception as exc:
        report.warning("VIDEO_PROXY_ERROR", str(exc), {})
    return None


def _candidato_de_video(caminho: Path) -> bool:
    """True quando o arquivo da pasta pode ser o video da sessao.

    Um MXF so de audio nao e video: o ffprobe diz se ele tem stream de video.
    Sem ffprobe, o MXF fica de fora.
    """
    return caminho.is_file() and (caminho.suffix.lower() != ".mxf" or tem_video(caminho))


def find_video_file(aaf_path: Path, explicit_video: Optional[str]) -> Union[None, Path, list]:
    if explicit_video:
        return Path(explicit_video).resolve()

    raw_search_dirs = [
        aaf_path.parent,
        aaf_path.parent / "Video Files",
        aaf_path.parent.parent / "Video Files",
        aaf_path.parent.parent,
    ]
    seen_dirs = set()
    ordered_dirs = []
    for d in raw_search_dirs:
        try:
            res_d = d.resolve()
            if res_d.is_dir() and res_d not in seen_dirs:
                seen_dirs.add(res_d)
                ordered_dirs.append(res_d)
        except Exception:
            pass

    extensions = [".mp4", ".mov", ".mxf", ".m4v", ".MP4", ".MOV", ".MXF", ".M4V"]

    for d in ordered_dirs:
        achados = []
        for ext in extensions:
            achados.extend(d.glob(f"*{ext}"))
        dir_candidates = list(set([c.resolve() for c in achados if _candidato_de_video(c)]))
        if len(dir_candidates) == 1:
            return dir_candidates[0]
        elif len(dir_candidates) > 1:
            aaf_tokens = set(re.findall(r"[a-zA-Z0-9]+", aaf_path.stem.lower()))
            scored = []
            for cand in dir_candidates:
                cand_tokens = set(re.findall(r"[a-zA-Z0-9]+", cand.stem.lower()))
                score = len(aaf_tokens & cand_tokens)
                is_proxy = 1 if cand.name.endswith("_LiveProxy.mp4") else 0
                is_mp4 = 1 if cand.suffix.lower() == ".mp4" else 0
                scored.append(((score, is_proxy, is_mp4), cand))
            scored.sort(key=lambda x: x[0], reverse=True)
            if scored[0][0] > scored[1][0] and (scored[0][0][0] > 0 or scored[0][0][1] > 0):
                return scored[0][1]
            return dir_candidates

    try:
        with aaf2.open(str(aaf_path), "r") as f:
            for mob in f.content.mobs:
                if isinstance(mob, aaf2.mobs.SourceMob):
                    desc = getattr(mob, "descriptor", None)
                    if isinstance(desc, aaf2.essence.CDCIDescriptor):
                        locs = getattr(desc, "locator", [])
                        if locs:
                            v = locs[0].get("URLString")
                            url = v.value if hasattr(v, "value") else v
                            if url and url.startswith("file://"):
                                url = urllib.parse.unquote(url)
                                p = url[7:]
                                if p.startswith("/?/"):
                                    p = p[3:]
                                elif p.startswith("/"):
                                    p = p[1:]
                                p_path = Path(p)
                                if p_path.exists():
                                    return p_path.resolve()
    except Exception:
        pass

    return None


def _video_not_found(report: Report, explicit: Optional[str]) -> None:
    if explicit:
        report.warning("VIDEO_NOT_FOUND", f"video not found: {explicit}", {"path": str(explicit)})
    else:
        report.info("VIDEO_NOT_FOUND", "no video next to the AAF or referenced in the file", {})


def process_video(
    session: SessionIR,
    aaf_path: Path,
    explicit_video: Optional[str],
    explicit_start: Optional[str],
    report: Report,
    burn_timecode: bool = False,
) -> None:
    video_path_or_candidates = find_video_file(aaf_path, explicit_video)

    if isinstance(video_path_or_candidates, list):
        if len(video_path_or_candidates) > 1:
            report.error(
                "VIDEO_MULTIPLE_CANDIDATES",
                "Multiple videos found; specify which to use with --video",
                {"candidates": [str(c) for c in video_path_or_candidates]},
            )
            return

    video_path = video_path_or_candidates

    if not video_path or not video_path.exists():
        return _video_not_found(report, explicit_video)

    duration_sec, timecode_str, codec = analyze_video(video_path)
    original_video_path = video_path.resolve()

    has_audio = video_has_audio(video_path)

    # Check codec compatibility on Windows (or missing audio stream required by Live)
    needs_proxy = (
        not has_audio
        or codec in ["prores", "dnxhd", "mxf"]
        or (video_path.suffix.lower() == ".mov" and not video_path.name.endswith("_LiveProxy.mp4"))
    )
    if needs_proxy:
        proxy = transcode_to_live_proxy(video_path, report)
        if proxy is not None:
            video_path = proxy
            p_dur, p_tc, p_codec = analyze_video(video_path)
            if p_dur:
                duration_sec = p_dur
            if p_tc and not timecode_str:
                timecode_str = p_tc
            if p_codec:
                codec = p_codec
        else:
            report.warning(
                "VIDEO_CODEC_UNSUPPORTED",
                f"Live on Windows does not decode the codec {codec} natively. Transcode the video to H.264 (.mp4).",
                {"codec": codec},
            )

    # Find anchor from AAF
    aaf_start_sec = None
    aaf_duration_sec = None
    aaf_source_offset = 0
    try:
        with aaf2.open(str(aaf_path), "r") as f:
            compositions = [m for m in f.content.mobs if isinstance(m, aaf2.mobs.CompositionMob)]
            if compositions:
                comp = compositions[0]
                for slot in comp.slots:
                    seg = getattr(slot, "segment", None)
                    if getattr(seg, "media_kind", "") == "Picture":
                        from .timecode import exact_edit_rate
                        from .aaf_read import _source_filename, _source_offset_samples

                        rate = (
                            exact_edit_rate(slot.edit_rate)
                            if getattr(slot, "edit_rate", None)
                            else exact_edit_rate(24)
                        )
                        components = getattr(seg, "components", None) or [seg]
                        curr = 0
                        for c in components:
                            if isinstance(c, aaf2.components.Filler):
                                curr += c.length
                            elif isinstance(c, aaf2.components.NestedScope) or isinstance(
                                c, aaf2.components.SourceClip
                            ):
                                aaf_start_sec = curr / rate
                                aaf_duration_sec = c.length / rate
                                if isinstance(c, aaf2.components.SourceClip) and c.start:
                                    location = _source_filename(c)
                                    if (
                                        location
                                        and Path(location).name.casefold()
                                        == original_video_path.name.casefold()
                                    ):
                                        aaf_source_offset = _source_offset_samples(
                                            c, session.sample_rate, rate, report
                                        )
                                    else:
                                        report.error(
                                            "VIDEO_SOURCE_UNCONFIRMED",
                                            "AAF asks for a video trim, but the selected file was not identified as the source; confirm the consolidated video",
                                            {},
                                        )
                                break
    except Exception as exc:
        from .report import StrictModeError

        if isinstance(exc, StrictModeError):
            raise
        report.warning(
            "VIDEO_AAF_READ_FAILED", f"could not read the video geometry of the AAF: {exc}", {}
        )

    start_samples = None
    anchor_source = None

    if explicit_start:
        start_samples = int(
            label_to_samples(explicit_start, session.timecode_rate, session.sample_rate)
        )
        anchor_source = "VIDEO_ANCHOR_EXPLICIT"
    elif aaf_start_sec is not None:
        start_samples = session.start_timecode_samples + round(aaf_start_sec * session.sample_rate)
        anchor_source = "VIDEO_ANCHOR_FROM_AAF"
    elif timecode_str:
        tc_samples = int(label_to_samples(timecode_str, session.timecode_rate, session.sample_rate))
        ffoa_marker = next((m for m in session.markers if m.name == "FFOA"), None)

        if tc_samples == 0 and ffoa_marker is not None and ffoa_marker.position_samples != 0:
            # Timecode zero num arquivo de video quase nunca e uma declaracao:
            # e o preenchimento padrao. O ffmpeg escreve um stream `tmcd` com
            # 00:00:00:00 em todo MP4 que gera, e um proxy exportado sem
            # timecode fica indistinguivel de um que comeca de fato na hora
            # zero. Obedecer a esse zero joga a imagem para o inicio da
            # timeline e, pior, arrasta a origem da sessao junto, empurrando
            # todo o audio uma hora para a frente.
            #
            # O marcador FFOA e autoridade sobre onde a imagem comeca, entao
            # ele ganha do zero. Um timecode nao-zero no arquivo continua
            # ganhando, porque ai e informacao de verdade.
            start_samples = ffoa_marker.position_samples
            anchor_source = "VIDEO_ANCHOR_FROM_FFOA"
            ffoa_tc = samples_to_label(
                ffoa_marker.position_samples, session.timecode_rate, session.sample_rate
            )
            report.info(
                "VIDEO_TIMECODE_PLACEHOLDER",
                f"the video file carries no useful timecode (it declares 00:00:00:00, which is the "
                f"default filler), so the picture was anchored on the FFOA marker, at "
                f"{ffoa_tc}. Use --video-start if the video really starts at time zero.",
                {"ffoa_samples": ffoa_marker.position_samples, "ffoa_timecode": ffoa_tc},
            )
        elif tc_samples == 0:
            v_clean = video_path.stem.lower()
            if v_clean.endswith("_liveproxy"):
                v_clean = v_clean[:-10]
            matching_track = None
            for t in session.tracks:
                if t.is_video:
                    continue
                t_clean = t.name.lower()
                if v_clean in t_clean or t_clean in v_clean:
                    matching_track = t
                    break

            if matching_track and matching_track.clips:
                start_samples = min(c.timeline_start_samples for c in matching_track.clips)
                anchor_source = "VIDEO_ANCHOR_FROM_GUIDE_TRACK"
                guide_tc = samples_to_label(
                    start_samples, session.timecode_rate, session.sample_rate
                )
                report.info(
                    "VIDEO_ANCHOR_FROM_GUIDE_TRACK",
                    f"Video anchored from the guide track '{matching_track.name}' at {guide_tc} ({start_samples} samples)",
                    {"start_samples": start_samples, "track": matching_track.name},
                )
            else:
                start_samples = 0
                anchor_source = "VIDEO_ANCHOR_FROM_FILE"
        else:
            start_samples = tc_samples
            anchor_source = "VIDEO_ANCHOR_FROM_FILE"

        if anchor_source == "VIDEO_ANCHOR_FROM_FILE" and ffoa_marker is not None:
            diff_samples = abs(start_samples - ffoa_marker.position_samples)
            if diff_samples > float(samples_per_frame(session.timecode_rate, session.sample_rate)):
                diff_sec = diff_samples / session.sample_rate
                vid_tc = samples_to_label(start_samples, session.timecode_rate, session.sample_rate)
                ffoa_tc = samples_to_label(
                    ffoa_marker.position_samples, session.timecode_rate, session.sample_rate
                )
                report.warning(
                    "VIDEO_ANCHOR_DISAGREES_WITH_FFOA",
                    f"The video file asked for anchoring at {vid_tc} ({start_samples} samples), "
                    f"but the session has an FFOA marker at {ffoa_tc} ({ffoa_marker.position_samples} samples). "
                    f"Difference of {diff_sec:.3f} seconds. Use --video-start to solve it if the video is out of sync.",
                )

    if start_samples is None:
        start_samples = min(
            (c.timeline_start_samples for t in session.tracks for c in t.clips), default=0
        )
        anchor_source = "VIDEO_ANCHOR_ASSUMED"
        report.warning(anchor_source, "Presumed video anchoring from the first audio clip", {})
    else:
        report.info(anchor_source, f"Video anchored at {start_samples} samples", {})

    # Duration mismatch
    final_duration_sec = aaf_duration_sec or duration_sec or 3600.0
    if duration_sec is not None and aaf_duration_sec is not None:
        if abs(duration_sec - aaf_duration_sec) > (1.0 / 24.0):
            report.warning(
                "VIDEO_DURATION_MISMATCH",
                f"Divergent duration: AAF says {float(aaf_duration_sec):.3f}s, file says {duration_sec:.3f}s",
                {"aaf": float(aaf_duration_sec), "file": duration_sec},
            )

    from .video_track import add_video_track

    if burn_timecode:
        from .video_burn import burn_for_live

        video_path = burn_for_live(
            original_video_path,
            video_path,
            start_samples - aaf_source_offset,
            session,
            report,
        )

    add_video_track(
        session,
        video_path,
        start_samples,
        duration_sec,
        final_duration_sec,
        aaf_source_offset,
        anchor_source,
        report,
    )
