"""Export, inspect e verify de um set do Live sem o registro do aafton, pela CLI."""

from __future__ import annotations

import json
from pathlib import Path

from aafton.aaf_read import read_aaf
from aafton.aaf_write_embed import EMBED_MIXED_RATES
from aafton.cli import build_parser, main
from aafton.report import Report
from aafton.timecode import TimecodeRate
from tests.unit.test_als_sem_sidecar import ESTICADO_104, clip, escrever_wav, montar_set

UMA_HORA_24 = 3600 * 48000


def _aaf(caminho: Path):
    return read_aaf(caminho, Report(strict=False))


def _codigos(relatorio: Path) -> list[str]:
    dados = json.loads(relatorio.read_text(encoding="utf-8"))
    return [issue["code"] for issue in dados["issues"]]


def _set_exato(tmp_path: Path) -> Path:
    wav = escrever_wav(tmp_path, "dx.wav")
    return montar_set(tmp_path, [("DX", [clip(wav, 0.0, 4.0)])])


def _set_com_warp(tmp_path: Path) -> Path:
    """Duas tracks com clip esticado e uma track exata, a 104 BPM."""
    wav = escrever_wav(tmp_path, "stem.wav")
    esticado = clip(wav, 0.0, 8.0, nome="esticado", warp=ESTICADO_104)
    exato = clip(wav, 8.0, 12.0, nome="exato", crossfade=True, loop_start=4.0)
    tracks = [
        ("3-Audio", [esticado, exato]),
        ("5-2", [clip(wav, 0.0, 8.0, warp=ESTICADO_104)]),
        ("DX", [clip(wav, 0.0, 4.0)]),
    ]
    return montar_set(tmp_path, tracks, bpm=104.0)


def test_export_estrito_comeca_em_01h_a_24_fps(tmp_path):
    saida = tmp_path / "saida.aaf"
    relatorio = tmp_path / "relatorio.json"

    assert main(["export", str(_set_exato(tmp_path)), str(saida), "--report", str(relatorio)]) == 0

    sessao = _aaf(saida)
    assert sessao.start_timecode_samples == UMA_HORA_24
    assert sessao.timecode_rate == TimecodeRate(24, 1)
    assert "ALS_START_TC_ASSUMED" in _codigos(relatorio)


def test_start_tc_e_frame_rate_mudam_o_inicio_do_aaf(tmp_path):
    saida = tmp_path / "saida.aaf"
    argumentos = ["--start-tc", "10:00:00:00", "--frame-rate", "25"]

    assert main(["export", str(_set_exato(tmp_path)), str(saida), *argumentos]) == 0

    sessao = _aaf(saida)
    assert sessao.start_timecode_samples == 10 * UMA_HORA_24
    assert sessao.timecode_rate == TimecodeRate(25, 1)


def test_inspect_aceita_29_97_drop_frame(tmp_path, capsys):
    argumentos = ["--start-tc", "01:00:00;00", "--frame-rate", "29.97df"]

    assert main(["inspect", str(_set_exato(tmp_path)), *argumentos]) == 0

    dados = json.loads(capsys.readouterr().out)
    assert dados["timecode_rate"] == {"num": 30000, "den": 1001, "drop_frame": True}


def test_export_estrito_cita_todas_as_tracks_num_unico_erro(tmp_path, capsys):
    saida = tmp_path / "saida.aaf"

    assert main(["export", str(_set_com_warp(tmp_path)), str(saida)]) == 1

    erro = capsys.readouterr().err
    assert erro.count("ALS_RENDER_IN_LIVE") == 1
    assert "3-Audio: 1 clip with warp; 5-2: 1 clip with warp" in erro
    assert not saida.exists()


def test_permissive_sozinho_continua_recusando(tmp_path, capsys):
    saida = tmp_path / "saida.aaf"

    assert main(["export", str(_set_com_warp(tmp_path)), str(saida), "--permissive"]) == 2

    assert "ALS_RENDER_IN_LIVE" in capsys.readouterr().err
    assert not saida.exists()


def test_leave_out_render_exporta_so_os_clips_exatos(tmp_path):
    saida = tmp_path / "saida.aaf"
    relatorio = tmp_path / "relatorio.json"
    argumentos = ["--leave-out-render", "--report", str(relatorio)]

    assert main(["export", str(_set_com_warp(tmp_path)), str(saida), *argumentos]) == 0

    sessao = _aaf(saida)
    assert sum(len(track.clips) for track in sessao.tracks) == 2
    dados = json.loads(relatorio.read_text(encoding="utf-8"))
    notas = [issue for issue in dados["issues"] if issue["code"] == "ALS_CLIPS_LEFT_OUT"]
    assert [nota["context"]["track"] for nota in notas] == ["3-Audio", "5-2"]
    assert notas[0]["context"]["crossfade_removed"] == ["exato"]
    assert notas[1]["context"]["track_removed"] is True


def test_video_numa_track_mista_nao_vai_para_o_aaf(tmp_path):
    wav = escrever_wav(tmp_path, "dx.wav")
    video = tmp_path / "1015.mp4"
    tracks = [("DX", [clip(video, 0.0, 4.0), clip(wav, 4.0, 8.0)])]
    caminho = montar_set(tmp_path, tracks)
    saida = tmp_path / "saida.aaf"
    relatorio = tmp_path / "relatorio.json"

    assert main(["export", str(caminho), str(saida), "--report", str(relatorio)]) == 0

    codigos = _codigos(relatorio)
    assert "WRITE_MEDIA_FORMAT_UNSUPPORTED" not in codigos
    assert "ALS_VIDEO_LEFT_OUT" in codigos
    assert sum(len(track.clips) for track in _aaf(saida).tracks) == 1


def test_embed_com_taxas_mistas_e_recusado(tmp_path, capsys):
    a = escrever_wav(tmp_path, "a.wav", 48000)
    b = escrever_wav(tmp_path, "b.wav", 44100)
    caminho = montar_set(tmp_path, [("A", [clip(a, 0.0, 4.0)]), ("B", [clip(b, 0.0, 4.0)])])
    saida = tmp_path / "saida.aaf"

    assert main(["export", str(caminho), str(saida), "--media", "embed"]) != 0

    assert EMBED_MIXED_RATES in capsys.readouterr().err
    assert not saida.exists()


def test_verify_e_inspect_aceitam_as_opcoes_do_set():
    parser = build_parser()
    for comando in (["verify", "a.aaf", "b.als"], ["inspect", "b.als"]):
        args = parser.parse_args(
            [*comando, "--start-tc", "10:00:00:00", "--frame-rate", "25", "--leave-out-render"]
        )
        assert args.start_tc == "10:00:00:00"
        assert args.frame_rate == TimecodeRate(25, 1)
        assert args.leave_out_render is True


def test_cli_verify_sem_sidecar_usa_aaf_sem_start_e_reprova_start_diferente(tmp_path, capsys):
    als = _set_exato(tmp_path)
    saida = tmp_path / "saida.aaf"
    opcoes = ["--start-tc", "10:00:00:00", "--frame-rate", "25"]
    assert main(["export", str(als), str(saida), *opcoes]) == 0
    capsys.readouterr()

    assert main(["verify", str(saida), str(als)]) == 0
    assert "start taken from the AAF (10:00:00:00 at 25 fps)" in capsys.readouterr().out

    assert main(["verify", str(saida), str(als), *opcoes]) == 0
    capsys.readouterr()

    outro_tc = ["--start-tc", "01:00:00:00", "--frame-rate", "25"]
    assert main(["verify", str(saida), str(als), *outro_tc]) == 1
    tabela = capsys.readouterr().out
    assert "01:00:00:00 at 25 fps" in tabela
    assert "10:00:00:00 at 25 fps" in tabela


def test_cli_verify_leave_out_render_verifica_e_escreve_markdown(tmp_path, capsys):
    wav = escrever_wav(tmp_path, "stem.wav")
    video = tmp_path / "1015.mp4"
    tracks = [
        (
            "3-Audio",
            [
                clip(wav, 0.0, 8.0, nome="esticado", warp=ESTICADO_104),
                clip(wav, 8.0, 12.0, nome="exato", crossfade=True, loop_start=4.0),
                clip(video, 12.0, 16.0),
            ],
        ),
        ("5-2", [clip(wav, 0.0, 8.0, warp=ESTICADO_104)]),
        ("DX", [clip(wav, 0.0, 4.0)]),
    ]
    als = montar_set(tmp_path, tracks, bpm=104.0, midi=("MIDI 1",), midi_clips=("MIDI 1",))
    saida = tmp_path / "saida.aaf"
    relatorio = tmp_path / "verify.md"
    assert main(["export", str(als), str(saida), "--leave-out-render"]) == 0
    capsys.readouterr()

    assert main(["verify", str(saida), str(als)]) == 1
    capsys.readouterr()

    args = ["verify", str(saida), str(als), "--leave-out-render", "--report", str(relatorio)]
    assert main(args) == 0
    tabela = capsys.readouterr().out
    markdown = relatorio.read_text(encoding="utf-8")
    for texto in (tabela, markdown):
        assert "start taken from the AAF (01:00:00:00 at 24 fps)" in texto
        assert "left out: 2 clips (render in Live), 1 MIDI tracks, 1 video" in texto


def test_leave_out_render_misto_maioria_44k1_so_48k_exportado_le_e_verifica(tmp_path):
    w44a = escrever_wav(tmp_path, "w44a.wav", taxa=44100)
    w44b = escrever_wav(tmp_path, "w44b.wav", taxa=44100)
    w48 = escrever_wav(tmp_path, "exata48.wav", taxa=48000)
    tracks = [
        ("W1", [clip(w44a, 0.0, 8.0, warp=ESTICADO_104)]),
        ("W2", [clip(w44b, 0.0, 8.0, warp=ESTICADO_104)]),
        ("EXATA", [clip(w48, 0.0, 4.0, loop_start=0.5)]),
    ]
    als = montar_set(tmp_path, tracks, bpm=104.0)
    saida = tmp_path / "leave_out_misto.aaf"

    assert main(["export", str(als), str(saida), "--leave-out-render"]) == 0
    assert _aaf(saida).sample_rate == 44100
    assert main(["verify", str(saida), str(als), "--leave-out-render"]) == 0
