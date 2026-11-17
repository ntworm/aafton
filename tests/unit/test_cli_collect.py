"""CLI tests for 'aafton collect'."""

from __future__ import annotations

import json
from pathlib import Path

from aafton.cli import build_parser, main
from tests.aaf_factory import wav_bytes
from tests.unit.test_als_sem_sidecar import clip, montar_set


def _tiny_wav(folder: Path, name: str = "dx.wav") -> Path:
    path = folder / name
    path.write_bytes(wav_bytes(480, 1, 48000))
    return path


def _tiny_video(folder: Path, name: str = "pic.mp4") -> Path:
    path = folder / name
    path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32)
    return path


def test_help_lists_collect_command():
    parser = build_parser()
    help_text = parser.format_help()
    assert "collect" in help_text
    sub_help = parser.parse_args(["collect", "in.als", "--dest", "out"])
    assert sub_help.source == "in.als"
    assert sub_help.dest == "out"
    assert sub_help.name is None
    assert sub_help.no_video is False
    assert sub_help.json is False


def test_collect_exit_0_all_copied_and_progress_on_stderr(tmp_path, capsys):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    wav = _tiny_wav(src_dir, "dialogo.wav")
    als = montar_set(src_dir, [("DX", [clip(wav, 0.0, 2.0)])], nome="cena.als")
    dest = tmp_path / "entrega"

    code = main(["collect", str(als), "--dest", str(dest), "--name", "pacote"])
    captured = capsys.readouterr()

    assert code == 0
    assert "# Collect media report" in captured.out
    assert "Copied: 1" in captured.out
    assert (dest / "pacote.als").is_file()
    assert (dest / "pacote Media" / "dialogo.wav").is_file()
    assert (dest / "collect_report.txt").is_file()
    err_lines = [line for line in captured.err.splitlines() if line.strip()]
    assert len(err_lines) == 1
    assert "dialogo.wav" in err_lines[0]


def test_collect_exit_3_when_media_missing_keeps_delivery(tmp_path, capsys):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    wav = _tiny_wav(src_dir, "ok.wav")
    missing = src_dir / "sumiu.wav"
    als = montar_set(
        src_dir,
        [("DX", [clip(wav, 0.0, 2.0), clip(missing, 2.0, 4.0)])],
        nome="parcial.als",
    )
    dest = tmp_path / "entrega_parcial"

    code = main(["collect", str(als), "--dest", str(dest)])
    captured = capsys.readouterr()

    assert code == 3
    assert "Copied: 1" in captured.out
    assert "Missing: 1" in captured.out
    assert (dest / "parcial.als").is_file()
    assert (dest / "parcial Media" / "ok.wav").is_file()
    assert (dest / "collect_report.txt").is_file()


def test_collect_json_flag_outputs_valid_json_on_stdout(tmp_path, capsys):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    wav = _tiny_wav(src_dir, "boom.wav")
    als = montar_set(src_dir, [("DX", [clip(wav, 0.0, 2.0)])], nome="json_set.als")
    dest = tmp_path / "out_json"

    code = main(["collect", str(als), "--dest", str(dest), "--json"])
    captured = capsys.readouterr()

    assert code == 0
    payload = json.loads(captured.out)
    assert payload["counts"]["copied"] == 1
    assert payload["counts"]["missing"] == 0
    assert len(payload["items"]) == 1
    assert payload["items"][0]["state"] == "copied"
    assert Path(payload["items"][0]["destination"]).name == "boom.wav"


def test_collect_no_video_leaves_video_out(tmp_path, capsys):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    wav = _tiny_wav(src_dir, "audio.wav")
    vid = _tiny_video(src_dir, "corte.mp4")
    als = montar_set(
        src_dir,
        [("DX", [clip(wav, 0.0, 2.0), clip(vid, 2.0, 4.0)])],
        nome="com_video.als",
    )
    dest = tmp_path / "sem_video"

    code = main(["collect", str(als), "--dest", str(dest), "--no-video", "--json"])
    captured = capsys.readouterr()

    assert code == 0
    payload = json.loads(captured.out)
    assert payload["counts"]["copied"] == 1
    assert payload["counts"]["video_skipped"] == 1
    assert (dest / "com_video Media" / "audio.wav").is_file()
    assert not (dest / "com_video Media" / "corte.mp4").exists()


def test_collect_exit_2_on_non_empty_dest_and_invalid_input(tmp_path, capsys):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    wav = _tiny_wav(src_dir, "dx.wav")
    als = montar_set(src_dir, [("DX", [clip(wav, 0.0, 2.0)])], nome="s.als")

    non_empty = tmp_path / "ocupado"
    non_empty.mkdir()
    (non_empty / "lixo.txt").write_text("x", encoding="utf-8")

    code_dest = main(["collect", str(als), "--dest", str(non_empty)])
    err_dest = capsys.readouterr().err
    assert code_dest == 2
    assert "error in the collect:" in err_dest

    bad_input = tmp_path / "nota.txt"
    bad_input.write_text("nao e sessao", encoding="utf-8")
    code_ext = main(["collect", str(bad_input), "--dest", str(tmp_path / "out2")])
    err_ext = capsys.readouterr().err
    assert code_ext == 2
    assert "error in the collect:" in err_ext

    code_missing = main(
        ["collect", str(tmp_path / "inexistente.als"), "--dest", str(tmp_path / "out3")]
    )
    err_missing = capsys.readouterr().err
    assert code_missing == 2
    assert "error in the collect:" in err_missing
