"""Prepara calibração P03b; corpus antigo de silêncio é opcional (D13)."""

from __future__ import annotations

import argparse
import gzip
import json
import math
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from fractions import Fraction

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from scripts.make_calibration_session import make_calibration_session  # noqa: E402
from tests.aaf_factory import build_calibration_aaf  # noqa: E402
from tests.support.paths import corpus_dir, fixture_aaf  # noqa: E402
from aafton.audio_audit import audit_audio, json_safe, to_markdown  # noqa: E402


def _write_json(path: Path, data) -> None:
    path.write_text(
        json.dumps(json_safe(data), indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _import(aaf: Path, als: Path, media: Path, logs: Path, *, origin=None, fades="keep") -> None:
    command = [
        sys.executable,
        "-m",
        "aafton.cli",
        "import",
        str(aaf),
        str(als),
        "--audio-dir",
        str(media),
        "--media",
        "link",
        "--fades",
        fades,
        "--stereo",
        "pair",
        "--report",
        str(logs / f"{als.stem}-import.json"),
    ]
    if origin is not None:
        command += ["--live-origin", origin]
    res = subprocess.run(command, cwd=REPO, capture_output=True, text=True, encoding="utf-8")
    (logs / f"{als.stem}-import.txt").write_text(res.stdout + res.stderr, encoding="utf-8")
    if res.returncode:
        raise RuntimeError(f"import {aaf.name}: exit {res.returncode}; consulte {logs}")


def _live_tracks(als: Path) -> tuple[list[str], int]:
    xml = ET.fromstring(gzip.decompress(als.read_bytes()))
    names = [t.find("Name/EffectiveName").get("Value") for t in xml.findall(".//Tracks/AudioTrack")]
    ends = [float(e.get("Value")) for e in xml.findall(".//AudioClip/CurrentEnd")]
    return names, math.ceil(max(ends, default=0))


def _export(als: Path, aaf: Path, logs: Path) -> None:
    command = [
        sys.executable,
        "-m",
        "aafton.cli",
        "export",
        str(als),
        str(aaf),
        "--media",
        "link",
        "--report",
        str(logs / "estereo-export.json"),
    ]
    res = subprocess.run(command, cwd=REPO, capture_output=True, text=True, encoding="utf-8")
    (logs / "estereo-export.txt").write_text(res.stdout + res.stderr, encoding="utf-8")
    if res.returncode:
        raise RuntimeError(f"export estéreo: exit {res.returncode}; consulte {logs}")


def _label(samples: int) -> str:
    if samples % 1920:
        raise ValueError("intervalo real nao cai em frame inteiro de 25 fps")
    frames = samples // 1920
    hours, remainder = divmod(frames, 90000)
    minutes, remainder = divmod(remainder, 1500)
    seconds, frames = divmod(remainder, 25)
    return f"{hours:02}:{minutes:02}:{seconds:02}:{frames:02}"


def _real_mask(aaf: Path, anchor: dict, names: list[str]) -> dict:
    """Regioes Fade N do AAF original; nao inventa curvas nem limiares.

    Aplicacao somente a esta fixture, com slots samples e Sequence. Uma
    estrutura diferente falha para revisao, evitando mascara incompleta.
    """
    import aaf2

    def sources(segment):
        if type(segment).__name__ == "SourceClip":
            return [segment]
        if type(segment).__name__ == "OperationGroup":
            return [c for child in segment.segments for c in sources(child)]
        if type(segment).__name__ == "Filler":
            return []
        raise ValueError(f"mascara: componente nao suportado: {type(segment).__name__}")

    by_slot = []
    with aaf2.open(str(aaf), "r") as f:
        composition = list(f.content.compositionmobs())[0]
        for slot in composition.slots:
            if type(slot.segment).__name__ == "Timecode":
                continue
            if Fraction(slot.edit_rate) != 48000 or type(slot.segment).__name__ != "Sequence":
                raise ValueError("mascara real exige Sequence e edit rate 48000")
            cursor, intervals = 0, []
            for component in slot.segment.components:
                duration = int(component.length)
                if any(
                    re.fullmatch(r"Fade\s+\d+", c.mob.name or "", re.IGNORECASE)
                    for c in sources(component)
                ):
                    intervals.append(
                        [
                            anchor["session_start_samples"] + cursor,
                            anchor["session_start_samples"] + cursor + duration,
                        ]
                    )
                cursor += duration
            by_slot.append(intervals)
    indices = anchor["audio_source_indices"]
    if len(indices) != len(names):
        raise ValueError("mascara: numero de tracks difere do sidecar")
    masks = {}
    for name, slots in zip(names, indices):
        if isinstance(slots, int):
            slots = [slots]
        intervals = sorted({tuple(interval) for slot in slots for interval in by_slot[slot]})
        masks[name] = [list(interval) for interval in intervals]
    return {"tracks": masks}


def prepare(output_dir: Path, *, include_structural_corpus: bool = False) -> dict:
    output_dir = output_dir.resolve()
    if output_dir == (REPO / "out/audio-audit-v1").resolve():
        raise ValueError("audio-audit-v1 é evidência preservada; escolha outra pasta")
    # Regeneracao nunca sobrescreve renders ou re-export humanos.
    if output_dir.exists() and any(
        p.is_file()
        for folder in (output_dir / "renders", output_dir / "reexport")
        if folder.exists()
        for p in folder.rglob("*")
    ):
        raise ValueError("pacote ja contem renders/re-export; escolha outra pasta")
    logs = output_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    calibration = make_calibration_session(output_dir / "calibracao")
    cal_als = output_dir / "calibracao/calibracao.als"
    _import(
        Path(calibration["aaf"]), cal_als, Path(calibration["samples"]), logs, origin="01:00:00:00"
    )
    cal_anchor = cal_als.with_suffix(".aafton.json")
    sanity = audit_audio(calibration["expected"], calibration["expected"], cal_anchor, mode="keep")
    if not sanity["ok"]:
        raise ValueError("sanidade da calibracao reprovada")
    _write_json(output_dir / "calibracao/sanity.json", sanity)
    (output_dir / "calibracao/sanity.md").write_text(to_markdown(sanity), encoding="utf-8")
    # Prova humana inclui a saída efetiva da CLI, além da fábrica independente.
    spec = json.loads(Path(calibration["spec"]).read_text(encoding="utf-8"))
    stereo_spec = next(t for t in spec["tracks"] if t["name"] == "CAL_INTERLEAVED")
    stereo_aaf = build_calibration_aaf(
        output_dir / "calibracao/estereo-fonte.aaf", [stereo_spec], frames=spec["frames"]
    )
    stereo_als = output_dir / "calibracao/estereo.als"
    _import(stereo_aaf, stereo_als, Path(calibration["samples"]), logs, origin="01:00:00:00")
    _export(stereo_als, output_dir / "calibracao/estereo-produto.aaf", logs)
    expected_stereo = output_dir / "calibracao/expected-estereo"
    expected_stereo.mkdir(exist_ok=True)
    shutil.copy2(Path(calibration["expected"]) / "CAL_INTERLEAVED.wav", expected_stereo)
    _write_json(
        output_dir / "map-estereo-produto.json",
        {
            "pairs": [
                {
                    "track": "CAL_INTERLEAVED",
                    "reference": "CAL_INTERLEAVED.wav",
                    "candidate": "CAL_INTERLEAVED.wav",
                }
            ]
        },
    )
    real_als, names, end = None, [], None
    if include_structural_corpus:
        real_dir = output_dir / "real"
        isolated = real_dir / "aaf_isolado"
        isolated.mkdir(parents=True, exist_ok=True)
        aaf = Path(shutil.copy2(fixture_aaf("TESTE_AFF_PROTOOLS.aaf"), isolated))
        real_als = real_dir / "TESTE_AFF_PROTOOLS-absorb.als"
        _import(aaf, real_als, corpus_dir("teste_aaf"), logs, fades="absorb")
        anchor = json.loads(real_als.with_suffix(".aafton.json").read_text(encoding="utf-8"))
        names, end = _live_tracks(real_als)
        if len(names) != 14:
            raise ValueError(f"preparo estrutural: contagem inesperada: {len(names)}")
        _write_json(real_dir / "mask-absorb.json", _real_mask(aaf, anchor, names))
    cal_names, cal_end = _live_tracks(cal_als)
    if len(cal_names) != 7:
        raise ValueError(f"preparo: contagem inesperada: calibracao {len(cal_names)}")
    for kind, track_names in (("calibracao", cal_names), ("teste", names)):
        mapping = {
            "pairs": [
                {"track": n, "reference": f"{n}.wav", "candidate": f"{n}.wav"} for n in track_names
            ]
        }
        _write_json(output_dir / f"map-{kind}.json", mapping)
    for folder in (
        "renders/live/calibracao",
        "renders/protools/calibracao",
        "renders/protools/calibracao-brutos",
        "renders/protools/estereo-produto",
        "reports",
    ):
        (output_dir / folder).mkdir(parents=True, exist_ok=True)
    guide = (REPO / "docs/AUDIO_AUDIT.md").read_text(encoding="utf-8")
    guide = guide.replace("{{PACOTE}}", str(output_dir))
    (output_dir / "INSTRUCOES.md").write_text(guide, encoding="utf-8")
    result = {
        "output_dir": str(output_dir),
        "calibration": calibration,
        "real_als": str(real_als) if real_als else None,
        "instructions": str(output_dir / "INSTRUCOES.md"),
        "real_tracks": names,
        "calibration_tracks": cal_names,
        "real_end_beat": end,
        "calibration_end_beat": 4,
        "sanity_ok": True,
        "structural_corpus_included": include_structural_corpus,
        "stereo_product_aaf": str(output_dir / "calibracao/estereo-produto.aaf"),
        "status": "P03b preparado; aceite sonoro aguarda renders humanos",
    }
    _write_json(output_dir / "bundle.json", result)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--include-structural-corpus",
        action="store_true",
        help="inclui silêncio TESTE_AAF somente para estrutura, fora do aceite sonoro",
    )
    args = parser.parse_args(argv)
    try:
        result = prepare(args.output_dir, include_structural_corpus=args.include_structural_corpus)
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"preparo interrompido: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
