"""Fronteiras de streaming do embed e do split com metadata preservada.

Os dois testes medem o teto de 64 KiB por leitura: o embed copia um WAV
maior que dois blocos e o split copia um iXML maior que dois blocos.
"""

import struct
from pathlib import Path
from unittest.mock import patch

import aaf2

from aafton.aaf_write_embed import embed_wave, verify_embedded_aaf
from aafton.audio_split import _iter_riff_chunks, split_stereo_source
from aafton.report import Report

BLOCK = 65536  # teto contratual de bytes lidos por vez
RATE = 48000
FMT_MONO = struct.pack("<HHIIHH", 1, 1, RATE, RATE * 2, 2, 16)
FMT_STEREO = struct.pack("<HHIIHH", 1, 2, RATE, RATE * 4, 4, 16)


class _LeitorLimitado:
    """Arquivo da fonte com leitura barrada acima do teto de 64 KiB."""

    def __init__(self, stream, requests: list[int], received: list[int]) -> None:
        self._stream = stream
        self._requests = requests
        self._received = received

    def read(self, size: int = -1) -> bytes:
        if size < 0 or size > BLOCK:
            raise AssertionError(f"leitura fora do teto de {BLOCK} bytes: {size}")
        data = self._stream.read(size)
        self._requests.append(size)
        self._received.append(len(data))
        return data

    def __enter__(self) -> "_LeitorLimitado":
        return self

    def __exit__(self, *exc_info: object) -> bool:
        self._stream.close()
        return False

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._stream, name)


def _guardar_fonte(fonte: Path, requests: list[int], received: list[int]):
    """Instrumenta so o Path.open da fonte; os demais arquivos passam direto."""
    original = Path.open
    alvo = Path(fonte).resolve()

    def abrir(self, *args, **kwargs):
        stream = original(self, *args, **kwargs)
        if Path(self).resolve() != alvo:
            return stream
        return _LeitorLimitado(stream, requests, received)

    return patch.object(Path, "open", abrir)


def _pcm(frames: int, semente: int) -> bytes:
    """Amostras de 16 bits, deterministicas e nao constantes."""
    bloco = b"".join(struct.pack("<h", ((i * 7919 + semente) % 65536) - 32768) for i in range(1024))
    return (bloco * (frames // 1024 + 1))[: frames * 2]


def _chunk(chunk_id: bytes, payload: bytes) -> bytes:
    """Chunk RIFF com o byte de padding quando o payload e impar."""
    tamanho = len(payload)
    return chunk_id + struct.pack("<I", tamanho) + payload + b"\x00" * (tamanho % 2)


def _wav(fmt: bytes, antes: tuple, depois: tuple, pcm: bytes) -> bytes:
    """RIFF/WAVE com os chunks informados antes e depois do chunk data."""
    corpo = b"WAVE" + _chunk(b"fmt ", fmt)
    for chunk_id, payload in antes:
        corpo += _chunk(chunk_id, payload)
    corpo += _chunk(b"data", pcm)
    for chunk_id, payload in depois:
        corpo += _chunk(chunk_id, payload)
    return b"RIFF" + struct.pack("<I", len(corpo)) + corpo


def _bext_payload() -> bytes:
    """Chunk bext BWF versao 1 (602 bytes) com time reference."""
    payload = (
        b"descricao de origem".ljust(256, b"\x00")
        + b"origem".ljust(32, b"\x00")
        + b"ref-0001".ljust(32, b"\x00")
        + b"2026-01-01".ljust(10, b"\x00")
        + b"12:00:00".ljust(8, b"\x00")
        + struct.pack("<Q", 123456789)
        + struct.pack("<H", 1)
        + b"\x00" * 254
    )
    assert len(payload) == 602
    return payload


def _ixml_grande(tamanho: int) -> bytes:
    """iXML reconhecivel, com tamanho impar e maior que dois blocos."""
    cabeca = b"<BWFXML><PROJECT><SPEED>50</SPEED><SCENE>01</SCENE><NOTE>"
    cauda = b"</NOTE></PROJECT></BWFXML>"
    payload = cabeca + b"A" * (tamanho - len(cabeca) - len(cauda)) + cauda
    assert len(payload) == tamanho
    assert len(payload) % 2 == 1
    return payload


def _payload(path: Path, chunk_id: bytes) -> bytes | None:
    """Payload de um chunk do WAV, lido pelo offset do walker RIFF."""
    for cid, offset, size in _iter_riff_chunks(path):
        if cid == chunk_id:
            with path.open("rb") as stream:
                stream.seek(offset)
                return stream.read(size)
    return None


def _essencia(path: Path, mob_id: str) -> bytes:
    """Bytes da essencia embutida, lidos do AAF reaberto."""
    with aaf2.open(str(path), "r") as f:
        for mob in f.content.mobs:
            if str(mob.mob_id) != mob_id:
                continue
            stream = mob.essence.open()
            try:
                blocos = []
                while True:
                    bloco = stream.read(BLOCK)
                    if not bloco:
                        break
                    blocos.append(bloco)
            finally:
                stream.close()
            return b"".join(blocos)
    raise AssertionError(f"mob {mob_id} ausente no AAF")


def test_embed_wave_copia_wav_acima_de_dois_blocos(tmp_path):
    """embed_wave le a fonte em 64 KiB, grava tudo e o AAF reaberto confere."""
    wav_path = tmp_path / "audio_grande.wav"
    raw = _wav(FMT_MONO, (), (), _pcm(70000, 3))
    wav_path.write_bytes(raw)
    assert len(raw) >= 140000
    aaf_path = tmp_path / "streaming.aaf"
    requests: list[int] = []
    received: list[int] = []

    with aaf2.open(str(aaf_path), "w") as f:
        comp = f.create.CompositionMob("comp")
        f.content.mobs.append(comp)
        meta = {
            "format": {"filename": str(wav_path), "format_name": "wav"},
            "streams": [
                {
                    "codec_type": "audio",
                    "codec_name": "pcm_s16le",
                    "sample_rate": RATE,
                    "duration_ts": 70000,
                    "channels": 1,
                }
            ],
        }
        _master, source, _tape = f.content.link_external_wav(meta)
        with _guardar_fonte(wav_path, requests, received):
            written = embed_wave(f, source, wav_path)
        source_mob_id = str(source.mob_id)

    assert written == len(raw)
    blocos = [tamanho for tamanho in received if tamanho]
    assert len(blocos) == 3
    assert blocos[:2] == [BLOCK, BLOCK]
    assert blocos[2] == len(raw) - 2 * BLOCK
    assert sum(blocos) == len(raw)
    assert max(requests) == BLOCK
    assert _essencia(aaf_path, source_mob_id) == raw
    assert verify_embedded_aaf(aaf_path, {source_mob_id: len(raw)}, Report(strict=True))


def test_split_stereo_source_copia_ixml_acima_de_dois_blocos(tmp_path):
    """O iXML gigante e copiado em 64 KiB, com payload e padding intactos."""
    fonte = tmp_path / "stereo.wav"
    bext, ixml = _bext_payload(), _ixml_grande(131073)
    esquerda = [((i * 7919) % 65536) - 32768 for i in range(64)]
    direita = [((i * 104729) % 65536) - 32768 for i in range(64)]
    stereo = b"".join(struct.pack("<hh", e, d) for e, d in zip(esquerda, direita))
    raw = _wav(FMT_STEREO, ((b"bext", bext),), ((b"iXML", ixml),), stereo)
    fonte.write_bytes(raw)
    requests: list[int] = []
    received: list[int] = []

    with _guardar_fonte(fonte, requests, received):
        saidas = split_stereo_source(fonte, tmp_path / "media", preserve_metadata=True)

    assert len(saidas) == 2
    assert max(requests) == BLOCK
    assert requests.count(BLOCK) >= 2
    assert sum(received) >= len(ixml)
    monos = [
        b"".join(struct.pack("<h", v) for v in esquerda),
        b"".join(struct.pack("<h", v) for v in direita),
    ]
    for saida, esperado in zip(saidas, monos):
        bruto = saida.read_bytes()
        assert bruto == _wav(FMT_MONO, ((b"bext", bext),), ((b"iXML", ixml),), esperado)
        assert struct.unpack_from("<I", bruto, 4)[0] == len(bruto) - 8
        assert len(bruto) % 2 == 0
        assert bruto[-1] == 0  # padding do iXML de tamanho impar
        assert _payload(saida, b"bext") == bext
        assert _payload(saida, b"iXML") == ixml
        assert _payload(saida, b"data") == esperado
    assert fonte.read_bytes() == raw
