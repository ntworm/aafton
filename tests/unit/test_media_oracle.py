import hashlib
from pathlib import Path
import pytest

from tests.aaf_factory import wav_bytes
from aafton.media_oracle import (
    MediaAuditReport,
    MediaFileResult,
    verify_media_file,
    verify_session_media,
)


@pytest.fixture
def valid_wav(tmp_path: Path) -> Path:
    wav_path = tmp_path / "valid.wav"
    wav_path.write_bytes(wav_bytes(frames=4800, channels=1, sample_rate=48000))
    return wav_path


def test_verify_media_file_integro(valid_wav: Path) -> None:
    expected_hash = hashlib.sha256(valid_wav.read_bytes()).hexdigest()

    # Com expected_hash
    res = verify_media_file(valid_wav, expected_hash=expected_hash)
    assert isinstance(res, MediaFileResult)
    assert res.is_valid is True
    assert res.status == "ok"
    assert res.sha256 == expected_hash
    assert res.expected_hash == expected_hash
    assert res.size_bytes == valid_wav.stat().st_size
    assert res.error_message is None

    # Sem expected_hash
    res_no_exp = verify_media_file(valid_wav)
    assert res_no_exp.is_valid is True
    assert res_no_exp.status == "ok"
    assert res_no_exp.sha256 == expected_hash
    assert res_no_exp.expected_hash is None


def test_verify_media_file_ausente(tmp_path: Path) -> None:
    missing_path = tmp_path / "nao_existe.wav"
    res = verify_media_file(missing_path, expected_hash="abcdef")
    assert res.is_valid is False
    assert res.status == "missing"
    assert res.sha256 is None
    assert res.error_message is not None
    assert "does not exist" in res.error_message.lower() or "not found" in res.error_message.lower()


def test_verify_media_file_hash_divergente(valid_wav: Path) -> None:
    wrong_hash = "0" * 64
    res = verify_media_file(valid_wav, expected_hash=wrong_hash)
    assert res.is_valid is False
    assert res.status == "hash_mismatch"
    assert res.sha256 != wrong_hash
    assert res.expected_hash == wrong_hash
    assert res.error_message is not None


def test_verify_media_file_corrompido_cabecalho(tmp_path: Path) -> None:
    corrupt_path = tmp_path / "corrupt_header.wav"
    corrupt_path.write_bytes(b"NOT_A_WAV_HEADER_AT_ALL")

    res = verify_media_file(corrupt_path)
    assert res.is_valid is False
    assert res.status == "corrupt"
    assert res.error_message is not None


def test_verify_media_file_truncado(tmp_path: Path) -> None:
    # Cabeçalho RIFF incompleto (< 12 bytes)
    trunc_short = tmp_path / "trunc_short.wav"
    trunc_short.write_bytes(b"RIFF\x20\x00")
    res_short = verify_media_file(trunc_short)
    assert res_short.is_valid is False
    assert res_short.status == "corrupt"

    # Cabeçalho declarando tamanho maior que o arquivo real
    full_wav = wav_bytes(frames=4800, channels=1, sample_rate=48000)
    trunc_body = tmp_path / "trunc_body.wav"
    # Salva apenas o cabeçalho de 44 bytes sem os dados de áudio declarados
    trunc_body.write_bytes(full_wav[:44])
    res_body = verify_media_file(trunc_body)
    assert res_body.is_valid is False
    assert res_body.status == "corrupt"


def test_verify_session_media_todos_integros(tmp_path: Path) -> None:
    media_dir = tmp_path / "media"
    media_dir.mkdir()

    f1 = media_dir / "fala1.wav"
    f2 = media_dir / "fala2.wav"
    b1 = wav_bytes(2400)
    b2 = wav_bytes(4800)
    f1.write_bytes(b1)
    f2.write_bytes(b2)

    h1 = hashlib.sha256(b1).hexdigest()
    h2 = hashlib.sha256(b2).hexdigest()
    hashes = {"fala1.wav": h1, "fala2.wav": h2}

    report = verify_session_media(media_dir, media_hashes=hashes)
    assert isinstance(report, MediaAuditReport)
    assert report.is_valid is True
    assert report.total_files == 2
    assert report.ok_count == 2
    assert report.missing_count == 0
    assert report.corrupt_count == 0
    assert report.mismatch_count == 0
    assert len(report.results) == 2


def test_verify_session_media_com_falhas_mistas(tmp_path: Path) -> None:
    media_dir = tmp_path / "media"
    media_dir.mkdir()

    # 1. OK
    f_ok = media_dir / "ok.wav"
    b_ok = wav_bytes(2400)
    f_ok.write_bytes(b_ok)
    h_ok = hashlib.sha256(b_ok).hexdigest()

    # 2. Hash mismatch
    f_mis = media_dir / "mismatch.wav"
    f_mis.write_bytes(wav_bytes(2400))
    h_mis = "f" * 64

    # 3. Corrupt
    f_cor = media_dir / "corrupt.wav"
    f_cor.write_bytes(b"LIXO_DE_ARQUIVO")
    h_cor = hashlib.sha256(b"LIXO_DE_ARQUIVO").hexdigest()

    # 4. Missing (não criar arquivo)
    h_missing = "e" * 64

    hashes = {
        "ok.wav": h_ok,
        "mismatch.wav": h_mis,
        "corrupt.wav": h_cor,
        "missing.wav": h_missing,
    }

    report = verify_session_media(media_dir, media_hashes=hashes)
    assert report.is_valid is False
    assert report.total_files == 4
    assert report.ok_count == 1
    assert report.missing_count == 1
    assert report.corrupt_count == 1
    assert report.mismatch_count == 1


def test_verify_session_media_via_sidecar(tmp_path: Path) -> None:
    media_dir = tmp_path / "media"
    media_dir.mkdir()

    f1 = media_dir / "take1.wav"
    b1 = wav_bytes(1000)
    f1.write_bytes(b1)
    h1 = hashlib.sha256(b1).hexdigest()

    sidecar_json = tmp_path / "sessao.aafton.json"
    sidecar_content = {"format_version": 2, "media_hashes": {"take1.wav": h1}}
    import json

    sidecar_json.write_text(json.dumps(sidecar_content), encoding="utf-8")

    report = verify_session_media(media_dir, sidecar_path=sidecar_json)
    assert report.is_valid is True
    assert report.total_files == 1
    assert report.ok_count == 1


def test_verify_session_media_sem_hashes_escaneia_diretorio(tmp_path: Path) -> None:
    media_dir = tmp_path / "media_scan"
    media_dir.mkdir()

    for i in range(3):
        f = media_dir / f"audio_{i}.wav"
        f.write_bytes(wav_bytes(1000 * (i + 1)))

    report = verify_session_media(media_dir)
    assert report.is_valid is True
    assert report.total_files == 3
    assert report.ok_count == 3
