import hashlib
import json
from pathlib import Path
import pytest

from tests.aaf_factory import wav_bytes
from aafton.cli import main
from aafton.turnover import (
    TurnoverError,
    TurnoverResult,
    create_turnover_package,
)


@pytest.fixture
def turnover_fixture(tmp_path: Path):
    src_dir = tmp_path / "source"
    src_dir.mkdir()

    session_file = src_dir / "cena12.als"
    import gzip

    session_file.write_bytes(gzip.compress(b"<Ableton><LiveSet/></Ableton>"))

    media_dir = src_dir / "media"
    media_dir.mkdir()

    w1 = media_dir / "fala1.wav"
    w2 = media_dir / "fala2.wav"
    b1 = wav_bytes(4800)
    b2 = wav_bytes(9600)
    w1.write_bytes(b1)
    w2.write_bytes(b2)

    h1 = hashlib.sha256(b1).hexdigest()
    h2 = hashlib.sha256(b2).hexdigest()

    sidecar_file = src_dir / "cena12.aafton.json"
    sidecar_data = {
        "format_version": 2,
        "sample_rate": 48000,
        "media_hashes": {
            "fala1.wav": h1,
            "fala2.wav": h2,
        },
    }
    sidecar_file.write_text(json.dumps(sidecar_data), encoding="utf-8")

    output_dir = tmp_path / "pacote_entrega"

    return {
        "session_file": session_file,
        "sidecar_file": sidecar_file,
        "media_dir": media_dir,
        "output_dir": output_dir,
        "w1": w1,
        "w2": w2,
        "h1": h1,
        "h2": h2,
    }


def test_create_turnover_package_sucesso_com_sidecar(turnover_fixture):
    res = create_turnover_package(
        session_file=turnover_fixture["session_file"],
        media_dir=turnover_fixture["media_dir"],
        output_dir=turnover_fixture["output_dir"],
        session_name="Cena12_Final",
    )

    assert isinstance(res, TurnoverResult)
    assert res.is_valid is True
    assert res.package_dir == turnover_fixture["output_dir"]

    # Verifica estrutura de arquivos gerada
    pkg = res.package_dir
    assert (pkg / "cena12.als").exists()
    assert (pkg / "cena12.aafton.json").exists()
    assert (pkg / "media" / "fala1.wav").exists()
    assert (pkg / "media" / "fala2.wav").exists()
    assert (pkg / "manifest.json").exists()
    assert (pkg / "turnover_report.txt").exists()

    # Valida manifest.json
    manifest = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["session_name"] == "Cena12_Final"
    assert manifest["generator"] == "aafton-turnover"
    assert "created_at" in manifest
    assert "files" in manifest

    files = manifest["files"]
    assert "cena12.als" in files
    assert "cena12.aafton.json" in files
    assert "media/fala1.wav" in files
    assert "media/fala2.wav" in files

    # Verifica se os hashes no manifesto correspondem aos arquivos em disco
    for rel_path, meta in files.items():
        actual_bytes = (pkg / rel_path).read_bytes()
        actual_hash = hashlib.sha256(actual_bytes).hexdigest()
        assert meta["sha256"] == actual_hash
        assert meta["size_bytes"] == len(actual_bytes)

    # Valida turnover_report.txt
    report_text = (pkg / "turnover_report.txt").read_text(encoding="utf-8")
    assert "Cena12_Final" in report_text
    assert "cena12.als" in report_text
    assert "media/fala1.wav" in report_text
    assert "INTEGRO" in report_text or "VALID" in report_text or "STATUS: OK" in report_text


def test_create_turnover_package_modo_estrito_falha_com_midia_corrompida(turnover_fixture):
    # Corrompe um dos arquivos de midia
    turnover_fixture["w1"].write_bytes(b"CORRUPTED_NOT_A_WAV")

    with pytest.raises(TurnoverError) as exc_info:
        create_turnover_package(
            session_file=turnover_fixture["session_file"],
            media_dir=turnover_fixture["media_dir"],
            output_dir=turnover_fixture["output_dir"],
            strict=True,
        )
    assert "corromp" in str(exc_info.value).lower() or "fail" in str(exc_info.value).lower()


def test_create_turnover_package_modo_permissivo(turnover_fixture):
    # Corrompe um dos arquivos de midia mas roda em permissive
    turnover_fixture["w1"].write_bytes(b"CORRUPTED_NOT_A_WAV")

    res = create_turnover_package(
        session_file=turnover_fixture["session_file"],
        media_dir=turnover_fixture["media_dir"],
        output_dir=turnover_fixture["output_dir"],
        strict=False,
    )
    assert res.is_valid is False
    assert (res.package_dir / "manifest.json").exists()
    assert (res.package_dir / "turnover_report.txt").exists()


def test_cli_turnover_sucesso(turnover_fixture, capsys):
    out_dir = turnover_fixture["output_dir"] / "cli_out"
    ret = main(
        [
            "turnover",
            str(turnover_fixture["session_file"]),
            "--media-dir",
            str(turnover_fixture["media_dir"]),
            "--output",
            str(out_dir),
            "--name",
            "CliSession",
        ]
    )
    assert ret == 0
    captured = capsys.readouterr().out
    assert "CliSession" in captured or (out_dir / "manifest.json").exists()
    assert (out_dir / "manifest.json").exists()


def test_cli_turnover_falha_em_modo_estrito(turnover_fixture):
    turnover_fixture["w1"].write_bytes(b"LIXO")
    out_dir = turnover_fixture["output_dir"] / "cli_fail"
    ret = main(
        [
            "turnover",
            str(turnover_fixture["session_file"]),
            "--media-dir",
            str(turnover_fixture["media_dir"]),
            "--output",
            str(out_dir),
        ]
    )
    assert ret != 0
