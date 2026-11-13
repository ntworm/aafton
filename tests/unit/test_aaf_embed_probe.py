"""Sondagem da receita de embed de midia dentro do AAF.

A receita medida aqui e a que o pipeline de escrita vai precisar adopter:

1. o SourceMob nasce de ``f.content.link_external_wav(meta)``, com
   WAVEDescriptor e Locator apontando para o WAV externo;
2. o Locator e removido do WAVEDescriptor, porque depois do embed nao existe
   arquivo externo para linkar;
3. a essencia RIFF completa e gravada no EssenceData do proprio SourceMob do
   arquivo, copiada do WAV em blocos de 64 KiB.

O teste prova a receita antes de qualquer mudanca no pipeline: com o WAV
removido do disco, o import precisa achar a essencia pelo mob_id e devolver um
WAV byte a byte igual ao original.

O que o pyaaf2 1.7.1 faz nesta receita, medido aqui:

- ``link_external_wav`` cria dois SourceMob: o da fita ("<TAPE MOB>", com o
  ImportDescriptor) e o do arquivo ("<SOURCE MOB>", o que a cadeia
  MasterMob -> SourceClip alcanca). O EssenceData pertence ao do arquivo;
- remover o Locator deixa o vetor de Locators vazio no WAVEDescriptor gravado;
- a essencia se le com ``EssenceData.open()``; ``open("rb")`` falha com
  "file readonly" porque o AAF ja foi aberto para leitura.
"""

from __future__ import annotations

import gzip
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

import aaf2
import pytest

from aafton.cli import main

SAMPLE_RATE = 48000
FRAMES = 4800
BLOCO = 65536

FORMATOS = [
    pytest.param("pcm16", 1, 16, "pcm_s16le", id="pcm16"),
    pytest.param("pcm24", 1, 24, "pcm_s24le", id="pcm24"),
    pytest.param("float32", 3, 32, "pcm_f32le", id="float32"),
]


def _wav_bytes(formato: str, frames: int = FRAMES, sample_rate: int = SAMPLE_RATE) -> bytes:
    """RIFF WAV mono valido, com amostras deterministicas e nao silenciosas."""
    if formato == "pcm16":
        amostras = b"".join(struct.pack("<h", (i * 37) % 30001 - 15000) for i in range(frames))
    elif formato == "pcm24":
        amostras = b"".join(
            bytes(((i * 7) % 256, (i * 13) % 256, (i * 29) % 256)) for i in range(frames)
        )
    else:
        amostras = b"".join(struct.pack("<f", ((i % 17) - 8) / 16.0) for i in range(frames))

    bits = {"pcm16": 16, "pcm24": 24, "float32": 32}[formato]
    tag = {"pcm16": 1, "pcm24": 1, "float32": 3}[formato]
    largura = bits // 8
    fmt = struct.pack("<HHIIHH", tag, 1, sample_rate, sample_rate * largura, largura, bits)
    corpo = (
        b"WAVE"
        + b"fmt "
        + struct.pack("<I", len(fmt))
        + fmt
        + b"data"
        + struct.pack("<I", len(amostras))
        + amostras
    )
    return b"RIFF" + struct.pack("<I", len(corpo)) + corpo


def _construir_aaf_embedido(aaf_path: Path, wav_path: Path, codec_name: str) -> Path:
    """Monta o AAF com a essencia RIFF embedded no SourceMob do arquivo."""
    with aaf2.open(str(aaf_path), "w") as f:
        composition = f.create.CompositionMob("cena12")
        f.content.mobs.append(composition)

        meta = {
            "format": {"filename": str(wav_path), "format_name": "wav"},
            "streams": [
                {
                    "codec_type": "audio",
                    "codec_name": codec_name,
                    "sample_rate": SAMPLE_RATE,
                    "duration_ts": FRAMES,
                    "channels": 1,
                }
            ],
        }
        master, source, _tape = f.content.link_external_wav(meta)

        # Sem arquivo externo depois do embed: o Locator sai do WAVEDescriptor.
        del source.descriptor["Locator"]

        ed = f.create.EssenceData()
        ed.mob = source
        f.content.essencedata.append(ed)
        stream = ed.open("w")
        with open(wav_path, "rb") as origem:
            while True:
                bloco = origem.read(BLOCO)
                if not bloco:
                    break
                stream.write(bloco)
        stream.close()

        slot = composition.create_timeline_slot(edit_rate=SAMPLE_RATE)
        slot.name = "DIALOGO"
        sequencia = f.create.Sequence(media_kind="sound")
        slot.segment = sequencia
        clip = f.create.SourceClip(media_kind="sound", start=0)
        clip.length = FRAMES
        clip.mob = master
        clip.slot_id = 1
        sequencia.components.append(clip)

        tc_slot = composition.create_timeline_slot(edit_rate=25)
        tc_slot.name = "TC"
        tc = f.create.Timecode(fps=25, drop=False)
        tc.start = 0
        tc.length = max(1, FRAMES * 25 // SAMPLE_RATE)
        tc_slot.segment = tc
    return aaf_path


def _ler_fmt(dados: bytes) -> dict:
    """Le o RIFF e devolve tag, bits, canais, taxa e tamanho do chunk data."""
    pos = 12
    fmt = None
    tamanho_data = None
    while pos + 8 <= len(dados):
        cid = dados[pos : pos + 4]
        tamanho = struct.unpack_from("<I", dados, pos + 4)[0]
        if cid == b"fmt ":
            tag, canais, taxa, _byte_rate, _align, bits = struct.unpack_from(
                "<HHIIHH", dados, pos + 8
            )
            fmt = {"tag": tag, "bits": bits, "canais": canais, "taxa": taxa}
        elif cid == b"data":
            tamanho_data = tamanho
        pos += 8 + tamanho + (tamanho % 2)
    assert fmt is not None, "o WAV nao tem chunk fmt"
    assert tamanho_data is not None, "o WAV nao tem chunk data"
    fmt["dados"] = tamanho_data
    return fmt


def _sem_locator(descriptor) -> bool:
    """True quando o WAVEDescriptor nao carrega nenhum Locator."""
    try:
        valor = descriptor["Locator"].value
    except KeyError:
        return True
    return not list(valor)


def _source_mob_do_master(f):
    """SourceMob alcancado pela cadeia MasterMob -> SourceClip -> SourceMob."""
    master = next(m for m in f.content.mobs if type(m).__name__ == "MasterMob")
    for slot in master.slots:
        segmento = slot.segment
        for comp in getattr(segmento, "components", [segmento]):
            if type(comp).__name__ == "SourceClip":
                return comp.mob
    raise AssertionError("o MasterMob nao tem SourceClip apontando para um SourceMob")


@pytest.mark.parametrize("formato,tag,bits,codec_name", FORMATOS)
def test_import_extrai_wav_embedido_byte_identico(tmp_path, formato, tag, bits, codec_name):
    """Com o WAV fora do disco, o import extrai a essencia embedded sem perder byte."""
    wav_path = tmp_path / "fonte.wav"
    wav_bytes = _wav_bytes(formato)
    wav_path.write_bytes(wav_bytes)

    aaf_path = _construir_aaf_embedido(tmp_path / "embed.aaf", wav_path, codec_name)

    # Relink impossivel: o unico WAV do tmp_path e o fixture sintetico que
    # acabamos de remover.
    wav_path.unlink()

    als = tmp_path / "saida.als"
    resultado = main(
        [
            "import",
            str(aaf_path),
            str(als),
            "--media",
            "extract",
            "--fades",
            "keep",
            "--live-origin",
            "00:00:00:00",
        ]
    )
    assert resultado == 0, "import --media extract deveria retornar exit 0"

    projeto = tmp_path / "saida Project" / "Samples" / "Imported"
    wavs = list(projeto.glob("*.wav"))
    assert len(wavs) == 1, f"Esperava 1 WAV extraido em {projeto}, encontrou {len(wavs)}: {wavs}"

    extraido = wavs[0]
    dados = extraido.read_bytes()
    assert dados == wav_bytes, "o WAV extraido precisa ser byte a byte igual ao original"

    lido = _ler_fmt(dados)
    assert lido["tag"] == tag, f"tag do fmt diferente: {lido['tag']} != {tag}"
    assert lido["bits"] == bits, f"bits por amostra diferentes: {lido['bits']} != {bits}"
    assert lido["canais"] == 1, f"o WAV extraido deveria ser mono: {lido['canais']}"
    assert lido["taxa"] == SAMPLE_RATE, f"taxa diferente: {lido['taxa']}"
    assert lido["dados"] == FRAMES * (bits // 8), f"geometria do audio diferente: {lido['dados']}"

    root = ET.parse(gzip.open(str(als))).getroot()
    caminhos = [
        elem.get("Value", "")
        for elem in root.iter("Path")
        if elem.get("Value", "").lower().endswith(".wav")
    ]
    assert caminhos, "o .als gerado nao referencia nenhum WAV"
    for caminho in caminhos:
        assert Path(caminho).is_absolute(), f"Esperado caminho absoluto, obtido: {caminho!r}"
        assert Path(caminho).read_bytes() == wav_bytes, "o WAV apontado pelo .als divergiu"


@pytest.mark.parametrize("formato,tag,bits,codec_name", FORMATOS)
def test_aaf_reaberto_tem_essencia_integral_sem_locator(tmp_path, formato, tag, bits, codec_name):
    """Reabre o AAF e prova a forma do embed: sem Locator e com a essencia no SourceMob."""
    wav_path = tmp_path / "fonte.wav"
    wav_bytes = _wav_bytes(formato)
    wav_path.write_bytes(wav_bytes)

    aaf_path = _construir_aaf_embedido(tmp_path / "embed.aaf", wav_path, codec_name)
    wav_path.unlink()

    with aaf2.open(str(aaf_path), "r") as f:
        source = _source_mob_do_master(f)
        assert type(source).__name__ == "SourceMob", "a cadeia do MasterMob nao chega num SourceMob"
        assert source.essence is not None, "o SourceMob do arquivo precisa ter essencia"
        assert _sem_locator(source.descriptor), "o WAVEDescriptor nao pode manter Locator externo"

        essencias = list(f.content.essencedata)
        assert len(essencias) == 1, f"esperava 1 EssenceData, encontrei {len(essencias)}"
        essence = essencias[0]
        assert str(essence.mob.mob_id) == str(source.mob_id), (
            "o EssenceData tem de apontar para o SourceMob do arquivo; "
            f"essence={essence.mob.mob_id} source={source.mob_id}"
        )
        dados = essence.open().read()

    assert dados == wav_bytes, "a essencia embedded nao guarda o RIFF completo"
