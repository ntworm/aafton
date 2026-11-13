# tests/unit/test_cli.py
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import aafton
from aafton.cli import build_parser, main, run_cli
from tests.aaf_factory import build_linked_aaf, wav_bytes

TEMPLATE = Path("templates/live12.als")


@pytest.fixture
def wav(tmp_path):
    caminho = tmp_path / "cena12_tk3.wav"
    caminho.write_bytes(wav_bytes(48000 * 120))
    return caminho


@pytest.fixture
def aaf(tmp_path, wav):
    return build_linked_aaf(tmp_path / "cena12.aaf", [(0, 96000, 24000), (192000, 96000, 0)], wav)


def test_sem_argumento_devolve_codigo_de_uso():
    assert main([]) == 2


def test_comando_desconhecido_devolve_codigo_de_uso():
    with pytest.raises(SystemExit) as exc:
        main(["converter-tudo"])
    assert exc.value.code == 2


def test_inspect_de_aaf_emite_json_com_tracks_e_timecode(aaf, capsys):
    assert main(["inspect", str(aaf)]) == 0
    dados = json.loads(capsys.readouterr().out)
    assert dados["sample_rate"] == 48000
    assert dados["start_timecode"] == "01:00:00:00"
    assert len(dados["tracks"]) == 1
    assert len(dados["tracks"][0]["clips"]) == 2


def test_import_recusa_live_origin_invalido(aaf, tmp_path):
    codigo = main(
        ["import", str(aaf), str(tmp_path / "saida.als"), "--live-origin", "nao-e-timecode"]
    )
    assert codigo == 2


@pytest.mark.skipif(not TEMPLATE.exists(), reason="Portao B: template ainda nao autorado")
def test_import_gera_als_e_sidecar_e_relatorio(aaf, tmp_path):
    saida = tmp_path / "saida.als"
    relatorio = tmp_path / "relatorio.json"
    codigo = main(
        [
            "import",
            str(aaf),
            str(saida),
            "--template",
            str(TEMPLATE),
            "--media",
            "link",
            "--report",
            str(relatorio),
        ]
    )
    assert codigo == 0
    assert saida.exists()
    assert (tmp_path / "saida.aafton.json").exists()
    assert json.loads(relatorio.read_text(encoding="utf-8"))["counts"]["error"] == 0


@pytest.mark.skipif(not TEMPLATE.exists(), reason="Portao B: template ainda nao autorado")
def test_verify_aprova_o_par_gerado_pelo_import(aaf, tmp_path, capsys):
    saida = tmp_path / "saida.als"
    main(["import", str(aaf), str(saida), "--template", str(TEMPLATE), "--media", "link"])
    capsys.readouterr()
    assert main(["verify", str(aaf), str(saida)]) == 0
    assert "TC AAF" in capsys.readouterr().out


@pytest.mark.skipif(not TEMPLATE.exists(), reason="Portao B: template ainda nao autorado")
def test_export_reconstroi_o_aaf_a_partir_do_als(aaf, tmp_path):
    als = tmp_path / "saida.als"
    main(["import", str(aaf), str(als), "--template", str(TEMPLATE), "--media", "link"])
    volta = tmp_path / "volta.aaf"
    assert main(["export", str(als), str(volta)]) == 0
    assert volta.exists()


@pytest.mark.skipif(not TEMPLATE.exists(), reason="Portao B: template ainda nao autorado")
def test_import_com_opcao_fades_grava_no_sidecar(aaf, tmp_path):
    saida = tmp_path / "saida_keep.als"
    codigo = main(
        [
            "import",
            str(aaf),
            str(saida),
            "--template",
            str(TEMPLATE),
            "--media",
            "link",
            "--fades",
            "keep",
        ]
    )
    assert codigo == 0
    sidecar_data = json.loads((tmp_path / "saida_keep.aafton.json").read_text(encoding="utf-8"))
    assert sidecar_data["fades"] == "keep"


def test_pipeline_order_session_text_before_stereo_pairing(aaf, tmp_path, monkeypatch):
    calls = []

    def fake_process_stereo_pairing(*args, **kwargs):
        calls.append("stereo")

    def fake_process_session_text(*args, **kwargs):
        calls.append("text")

    def fake_process_video(*args, **kwargs):
        calls.append("video")

    import aafton.stereo as mod_stereo
    import aafton.session_text as mod_session_text
    import aafton.video as mod_video

    monkeypatch.setattr(mod_stereo, "process_stereo_pairing", fake_process_stereo_pairing)
    monkeypatch.setattr(mod_session_text, "process_session_text", fake_process_session_text)
    monkeypatch.setattr(mod_video, "process_video", fake_process_video)

    saida = tmp_path / "ordem.als"
    main(
        [
            "import",
            str(aaf),
            str(saida),
            "--template",
            str(TEMPLATE),
        ]
    )

    assert calls.index("text") < calls.index("stereo")
    assert calls.index("text") < calls.index("video")


def test_fractional_timecode_requires_drop_frame_and_rejects_integer_text(tmp_path, wav):
    import json
    from aafton.aaf_read import read_aaf
    from aafton.report import Report
    from tests.aaf_factory import build_linked_aaf

    # Invariante 1: fps=30, drop=True -> TimecodeRate(30000, 1001, True)
    aaf_drop = build_linked_aaf(tmp_path / "t_drop.aaf", [(1600, 48000, 0)], wav, fps=30, drop=True)
    report = Report()
    session_drop = read_aaf(aaf_drop, report, extract_dir=tmp_path / "media1")
    assert session_drop.timecode_rate.num == 30000
    assert session_drop.timecode_rate.den == 1001
    assert session_drop.timecode_rate.drop_frame is True

    # Invariante 2: fps=30, drop=False -> TimecodeRate(30, 1, False)
    aaf_ndf = build_linked_aaf(tmp_path / "t_ndf.aaf", [(1600, 48000, 0)], wav, fps=30, drop=False)
    session_ndf = read_aaf(aaf_ndf, report, extract_dir=tmp_path / "media2")
    assert session_ndf.timecode_rate.num == 30
    assert session_ndf.timecode_rate.den == 1
    assert session_ndf.timecode_rate.drop_frame is False

    # Invariante 3: AAF drop=True + Texto 30 Frame (NDF) -> SESSION_TEXT_MISMATCH
    txt = tmp_path / "t.txt"
    txt.write_text(
        "SESSION NAME:\tTest\nSAMPLE RATE:\t48000\nTIMECODE FORMAT:\t30 Frame\nSESSION START TIMECODE:\t00:00:00:00\n",
        encoding="utf-8",
    )

    als = tmp_path / "t.als"
    report_json = tmp_path / "report.json"

    from aafton.cli import main

    args = [
        "import",
        str(aaf_drop),
        str(als),
        "--session-text",
        str(txt),
        "--report",
        str(report_json),
        "--permissive",
    ]
    assert main(args) == 1

    with open(report_json, "r") as f:
        rep = json.load(f)

    issues = [i["code"] for i in rep["issues"]]
    assert issues.count("SESSION_TEXT_MISMATCH") == 1
    assert issues.count("TIMECODE_RATE_FROM_TEXT") == 0


def test_premiere_embedded_picture_slot_skipped(tmp_path):
    from aafton.cli import main
    from tests.support.paths import private_fixture

    fixture_premiere = private_fixture("dialect_premiere.aaf")
    import gzip
    import xml.etree.ElementTree as ET

    als = tmp_path / "pr.als"
    report = tmp_path / "report.json"

    args = [
        "import",
        str(fixture_premiere),
        str(als),
        "--report",
        str(report),
        "--permissive",
    ]
    main(args)

    with gzip.open(als, "rt", encoding="utf-8") as f:
        root = ET.fromstring(f.read())

    # Assert there is no track named "Track 1" (which was the video track)
    tracks = root.findall(".//AudioTrack")
    assert len(tracks) == 8
    for track in tracks:
        name = track.find("Name/UserName").get("Value", "")
        assert name != "Track 1"

    import json

    with open(report, "r") as f:
        rep = json.load(f)

    issues = [i["code"] for i in rep["issues"]]
    assert "VIDEO_TRACK_SKIPPED_ON_IMPORT" in issues


def test_version_exibe_versao_e_sai_zero(capsys):
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    texto = pyproject.read_text(encoding="utf-8")
    encontrado = re.search(r'^version = "(.+)"$', texto, re.MULTILINE)
    assert encontrado is not None
    assert aafton.__version__ == encontrado.group(1)

    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out == f"aafton {aafton.__version__}\n"


def test_todo_argumento_tem_help():
    parser = build_parser()
    subparsers = next(
        action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
    )
    for nome in (
        "import",
        "export",
        "inspect",
        "verify",
        "audit-audio",
        "turnover",
        "ltc",
        "ui",
        "gui",
    ):
        for acao in subparsers.choices[nome]._actions:
            if acao.dest == "help":
                continue
            assert acao.help, f"{nome}: {acao.dest} sem texto de ajuda"


@pytest.mark.parametrize(
    ("comando", "ajuda"),
    [
        ("import", "source file (.aaf, .edl, .xml or .fcpxml)"),
        (
            "export",
            "AAF media: link references the files; embed incorporates the audio "
            "(default: link; other formats ignore this option)",
        ),
        ("inspect", "file to inspect (.aaf, .als or exchange format)"),
        ("verify", "deviation tolerance in samples; without a value, the AAF regime applies"),
        ("audit-audio", "folder with the reference WAVs"),
    ],
)
def test_help_dos_subcomandos_descreve_opcoes(capsys, comando, ajuda):
    with pytest.raises(SystemExit) as exc:
        main([comando, "--help"])
    assert exc.value.code == 0
    assert ajuda in " ".join(capsys.readouterr().out.split())


def test_run_cli_arquivo_inexistente_retorna_2_sem_traceback(tmp_path, capsys):
    inexistente = tmp_path / "inexistente.als"
    codigo = run_cli(["inspect", str(inexistente)])
    lido = capsys.readouterr()
    assert codigo == 2
    assert lido.err == f"error: file not found: {inexistente}\n"
    assert "Traceback" not in lido.err

    codigo = run_cli(["export", str(inexistente), str(tmp_path / "out.aaf")])
    lido = capsys.readouterr()
    assert codigo == 2
    assert lido.err == f"error: file not found: {inexistente}\n"


def test_run_cli_erro_sem_filename_nao_duplica_prefixo(monkeypatch, tmp_path, capsys):
    def read_session_quebrada(*args, **kwargs):
        raise FileNotFoundError("Arquivo de sessao nao encontrado: x")

    monkeypatch.setattr("aafton.cli.read_session", read_session_quebrada)
    codigo = run_cli(["inspect", str(tmp_path / "qualquer.als")])
    lido = capsys.readouterr()
    assert codigo == 2
    assert lido.err == "error: Arquivo de sessao nao encontrado: x\n"


def test_run_cli_keyboard_interrupt_retorna_130(monkeypatch, tmp_path, capsys):
    def read_session_interrompida(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr("aafton.cli.read_session", read_session_interrompida)
    codigo = run_cli(["inspect", str(tmp_path / "qualquer.als")])
    lido = capsys.readouterr()
    assert codigo == 130
    assert lido.err == "\noperation cancelled by the user.\n"


def test_main_preserva_excecao_para_o_oraculo(tmp_path):
    with pytest.raises(FileNotFoundError):
        main(["inspect", str(tmp_path / "inexistente.als")])


def test_entrada_do_terminal_aponta_para_run_cli():
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    texto = pyproject.read_text(encoding="utf-8")
    assert 'aafton = "aafton.cli:run_cli"' in texto


def test_modulo_no_terminal_nao_mostra_traceback(tmp_path):
    inexistente = tmp_path / "inexistente.als"
    resultado = subprocess.run(
        [sys.executable, "-m", "aafton.cli", "inspect", str(inexistente)],
        env={**os.environ, "PYTHONPATH": str(Path(aafton.__file__).parents[1])},
        capture_output=True,
        text=True,
        check=False,
    )
    assert resultado.returncode == 2
    assert resultado.stderr == f"error: file not found: {inexistente}\n"
    assert "Traceback" not in resultado.stderr
