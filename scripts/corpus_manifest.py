"""Gera o manifesto pinado de um corpus externo de WAV.

O audio dos testes (silencio estrutural ou corpus real TESTE_NOVO) fica fora
do Git. O que vai para o repositorio e este manifesto: por arquivo, nome, bytes,
sha256, canais, taxa, quadros e formato. Os testes resolvem a pasta local por
AAFTON_CORPUS_DIR e conferem contra o manifesto, entao um corpus trocado ou
incompleto falha em vez de passar ou pular.

O cabecalho WAV e lido aqui mesmo, so com a biblioteca padrao, sem passar pelo
leitor de midia do aafton: o manifesto serve de referencia independente para
ele.

Uso:
    python scripts/corpus_manifest.py PASTA_DOS_WAV tests/corpus/teste_aaf.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

WAVE_FORMAT_PCM = 0x0001
WAVE_FORMAT_IEEE_FLOAT = 0x0003
WAVE_FORMAT_EXTENSIBLE = 0xFFFE

DESCRICAO_PADRAO = {
    "teste_aaf": (
        "Silencio digital gerado por scripts/make_silence_corpus.py, somente para testes "
        "estruturais de TESTE_AFF_PROTOOLS.aaf; nao e midia real do Pro Tools. "
        "Fica fora do Git; pasta resolvida por AAFTON_CORPUS_DIR ou .local/corpus/teste_aaf."
    ),
    "teste_novo_reexport": "AAF nativo TESTE_NOVO_FINAL24, Pro Tools 26.4.1, 14/10/2026 (D13): quatro tracks committed com fades/clip gain impressos, fader 0 e automacao desligada; 12 WAV mono PCM24 e 8 slots. Referencias do mesmo AAF reimportado no Pro Tools; clipping pre-export documentado separadamente. Sha256 obrigatorio, audio fora do Git.",
}


class CorpusError(ValueError):
    """Arquivo do corpus que este gerador nao sabe descrever com certeza."""


def wav_info(caminho: Path) -> dict:
    """Canais, taxa, quadros e formato lidos do cabecalho RIFF/WAVE."""
    tamanho_arquivo = caminho.stat().st_size
    with open(caminho, "rb") as f:
        cabecalho = f.read(12)
        if len(cabecalho) < 12:
            raise CorpusError(f"{caminho.name}: curto demais para ter cabecalho")
        if cabecalho[:4] in (b"RF64", b"BW64"):
            raise CorpusError(f"{caminho.name}: RF64/BW64 nao suportado no manifesto")
        if cabecalho[:4] != b"RIFF" or cabecalho[8:12] != b"WAVE":
            raise CorpusError(f"{caminho.name}: nao e RIFF/WAVE")

        fmt = None
        data_bytes = None
        while True:
            bloco = f.read(8)
            if len(bloco) < 8:
                break
            chunk_id, tamanho = struct.unpack("<4sI", bloco)
            inicio = f.tell()
            if chunk_id == b"fmt ":
                if tamanho < 16:
                    raise CorpusError(f"{caminho.name}: chunk fmt com {tamanho} bytes")
                fmt = f.read(tamanho)
            elif chunk_id == b"data":
                if inicio + tamanho > tamanho_arquivo:
                    raise CorpusError(
                        f"{caminho.name}: chunk data declara {tamanho} bytes, "
                        f"mas o arquivo termina antes"
                    )
                data_bytes = tamanho
            f.seek(inicio + tamanho + (tamanho % 2))

    if fmt is None or data_bytes is None:
        raise CorpusError(f"{caminho.name}: sem chunk fmt ou data")

    tag, canais, taxa, _bytes_por_seg, block_align, bits = struct.unpack("<HHIIHH", fmt[:16])
    subformato = tag
    if tag == WAVE_FORMAT_EXTENSIBLE:
        if len(fmt) < 40:
            raise CorpusError(f"{caminho.name}: WAVE_FORMAT_EXTENSIBLE com fmt de {len(fmt)} bytes")
        subformato = struct.unpack("<H", fmt[24:26])[0]
    if subformato == WAVE_FORMAT_PCM:
        tipo = "pcm"
    elif subformato == WAVE_FORMAT_IEEE_FLOAT:
        tipo = "float"
    else:
        raise CorpusError(f"{caminho.name}: formato {tag:#06x}/{subformato:#06x} desconhecido")
    if block_align <= 0 or data_bytes % block_align:
        raise CorpusError(
            f"{caminho.name}: data com {data_bytes} bytes nao divide block_align {block_align}"
        )

    return {
        "channels": canais,
        "sample_rate": taxa,
        "frames": data_bytes // block_align,
        "format": f"{tipo}{bits}",
        "format_tag": f"{tag:#06x}",
    }


def sha256(caminho: Path) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for pedaco in iter(lambda: f.read(1 << 20), b""):
            h.update(pedaco)
    return h.hexdigest()


def build_manifest(pasta: Path, nome: str, descricao: str | None = None) -> dict:
    wavs = sorted(
        (p for p in pasta.rglob("*") if p.is_file() and p.suffix.lower() in (".wav", ".aaf")),
        key=lambda p: p.name,
    )
    if not wavs:
        raise CorpusError(f"{pasta}: nenhum .wav ou .aaf encontrado")
    arquivos = []
    for caminho in wavs:
        entrada = {
            "name": caminho.relative_to(pasta).as_posix(),
            "bytes": caminho.stat().st_size,
            "sha256": sha256(caminho),
        }
        entrada.update(wav_info(caminho) if caminho.suffix.lower() == ".wav" else {"format": "aaf"})
        arquivos.append(entrada)
    return {
        "corpus": nome,
        "description": descricao or DESCRICAO_PADRAO.get(nome, ""),
        "files": arquivos,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("pasta", type=Path, help="pasta com os WAV do corpus")
    parser.add_argument("saida", type=Path, help="manifesto JSON a gravar")
    parser.add_argument("--name", help="nome do corpus (padrao: nome do arquivo de saida)")
    parser.add_argument("--description", help="descricao gravada no manifesto")
    args = parser.parse_args(argv)

    if not args.pasta.is_dir():
        print(f"pasta do corpus nao existe: {args.pasta}", file=sys.stderr)
        return 2
    nome = args.name or args.saida.stem
    try:
        manifesto = build_manifest(args.pasta, nome, args.description)
    except CorpusError as erro:
        print(f"corpus recusado: {erro}", file=sys.stderr)
        return 1

    args.saida.parent.mkdir(parents=True, exist_ok=True)
    texto = json.dumps(manifesto, indent=2, ensure_ascii=False) + "\n"
    with open(args.saida, "w", encoding="utf-8", newline="\n") as f:
        f.write(texto)
    total = sum(e["bytes"] for e in manifesto["files"])
    print(f"{args.saida}: {len(manifesto['files'])} arquivos, {total} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
