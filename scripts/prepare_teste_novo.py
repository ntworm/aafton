"""Prepara TESTE_NOVO keep pelo mapa explícito, sem alterar corpus pinado.

AAF nativo nomeado é copiado sem alteração; fonte histórica sem SlotName
ganha nomes somente na cópia local. Nomes divergentes continuam rejeitados.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import aaf2

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from tests.support.paths import reexport_corpus  # noqa: E402


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def prepare(output_dir: Path) -> dict:
    output_dir = output_dir.resolve()
    source_aaf, media = reexport_corpus()
    selected = json.loads(
        (REPO / "tests/corpus/teste_novo_tracks.json").read_text(encoding="utf-8")
    )
    source_sha = _sha(source_aaf)
    if source_sha != selected["source_aaf_sha256"]:
        raise ValueError("TESTE_NOVO: mapa não corresponde ao AAF pinado")
    render_dir = output_dir / "renders/live"
    if render_dir.exists() and any(render_dir.rglob("*.wav")):
        raise ValueError("TESTE_NOVO já tem render humano; preserve-o antes de regenerar")
    for track in selected["tracks"]:
        bounce = output_dir / "bounces" / track["reference"]
        if not bounce.is_file() or _sha(bounce) != track["reference_sha256"]:
            raise ValueError(f"bounce ausente ou hash divergente: {bounce.name}")
    live, reports = output_dir / "live", output_dir / "reports"
    named_dir = live / "aaf-nomeado"
    for folder in (named_dir, reports, render_dir):
        folder.mkdir(parents=True, exist_ok=True)
    named_aaf = Path(shutil.copy2(source_aaf, named_dir))
    slot_names = {
        slot_id: track["track"] + f" [{channel}]"
        for track in selected["tracks"]
        for slot_id, channel in zip(track["slot_ids"], ("L", "R"))
    }
    native_names = {
        slot_id: track["track"] for track in selected["tracks"] for slot_id in track["slot_ids"]
    }
    renamed_slots = []
    with aaf2.open(str(named_aaf), "r") as f:
        slots = [
            s for s in next(f.content.compositionmobs()).slots if s.segment.media_kind == "Sound"
        ]
        if {s.slot_id for s in slots} != set(slot_names):
            raise ValueError("slots do AAF diferem do mapa explícito")
        for slot in slots:
            if not slot.name:
                renamed_slots.append(slot.slot_id)
            elif slot.name not in (native_names[slot.slot_id], slot_names[slot.slot_id]):
                raise ValueError(f"SlotName diverge do mapa explícito: slot {slot.slot_id}")
    if renamed_slots:
        with aaf2.open(str(named_aaf), "r+") as f:
            for slot in next(f.content.compositionmobs()).slots:
                if slot.slot_id in renamed_slots:
                    slot.name = slot_names[slot.slot_id]
    if _sha(source_aaf) != source_sha:
        raise ValueError("AAF original alterado durante preparação")
    als = live / "TESTE_NOVO-keep.als"
    command = [
        sys.executable,
        "-m",
        "aafton.cli",
        "import",
        str(named_aaf),
        str(als),
        "--audio-dir",
        str(media),
        "--media",
        "link",
        "--fades",
        "keep",
        "--stereo",
        "pair",
        "--live-origin",
        "01:03:30:00",
        "--report",
        str(reports / "keep-import.json"),
    ]
    result = subprocess.run(command, cwd=REPO, capture_output=True, text=True, encoding="utf-8")
    (reports / "keep-import.txt").write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(f"import keep: exit {result.returncode}; consulte {reports}")
    xml = ET.fromstring(gzip.decompress(als.read_bytes()))
    names = [t.find("Name/EffectiveName").get("Value") for t in xml.findall(".//Tracks/AudioTrack")]
    anchor = json.loads(als.with_suffix(".aafton.json").read_text(encoding="utf-8"))
    expected = [track["track"] for track in selected["tracks"]]
    if names != expected or any(anchor["track_channels"][n] != 2 for n in names):
        raise ValueError(f"pareamento incompleto: {names!r}; esperado {expected!r}")
    if anchor["live_origin_samples"] != selected["origin_samples"]:
        raise ValueError("origem Live difere do bounce pinado")
    mapping = {
        "pairs": [
            {k: track[k] for k in ("track", "reference", "candidate")}
            for track in selected["tracks"]
        ]
    }
    (output_dir / "map-teste-novo.json").write_text(
        json.dumps(mapping, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    proof = {
        "source_aaf_sha256": source_sha,
        "named_aaf_sha256": _sha(named_aaf),
        "changed_properties": (
            f"SlotName dos slots {renamed_slots}, conforme teste_novo_tracks.json"
            if renamed_slots
            else "Nenhuma; SlotName nativo ja corresponde ao mapa explicito"
        ),
        "als": str(als),
        "tracks": names,
        "import_exit": result.returncode,
        "sample_rate": selected["sample_rate"],
        "render_frames": selected["render_frames"],
        "status": "preparado; aceite sonoro aguarda render Live",
    }
    (reports / "preparo.json").write_text(
        json.dumps(proof, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return proof


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--output-dir", type=Path, default=REPO / "out/teste-novo")
    args = parser.parse_args(argv)
    try:
        proof = prepare(args.output_dir)
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"preparo interrompido: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(proof, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
