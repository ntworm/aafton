import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path
import pytest

from aafton.als_xml import save_als
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
    media_dir = src_dir / "media"
    media_dir.mkdir()

    w1 = media_dir / "fala1.wav"
    w2 = media_dir / "fala2.wav"
    b1 = wav_bytes(4800)
    b2 = wav_bytes(9600)
    w1.write_bytes(b1)
    w2.write_bytes(b2)

    root = ET.Element("Ableton", Creator="Ableton Live 12.4.6")
    live = ET.SubElement(root, "LiveSet")
    tracks = ET.SubElement(live, "Tracks")
    for idx, (w, name) in enumerate([(w1, "fala1"), (w2, "fala2")]):
        track = ET.SubElement(tracks, "AudioTrack", Id=str(10 + idx))
        eff = ET.SubElement(track, "Name")
        ET.SubElement(eff, "EffectiveName", Value=name)
        events = ET.SubElement(track, "DeviceChain")
        seq = ET.SubElement(events, "MainSequencer")
        sample = ET.SubElement(seq, "Sample")
        arr = ET.SubElement(sample, "ArrangerAutomation")
        evts = ET.SubElement(arr, "Events")
        clip = ET.SubElement(evts, "AudioClip", Id=str(idx), Time="0.0")
        ET.SubElement(clip, "CurrentStart", Value="0.0")
        ET.SubElement(clip, "CurrentEnd", Value="1.0")
        ET.SubElement(clip, "Name", Value=name)
        sref = ET.SubElement(clip, "SampleRef")
        fref = ET.SubElement(sref, "FileRef")
        ET.SubElement(fref, "RelativePathType", Value="1")
        ET.SubElement(fref, "RelativePath", Value=f"media/{w.name}")
        ET.SubElement(fref, "Path", Value=str(w))
    main_track = ET.SubElement(live, "MainTrack")
    mixer = ET.SubElement(main_track, "DeviceChain")
    mix = ET.SubElement(mixer, "Mixer")
    tempo = ET.SubElement(mix, "Tempo")
    ET.SubElement(tempo, "Manual", Value="60.0")
    save_als(root, session_file)

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
            output_dir=turnover_fixture["output_dir"],
            strict=True,
        )
    assert "corromp" in str(exc_info.value).lower() or "fail" in str(exc_info.value).lower()


def test_create_turnover_package_modo_permissivo(turnover_fixture):
    # Corrompe um dos arquivos de midia mas roda em permissive
    turnover_fixture["w1"].write_bytes(b"CORRUPTED_NOT_A_WAV")

    res = create_turnover_package(
        session_file=turnover_fixture["session_file"],
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
            "--output",
            str(out_dir),
        ]
    )
    assert ret != 0
