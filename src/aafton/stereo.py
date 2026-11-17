import hashlib
import math
import re
import tempfile
from collections import defaultdict
from copy import deepcopy
from functools import partial
from pathlib import Path
from typing import Dict, Tuple

from . import media_names
from .ir import SessionIR, TrackIR, MediaAsset
from .report import Report
from .media_extract import read_audio_metadata
from .stereo_fonte_unica import colapsar_fonte_unica
from .stereo_pair_state import par_com_transacao


def _get_base_name(name: str) -> Tuple[str, str]:
    match = re.search(r" \[([^\]]+)\]$", name)
    if match:
        suffix = match.group(1)
        base = name[: match.start()]
        return base, suffix

    match = re.search(r"([_\.])([LRlr])$", name)
    if match:
        suffix = match.group(2).upper()
        base = name[: match.start()]
        return base, suffix

    return name, ""


def _juntar_origens(clips_l: list, clips_r: list) -> None:
    """O clip estereo que fica leva as chaves de origem do clip R do par."""
    ordem = lambda c: c.timeline_start_samples  # noqa: E731
    for cl, cr in zip(sorted(clips_l, key=ordem), sorted(clips_r, key=ordem)):
        if "origin_keys" in cl.raw and "origin_keys" in cr.raw:
            cl.raw["origin_keys"] = [*cl.raw["origin_keys"], *cr.raw["origin_keys"]]


def _pan_valido(valor) -> float | None:
    """Pan estatico como float finito entre -1 e 1; None quando nao e."""
    try:
        pan = float(valor)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(pan) or not -1.0 <= pan <= 1.0:
        return None
    return pan


def _pan_do_par(t_l: TrackIR, t_r: TrackIR, base_name: str, report: Report) -> bool | None:
    """Pan estatico do par: None recusa o par; True quando e L/R complementar."""
    left_pan = _pan_valido(t_l.pan)
    right_pan = _pan_valido(t_r.pan)
    if left_pan is None or right_pan is None:
        report.warning(
            "STEREO_PAIR_REJECTED",
            "Invalid static pan between the channels",
            {
                "track": base_name,
                "reason": "pan_invalid",
                "left_pan": repr(t_l.pan),
                "right_pan": repr(t_r.pan),
            },
        )
        return None

    both_centered = math.isclose(left_pan, 0.0, abs_tol=1e-9) and math.isclose(
        right_pan, 0.0, abs_tol=1e-9
    )
    hard_lr = math.isclose(left_pan, -1.0, abs_tol=1e-9) and math.isclose(
        right_pan, 1.0, abs_tol=1e-9
    )
    if not (both_centered or hard_lr):
        report.warning(
            "STEREO_PAIR_REJECTED",
            "Incompatible static pan between the channels",
            {
                "track": base_name,
                "reason": "pan",
                "left_pan": t_l.pan,
                "right_pan": t_r.pan,
            },
        )
        return None
    return hard_lr


def process_stereo_pairing(
    session: SessionIR, mode: str, report: Report, extract_dir: Path | None, dry_run: bool = False
) -> None:
    if mode == "split":
        return

    groups = defaultdict(list)

    for i, t in enumerate(session.tracks):
        base, suffix = _get_base_name(t.name)
        groups[base].append((i, t, suffix))

    to_remove = set()
    generated_files: Dict[Tuple[str, str], str] = {}
    total_interleaved_written = 0

    # Os dois canais que vem do mesmo arquivo estereo viram um clip so: no
    # Live eles seriam dois clips sobrepostos na mesma track.
    colapsar_fonte_unica(session, report)

    # O combiner Pro Tools contém duas fontes mono dentro de um slot lógico.
    # Live precisa de um arquivo estéreo e um clip, como no par de slots L/R.
    for track in session.tracks:
        if track.channel_count != 2 or not track.clips:
            continue
        assets = [session.media_by_id(c.media_id) for c in track.clips]
        if not all(asset is not None and asset.channels == 1 for asset in assets):
            continue
        left, right = deepcopy(track), deepcopy(track)
        left.clips = [c for c in left.clips if c.channel_index == 0]
        right.clips = [c for c in right.clips if c.channel_index == 1]
        success, written = par_com_transacao(
            partial(_try_pair, source_key_in_name=True),
            (session, track.name, left, right, report, extract_dir, generated_files, dry_run),
            session,
            left,
            generated_files,
        )
        if success:
            _juntar_origens(left.clips, right.clips)
            track.clips = left.clips
            total_interleaved_written += written

    for base_name, todas in groups.items():
        # Uma track sem sufixo com o mesmo nome base (Audio 1 ao lado de
        # Audio 1_L e Audio 1_R, como no AAF do Premiere) nao impede o par.
        # Outros sufixos (C, Ls, LFE de um 5.1) continuam impedindo o par.
        group = [g for g in todas if g[2]]
        homonima = len(group) < len(todas)
        if len(group) == 2:
            i1, t1, suf1 = group[0]
            i2, t2, suf2 = group[1]
            if {suf1, suf2} == {"L", "R"}:
                t_l = t1 if suf1 == "L" else t2
                t_r = t2 if suf2 == "R" else t1
                i_r = i2 if suf2 == "R" else i1

                hard_lr = _pan_do_par(t_l, t_r, base_name, report)
                if hard_lr is None:
                    continue

                success, written = par_com_transacao(
                    _try_pair,
                    (session, base_name, t_l, t_r, report, extract_dir, generated_files, dry_run),
                    session,
                    t_l,
                    generated_files,
                )
                if success:
                    _juntar_origens(t_l.clips, t_r.clips)
                    if hard_lr:
                        t_l.pan = 0.0
                        report.info(
                            "STEREO_PAN_CENTERED",
                            f"the pair {base_name!r} had complementary L/R pan and was "
                            "centered after becoming a stereo track",
                            {"track": base_name},
                        )
                    total_interleaved_written += written
                    if homonima:
                        t_l.name = f"{base_name} L/R"
                    t_l.original_source_indices.extend(t_r.original_source_indices)
                    to_remove.add(i_r)

    if to_remove:
        session.tracks = [t for i, t in enumerate(session.tracks) if i not in to_remove]

    if mode == "pair" and not dry_run:
        report.info(
            "STEREO_INTERLEAVED",
            f"{total_interleaved_written} files generated",
            {"count": total_interleaved_written, "dir": str(extract_dir) if extract_dir else ""},
        )


def _try_pair(
    session: SessionIR,
    base_name: str,
    t_l: TrackIR,
    t_r: TrackIR,
    report: Report,
    extract_dir: Path | None,
    generated_files: dict,
    dry_run: bool,
    *,
    source_key_in_name=False,
) -> Tuple[bool, int]:
    if any(
        getattr(t_l, field) != getattr(t_r, field)
        for field in ("gain_linear", "muted", "gain_points", "plugins")
    ):
        report.warning(
            "STEREO_PAIR_REJECTED",
            "Gain, mute, automation or plugins different between the channels",
            {"track": base_name},
        )
        return False, 0
    if len(t_l.clips) != len(t_r.clips):
        report.warning("STEREO_PAIR_REJECTED", "Different number of clips", {"track": base_name})
        return False, 0

    clips_l = sorted(t_l.clips, key=lambda c: c.timeline_start_samples)
    clips_r = sorted(t_r.clips, key=lambda c: c.timeline_start_samples)

    for cl, cr in zip(clips_l, clips_r):
        if any(
            getattr(cl, field) != getattr(cr, field)
            for field in ("gain_linear", "muted", "fade_in", "fade_out", "crossfade_in_state")
        ):
            report.warning(
                "STEREO_PAIR_REJECTED",
                "Different clip states between the channels",
                {"track": base_name},
            )
            return False, 0
        if (
            cl.timeline_start_samples != cr.timeline_start_samples
            or cl.timeline_end_samples != cr.timeline_end_samples
            or cl.source_offset_samples != cr.source_offset_samples
        ):
            report.warning("STEREO_PAIR_REJECTED", "Different cuts", {"track": base_name})
            return False, 0

    if len(clips_l) == 0:
        t_l.name = base_name
        t_l.channel_count = 2
        return True, 0

    clip_actions = []

    for cl, cr in zip(clips_l, clips_r):
        al = session.media_by_id(cl.media_id)
        ar = session.media_by_id(cr.media_id)
        if not al or not ar or not al.resolved_path or not ar.resolved_path:
            report.warning("STEREO_PAIR_REJECTED", "Media not resolved", {"track": base_name})
            return False, 0

        path_l = Path(al.resolved_path)
        path_r = Path(ar.resolved_path)

        if path_l == path_r:
            clip_actions.append(("cheap", cl, al.id, None, None))
            continue

        try:
            meta_l = read_audio_metadata(path_l)
            meta_r = read_audio_metadata(path_r)
        except Exception as e:
            report.warning("MEDIA_METADATA_UNREADABLE", str(e), {"track": base_name})
            return False, 0

        if meta_l["channels"] != 1 or meta_r["channels"] != 1:
            report.warning("STEREO_PAIR_REJECTED", "Not mono", {"track": base_name})
            return False, 0

        if (
            meta_l["sample_rate"] != meta_r["sample_rate"]
            or meta_l["duration_samples"] != meta_r["duration_samples"]
            or meta_l.get("bits") != meta_r.get("bits")
        ):
            report.warning("STEREO_PAIR_REJECTED", "Incompatible properties", {"track": base_name})
            return False, 0

        clip_actions.append(("interleave", cl, path_l, path_r, meta_l))

    files_written = 0

    for action in clip_actions:
        if action[0] == "cheap":
            continue

        _, cl, path_l, path_r, meta = action
        pair_key = (str(path_l), str(path_r))

        if pair_key in generated_files:
            cl.media_id = generated_files[pair_key]
            continue

        if dry_run:
            new_media_id = f"stereo_{pair_key[0]}_{pair_key[1]}"
            if session.media_by_id(new_media_id) is None:
                new_asset = MediaAsset(
                    id=new_media_id,
                    filename=f"{path_l.stem}.wav",
                    channels=2,
                    sample_rate=meta["sample_rate"],
                    duration_samples=meta["duration_samples"],
                    status="resolved",
                    resolved_path="dummy.wav",
                )
                session.media.append(new_asset)
            cl.media_id = new_media_id
            generated_files[pair_key] = new_media_id
            continue

        if extract_dir is None:
            raise ValueError("extract_dir needed for interleaving")

        base_filename = cl.name
        if base_filename.endswith(".wav") or base_filename.endswith(".WAV"):
            base_filename = base_filename[:-4]
        if base_filename.endswith(".L") or base_filename.endswith(".R"):
            base_filename = base_filename[:-2]
        elif base_filename.endswith("_L") or base_filename.endswith("_R"):
            base_filename = base_filename[:-2]

        extract_dir.mkdir(parents=True, exist_ok=True)
        from .media_extract import file_sha256

        try:
            with tempfile.TemporaryDirectory(prefix="stereo-", dir=extract_dir) as temporary:
                staged = Path(temporary) / "audio.wav"
                meta_r_interleave = read_audio_metadata(path_r)
                _interleave_wav(path_l, path_r, staged, meta, meta_r_interleave)
                digest = file_sha256(staged)
                out_name = media_names.stereo_output_name(extract_dir, base_filename, digest)
                out_path = extract_dir / out_name
                if out_path.exists() or out_path.is_symlink():
                    if (
                        out_path.is_symlink()
                        or not out_path.is_file()
                        or file_sha256(out_path) != digest
                    ):
                        raise ValueError(f"existing stereo file differs: {out_path}")
                else:
                    staged.rename(out_path)
                    files_written += 1
        except media_names.MediaNameTooLongError as exc:
            report.warning("STEREO_PATH_TOO_LONG", str(exc), {"track": base_name})
            return False, files_written
        except Exception as exc:
            report.warning(
                "STEREO_INTERLEAVE_FAILED",
                f"Could not interleave {path_l.name}+{path_r.name}: {type(exc).__name__}: {exc}",
                {"track": base_name},
            )
            return False, files_written

        out_meta = read_audio_metadata(out_path)
        new_media_id = f"stereo_{hashlib.sha256(str(pair_key).encode()).hexdigest()[:8]}"
        if session.media_by_id(new_media_id) is None:
            new_asset = MediaAsset(
                id=new_media_id,
                filename=out_name,
                channels=2,
                sample_rate=out_meta["sample_rate"],
                duration_samples=out_meta["duration_samples"],
                status="resolved",
                resolved_path=str(out_path.resolve()),
            )
            session.media.append(new_asset)

        cl.media_id = new_media_id
        generated_files[pair_key] = new_media_id

        # Limpar o sufixo .L / .R do nome do clip para que o Live mostre o nome limpo do evento estéreo
        clean_cname = cl.name
        if clean_cname.endswith(".wav") or clean_cname.endswith(".WAV"):
            clean_cname = clean_cname[:-4]
        if clean_cname.endswith(".L") or clean_cname.endswith(".R"):
            clean_cname = clean_cname[:-2]
        elif clean_cname.endswith("_L") or clean_cname.endswith("_R"):
            clean_cname = clean_cname[:-2]
        cl.name = clean_cname

    # O par so vira uma track estereo quando deu tudo certo: recusar o par nao
    # pode deixar a sessao com o nome e os canais de uma conversao que nao aconteceu.
    t_l.name = base_name
    t_l.channel_count = 2
    return True, files_written


def _interleave_wav(path_l: Path, path_r: Path, out_path: Path, meta_l: dict, meta_r: dict) -> None:
    """Entrelaca dois mono em um WAV estereo. Converte big-endian para little-endian se necessario."""
    import struct

    channels = 2
    sample_rate = meta_l["sample_rate"]
    bits = meta_l.get("bits") or 16
    bytes_per_sample = bits // 8
    block_align = channels * bytes_per_sample
    bytes_per_sec = sample_rate * block_align

    left_fmt, right_fmt = meta_l.get("fmt_payload"), meta_r.get("fmt_payload")

    def encoding(fmt):
        if fmt is None:
            return 1
        tag = struct.unpack_from("<H", fmt)[0]
        return struct.unpack_from("<H", fmt, 24)[0] if tag == 0xFFFE else tag

    if encoding(left_fmt) != encoding(right_fmt):
        raise ValueError("mono sources use different encodings")
    if left_fmt is not None:
        new_fmt = bytearray(left_fmt)
        struct.pack_into("<HIIH", new_fmt, 2, channels, sample_rate, bytes_per_sec, block_align)
        if struct.unpack_from("<H", new_fmt)[0] == 0xFFFE:
            struct.pack_into("<I", new_fmt, 20, 3)
    else:
        new_fmt = struct.pack("<HHIIHH", 1, channels, sample_rate, bytes_per_sec, block_align, bits)

    data_size_mono = meta_l.get("data_size") or (meta_l["duration_samples"] * bytes_per_sample)
    data_size = data_size_mono * 2

    swap_l = meta_l.get("is_big_endian", False)
    swap_r = meta_r.get("is_big_endian", False)

    with open(out_path, "wb") as f_out:
        f_out.write(b"RIFF")
        f_out.write(struct.pack("<I", 4 + 8 + len(new_fmt) + len(new_fmt) % 2 + 8 + data_size))
        f_out.write(b"WAVE")
        f_out.write(b"fmt ")
        f_out.write(struct.pack("<I", len(new_fmt)))
        f_out.write(new_fmt)
        f_out.write(b"\0" * (len(new_fmt) % 2))
        f_out.write(b"data")
        f_out.write(struct.pack("<I", data_size))

        with open(path_l, "rb") as fl, open(path_r, "rb") as fr:
            fl.seek(meta_l["data_offset"])
            fr.seek(meta_r["data_offset"])

            chunk_samples = 4096
            chunk_bytes = chunk_samples * bytes_per_sample
            remaining = data_size_mono
            while remaining:
                count = min(chunk_bytes, remaining)
                bl = fl.read(count)
                br = fr.read(count)
                if len(bl) != count or len(br) != count or count % bytes_per_sample:
                    raise ValueError("mono source truncated or misaligned")
                remaining -= count

                n = min(len(bl), len(br)) // bytes_per_sample
                if swap_l:
                    bl = _swap_endian(bl[: n * bytes_per_sample], bytes_per_sample)
                else:
                    bl = bl[: n * bytes_per_sample]
                if swap_r:
                    br = _swap_endian(br[: n * bytes_per_sample], bytes_per_sample)
                else:
                    br = br[: n * bytes_per_sample]

                interleaved = bytearray(n * bytes_per_sample * 2)
                for i in range(n):
                    s = i * bytes_per_sample
                    d = i * bytes_per_sample * 2
                    interleaved[d : d + bytes_per_sample] = bl[s : s + bytes_per_sample]
                    interleaved[d + bytes_per_sample : d + bytes_per_sample * 2] = br[
                        s : s + bytes_per_sample
                    ]

                f_out.write(interleaved)


def _swap_endian(data: bytes, bytes_per_sample: int) -> bytes:
    """Troca big-endian para little-endian sample a sample."""
    if bytes_per_sample == 2:
        arr = bytearray(data)
        for i in range(0, len(arr) - 1, 2):
            arr[i], arr[i + 1] = arr[i + 1], arr[i]
        return bytes(arr)
    elif bytes_per_sample == 3:
        arr = bytearray(data)
        for i in range(0, len(arr) - 2, 3):
            arr[i], arr[i + 2] = arr[i + 2], arr[i]
        return bytes(arr)
    elif bytes_per_sample == 4:
        arr = bytearray(data)
        for i in range(0, len(arr) - 3, 4):
            arr[i], arr[i + 3] = arr[i + 3], arr[i]
            arr[i + 1], arr[i + 2] = arr[i + 2], arr[i + 1]
        return bytes(arr)
    return data
