"""Testes do preflight de embed: teto de 2 GiB e geometria do WAV.

O preflight e a ultima barreira antes do split stereo e da gravacao da
essencia. Estes testes medem tres invariantes:

1. o teto e conferido sobre os bytes de DATA realmente gravados, nunca
   sobre o arquivo stereo original nem sobre o par de monos do split;
2. a multiplicidade do teto segue os assets de embed_assets: clip repetido
   nao multiplica, alias de asset multiplica;
3. so entra WAV com geometria coerente: bits suportados, canais 1 ou 2, taxa
   positiva, block_align, byte_rate e data alinhados ao fmt.

O teto e injetado por monkeypatch, entao nenhum teste aloca GiB.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from aafton import aaf_write_embed
from aafton.aaf_write_embed import preflight_embed
from aafton.ir import ClipIR, MediaAsset, SessionIR, TrackIR
from aafton.report import Report, StrictModeError
from aafton.timecode import TimecodeRate

SAMPLE_RATE = 48000
PCM = 1
IEEE_FLOAT = 3
GUID_PCM = bytes.fromhex("0100000000001000800000aa00389b71")
GUID_FLOAT = bytes.fromhex("0300000000001000800000aa00389b71")


def _fmt_payload(
    channels: int = 1,
    bits: int = 16,
    tag: int = PCM,
    sample_rate: int = SAMPLE_RATE,
    block_align: int | None = None,
    byte_rate: int | None = None,
    extensible: bool = False,
    subformat: int = PCM,
) -> bytes:
    """Payload de um chunk fmt, com os desvios pedidos no teste."""
    align = channels * (bits // 8)
    if block_align is None:
        block_align = align
    if byte_rate is None:
        byte_rate = sample_rate * block_align
    if not extensible:
        return struct.pack("<HHIIHH", tag, channels, sample_rate, byte_rate, block_align, bits)
    guid = GUID_FLOAT if subformat == IEEE_FLOAT else GUID_PCM
    return (
        struct.pack("<HHIIHH", 0xFFFE, channels, sample_rate, byte_rate, block_align, bits)
        + struct.pack("<HHI", 22, bits, 0)
        + guid
    )


def _wav_bytes(fmt: bytes, data: bytes) -> bytes:
    """RIFF/WAVE com um chunk fmt e um chunk data, com o padding correto."""
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"\x00" * (len(fmt) % 2)
    body += b"data" + struct.pack("<I", len(data)) + data + b"\x00" * (len(data) % 2)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _write_wav(path: Path, fmt: bytes, data: bytes) -> Path:
    path.write_bytes(_wav_bytes(fmt, data))
    return path


def _clip(clip_id: str, media_id: str) -> ClipIR:
    return ClipIR(
        id=clip_id,
        track_id="t1",
        channel_index=0,
        event_index=0,
        name=f"Clip_{clip_id}",
        media_id=media_id,
        timeline_start_samples=0,
        timeline_end_samples=1000,
    )


def _session(assets: list[MediaAsset], clips: list[ClipIR] | None = None) -> SessionIR:
    track = TrackIR(
        id="t1",
        source_index=0,
        name="Audio",
        kind="audio",
        channel_count=1,
        clips=clips or [],
    )
    return SessionIR(
        name="s",
        sample_rate=SAMPLE_RATE,
        timecode_rate=TimecodeRate(25, 1, False),
        start_timecode_samples=0,
        tracks=[track],
        media=assets,
    )


def _asset(path: Path, asset_id: str = "a1") -> MediaAsset:
    return MediaAsset(id=asset_id, filename=str(path), resolved_path=str(path))


def _snapshot(session: SessionIR) -> list:
    """Estado observavel do IR, para provar que o preflight nao muta a sessao."""
    return [
        (
            asset.id,
            asset.filename,
            asset.resolved_path,
            asset.channels,
            asset.sample_rate,
            asset.duration_samples,
            asset.status,
        )
        for asset in session.media
    ] + [
        (track.id, tuple((clip.id, clip.media_id) for clip in track.clips))
        for track in session.tracks
    ]


def test_preflight_limite_injetado_acima_dos_bytes_de_data(tmp_path, monkeypatch):
    """DATA acima do limite injetado reprova o preflight nos dois modos."""
    data = b"\x00" * 200
    wav = _write_wav(tmp_path / "a.wav", _fmt_payload(), data)
    session = _session([_asset(wav)], [_clip("c1", "a1")])

    monkeypatch.setattr(aaf_write_embed, "EMBED_LIMIT_BYTES", len(data) - 1)

    permissive = Report(strict=False)
    assert not preflight_embed(session, permissive)
    excedeu = [i for i in permissive.issues if i.code == "EXPORT_EMBED_EXCEEDS_2GB"]
    assert len(excedeu) == 1
    assert excedeu[0].context["stage"] == "preflight"
    # A soma e dos bytes de DATA, nao do arquivo: o stat tem cabecalho a mais.
    assert excedeu[0].context["size"] == len(data)

    strict = Report(strict=True)
    with pytest.raises(StrictModeError):
        preflight_embed(session, strict)


def test_preflight_limite_no_teto_e_aceito(tmp_path, monkeypatch):
    """DATA exatamente no limite injetado ainda passa pelo preflight."""
    data = b"\x00" * 200
    wav = _write_wav(tmp_path / "a.wav", _fmt_payload(), data)
    session = _session([_asset(wav)], [_clip("c1", "a1")])

    monkeypatch.setattr(aaf_write_embed, "EMBED_LIMIT_BYTES", len(data))

    report = Report(strict=False)
    assert preflight_embed(session, report)
    assert report.count("error") == 0


def test_preflight_clip_repetido_nao_multiplica_o_teto(tmp_path, monkeypatch):
    """Clips que apontam para o mesmo asset nao somam bytes de DATA."""
    data = b"\x00" * 200
    wav = _write_wav(tmp_path / "a.wav", _fmt_payload(), data)
    clips = [_clip("c1", "a1"), _clip("c2", "a1"), _clip("c3", "a1")]

    monkeypatch.setattr(aaf_write_embed, "EMBED_LIMIT_BYTES", len(data))

    session = _session([_asset(wav)], clips)
    report = Report(strict=False)
    assert preflight_embed(session, report)
    assert report.count("error") == 0


@pytest.mark.parametrize("alias_id", ["a1", "a1_alias"])
def test_preflight_alias_de_asset_multiplica_o_teto(tmp_path, monkeypatch, alias_id):
    """Um segundo asset para o mesmo arquivo entra na soma do teto."""
    data = b"\x00" * 200
    wav = _write_wav(tmp_path / "a.wav", _fmt_payload(), data)
    clips = [_clip("c1", "a1")]

    monkeypatch.setattr(aaf_write_embed, "EMBED_LIMIT_BYTES", len(data))

    session = _session([_asset(wav), _asset(wav, alias_id)], clips)
    report = Report(strict=False)
    assert not preflight_embed(session, report)
    excedeu = [i for i in report.issues if i.code == "EXPORT_EMBED_EXCEEDS_2GB"]
    assert len(excedeu) == 1
    assert excedeu[0].context["size"] == 2 * len(data)


@pytest.mark.parametrize(
    "bits,tag,extensible",
    [
        (8, PCM, False),
        (16, PCM, False),
        (24, PCM, False),
        (32, PCM, False),
        (32, PCM, True),
        (32, IEEE_FLOAT, False),
        (32, IEEE_FLOAT, True),
    ],
)
def test_preflight_aceita_pcm_e_float_validos(tmp_path, bits, tag, extensible):
    """PCM 8/16/24/32 e float32, simples ou extensivel, passam no preflight."""
    fmt = _fmt_payload(bits=bits, tag=tag, extensible=extensible, subformat=tag)
    data = b"\x00" * (64 * (bits // 8))
    wav = _write_wav(tmp_path / "ok.wav", fmt, data)
    session = _session([_asset(wav)], [_clip("c1", "a1")])

    report = Report(strict=False)
    assert preflight_embed(session, report)
    assert report.count("error") == 0


def test_preflight_aceita_wav_estereo(tmp_path):
    """WAV stereo de 16 bits e aceito: e a entrada do split."""
    fmt = _fmt_payload(channels=2, bits=16)
    data = b"\x00" * (64 * 2 * 2)
    wav = _write_wav(tmp_path / "stereo.wav", fmt, data)
    session = _session([_asset(wav)], [_clip("c1", "a1")])

    report = Report(strict=False)
    assert preflight_embed(session, report)
    assert report.count("error") == 0


@pytest.mark.parametrize(
    "fmt,data_size,code",
    [
        (_fmt_payload(bits=64, tag=IEEE_FLOAT), 64, "EMBED_BITS_UNSUPPORTED"),
        (
            _fmt_payload(bits=64, extensible=True, subformat=IEEE_FLOAT),
            64,
            "EMBED_BITS_UNSUPPORTED",
        ),
        (_fmt_payload(bits=64), 64, "EMBED_BITS_UNSUPPORTED"),
        (_fmt_payload(bits=12), 24, "EMBED_BITS_UNSUPPORTED"),
        (_fmt_payload(block_align=3), 200, "EMBED_FMT_INVALID"),
        (_fmt_payload(byte_rate=1), 200, "EMBED_FMT_INVALID"),
        (_fmt_payload(sample_rate=0), 200, "EMBED_FMT_INVALID"),
        (_fmt_payload(), 201, "EMBED_DATA_INVALID"),
    ],
)
def test_preflight_rejeita_geometria_desalinhada(tmp_path, fmt, data_size, code):
    """Bits, taxa, block_align, byte_rate e data fora do contrato reprovam."""
    data = b"\x00" * data_size
    wav = _write_wav(tmp_path / "ruim.wav", fmt, data)
    session = _session([_asset(wav)], [_clip("c1", "a1")])

    report = Report(strict=False)
    assert not preflight_embed(session, report)
    assert any(i.code == code for i in report.issues), [i.code for i in report.issues]


def test_preflight_rejeita_fmt_e_data_duplicados(tmp_path):
    """fmt ou data duplicados reprovam: um cabecalho nao descreve outro payload."""
    fmt = _fmt_payload()
    data = b"\x00" * 200

    dup_fmt = (
        b"WAVE"
        + b"fmt "
        + struct.pack("<I", len(fmt))
        + fmt
        + b"fmt "
        + struct.pack("<I", len(fmt))
        + fmt
        + b"data"
        + struct.pack("<I", len(data))
        + data
    )
    dup_fmt_path = tmp_path / "dup_fmt.wav"
    dup_fmt_path.write_bytes(b"RIFF" + struct.pack("<I", len(dup_fmt)) + dup_fmt)

    dup_data = (
        b"WAVE"
        + b"fmt "
        + struct.pack("<I", len(fmt))
        + fmt
        + b"data"
        + struct.pack("<I", len(data))
        + data
        + b"data"
        + struct.pack("<I", len(data))
        + data
    )
    dup_data_path = tmp_path / "dup_data.wav"
    dup_data_path.write_bytes(b"RIFF" + struct.pack("<I", len(dup_data)) + dup_data)

    rep_f = Report(strict=False)
    assert not preflight_embed(_session([_asset(dup_fmt_path)]), rep_f)
    assert any(i.code == "EMBED_FMT_INVALID" for i in rep_f.issues)

    rep_d = Report(strict=False)
    assert not preflight_embed(_session([_asset(dup_data_path)]), rep_d)
    assert any(i.code == "EMBED_DATA_INVALID" for i in rep_d.issues)


def test_preflight_rejeicao_nao_muta_o_ir(tmp_path):
    """Reprovar no preflight nao altera media nem tracks da sessao."""
    fmt = _fmt_payload(bits=64, tag=IEEE_FLOAT)
    data = b"\x00" * 64
    wav = _write_wav(tmp_path / "ruim.wav", fmt, data)
    session = _session([_asset(wav)], [_clip("c1", "a1")])

    antes = _snapshot(session)
    report = Report(strict=False)
    assert not preflight_embed(session, report)
    assert report.count("error") >= 1
    assert _snapshot(session) == antes


def test_preflight_erro_de_leitura_permissivo_e_estrito(tmp_path, monkeypatch):
    """PermissionError ao classificar arquivo existente gera EMBED_METADATA_UNREADABLE."""
    wav = _write_wav(tmp_path / "inacessivel.wav", _fmt_payload(), b"\x00" * 200)
    session = _session([_asset(wav)], [_clip("c1", "a1")])

    target_resolved = str(wav.resolve())
    real_open = Path.open

    def mock_open(self, *args, **kwargs):
        if str(self.resolve()) == target_resolved:
            raise PermissionError("acesso negado na classificacao")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", mock_open)

    permissive = Report(strict=False)
    assert not preflight_embed(session, permissive)
    issues = [i for i in permissive.issues if i.code == "EMBED_METADATA_UNREADABLE"]
    assert len(issues) == 1
    assert "acesso negado" in issues[0].message
    assert issues[0].context["asset_id"] == "a1"

    strict = Report(strict=True)
    with pytest.raises(StrictModeError):
        preflight_embed(session, strict)


def test_preflight_struct_error_cabecalho_curto(tmp_path, monkeypatch):
    """struct.error ao desempacotar metadados gera EMBED_METADATA_UNREADABLE nos dois reports."""
    wav = _write_wav(tmp_path / "curto.wav", _fmt_payload(), b"\x00" * 200)
    session = _session([_asset(wav)], [_clip("c1", "a1")])

    target_str = str(wav)

    def mock_read_meta(path):
        if str(path) == target_str:
            raise struct.error("unpack requires a buffer of 16 bytes")
        return {"data_size": 200, "fmt_payload": b""}

    monkeypatch.setattr("aafton.aaf_write_embed.read_audio_metadata", mock_read_meta)

    permissive = Report(strict=False)
    assert not preflight_embed(session, permissive)
    issues = [i for i in permissive.issues if i.code == "EMBED_METADATA_UNREADABLE"]
    assert len(issues) == 1
    assert issues[0].context["asset_id"] == "a1"

    strict = Report(strict=True)
    with pytest.raises(StrictModeError):
        preflight_embed(session, strict)


def test_preflight_mesmo_id_caminhos_distintos_limite_inferior(tmp_path, monkeypatch):
    """Dois assets com mesmo id e arquivos distintos somam bytes reais sem dedup por id."""
    fmt = _fmt_payload(channels=1, bits=16)
    data100 = b"\x00" * 200
    data1000 = b"\x00" * 2000
    w1 = _write_wav(tmp_path / "w100.wav", fmt, data100)
    w2 = _write_wav(tmp_path / "w1000.wav", fmt, data1000)

    asset1 = MediaAsset(id="a", filename=str(w1), resolved_path=str(w1))
    asset2 = MediaAsset(id="a", filename=str(w2), resolved_path=str(w2))
    session = _session([asset1, asset2], [_clip("c1", "a")])

    # No limite inferior de 2200 DATA bytes (200 + 2000), o preflight passa:
    # a versao anterior superestimava para 4000 ao indexar dict por asset.id.
    monkeypatch.setattr(aaf_write_embed, "EMBED_LIMIT_BYTES", 2200)
    report = Report(strict=False)
    assert preflight_embed(session, report)
    assert report.count("error") == 0

    # Com 2199 bytes, o limite e violado acusando exatamente os 2200 bytes reais
    monkeypatch.setattr(aaf_write_embed, "EMBED_LIMIT_BYTES", 2199)
    report_reprovado = Report(strict=False)
    assert not preflight_embed(session, report_reprovado)
    excedeu = [i for i in report_reprovado.issues if i.code == "EXPORT_EMBED_EXCEEDS_2GB"]
    assert len(excedeu) == 1
    assert excedeu[0].context["size"] == 2200
