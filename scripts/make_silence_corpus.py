"""Gera corpus de silencio PCM16 a partir de manifesto JSON."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import wave
from pathlib import Path


def _sha256(caminho: Path) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for pedaco in iter(lambda: f.read(1 << 20), b""):
            h.update(pedaco)
    return h.hexdigest()


def make_silence_corpus(manifesto_caminho: Path, pasta_destino: Path) -> int:
    with open(manifesto_caminho, encoding="utf-8") as f:
        dados = json.load(f)

    entradas = dados.get("files", [])
    for entrada in entradas:
        if entrada.get("format") != "pcm16":
            print(
                f"formato nao suportado em {entrada.get('name')}: {entrada.get('format')}",
                file=sys.stderr,
            )
            return 2

    pasta_destino.mkdir(parents=True, exist_ok=True)
    for entrada in entradas:
        alvo = pasta_destino / entrada["name"]
        with wave.open(str(alvo), "wb") as w:
            w.setnchannels(entrada["channels"])
            w.setsampwidth(2)
            w.setframerate(entrada["sample_rate"])
            w.writeframes(b"\0" * (2 * entrada["channels"] * entrada["frames"]))

    identicos = 0
    total = len(entradas)
    for entrada in entradas:
        alvo = pasta_destino / entrada["name"]
        if alvo.stat().st_size == entrada["bytes"] and _sha256(alvo) == entrada["sha256"]:
            identicos += 1

    print(f"{identicos}/{total} identical")
    return 0 if identicos == total else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="Manifesto JSON")
    parser.add_argument("folder", type=Path, help="Pasta de destino")
    args = parser.parse_args(argv)
    return make_silence_corpus(args.manifest, args.folder)


if __name__ == "__main__":
    raise SystemExit(main())
