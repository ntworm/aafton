"""Testes do Studio (backend Live -> AAF) para sets sem o registro do aafton."""

from __future__ import annotations

import gzip
import xml.etree.ElementTree as ET
from pathlib import Path

from aafton.aaf_read import read_aaf
from aafton.als_render import MISTA
from aafton.report import Report
from aafton.sidecar import Anchor, write_sidecar
from aafton.studio import app_set, app_view
from aafton.timecode import TimecodeRate, samples_to_label
from tests.unit.test_als_sem_sidecar import ESTICADO_104, clip, escrever_wav, montar_set


def _adicionar_clip_midi(als_path: Path, track_nome: str) -> None:
    """Coloca um MidiClip no MidiTrack para acionar o estado MIDI na leitura."""
    with gzip.open(als_path, "rb") as handle:
        root = ET.parse(handle).getroot()
    for track in root.findall("./LiveSet/Tracks/MidiTrack"):
        nome = track.find("Name/EffectiveName")
        if nome is None or nome.get("Value") != track_nome:
            continue
        dc = ET.SubElement(track, "DeviceChain")
        seq = ET.SubElement(dc, "MainSequencer")
        ct = ET.SubElement(seq, "ClipTimeable")
        auto = ET.SubElement(ct, "ArrangerAutomation")
        ev = ET.SubElement(auto, "Events")
        mc = ET.SubElement(ev, "MidiClip")
        ET.SubElement(mc, "CurrentStart", Value="4")
        ET.SubElement(mc, "CurrentEnd", Value="8")
    with gzip.open(als_path, "wb") as handle:
        handle.write(ET.tostring(root, encoding="utf-8"))


def _pedido(als: Path, saida: Path, tracks: list[int], **opcoes) -> dict:
    """Monta um pedido convert Live -> AAF para os testes sem sidecar."""
    opcoes.setdefault("mode", "strict")
    return {
        "direction": "to_aaf",
        "input": str(als),
        "output": str(saida),
        "tracks": list(tracks),
        "overwrite": False,
        "options": opcoes,
    }


def test_set_sem_registro_retorna_made_in_live_e_campos_padrao(tmp_path):
    wav = escrever_wav(tmp_path, "dx.wav")
    als = montar_set(tmp_path, [("DX", [clip(wav, 0.0, 4.0)])])

    res = app_set.read_set({"direction": "to_aaf", "input": str(als)})

    assert res["ok"] is True
    assert res["made_in"] == "Live (no aafton record)"
    assert res["facts"][0] == {
        "k": "Made in",
        "v": "Live (no aafton record)",
        "sub": None,
        "st": None,
    }
    assert res["start"] == {
        "start_tc": "01:00:00:00",
        "frame_rate": "24",
        "from_locator": False,
    }
    assert res["can_leave_out"] is False
    assert res["guide"] == []
    assert res["timeline"]["start_tc"] == "01:00:00:00"
    assert res["tracks"][0]["state"] == {"st": "ok", "label": "ready"}
    assert res["tracks"][0]["pick"] is True


def test_set_sem_registro_com_locator_tc_inicia_no_valor_do_locator(tmp_path):
    wav = escrever_wav(tmp_path, "dx.wav")
    als = montar_set(tmp_path, [("DX", [clip(wav, 0.0, 4.0)])], locator="TC 02:30:00:00 @ 48000")

    res = app_set.read_set({"direction": "to_aaf", "input": str(als)})

    assert res["ok"] is True
    assert res["made_in"] == "Live (no aafton record)"
    assert res["start"] == {
        "start_tc": "02:30:00:00",
        "frame_rate": "24",
        "from_locator": True,
    }
    assert res["timeline"]["start_tc"] == "02:30:00:00"


def test_set_sem_registro_lista_render_por_track_mista_midi_e_video(tmp_path):
    mono = escrever_wav(tmp_path, "mono.wav", canais=1)
    estereo = escrever_wav(tmp_path, "estereo.wav", canais=2)
    video = tmp_path / "corte.mov"
    tracks = [
        (
            "3-Audio",
            [
                clip(mono, 0.0, 8.0, nome="w1", warp=ESTICADO_104),
                clip(mono, 8.0, 16.0, nome="w2", warp=ESTICADO_104),
                clip(mono, 16.0, 20.0, nome="ok"),
            ],
        ),
        ("4-Loop", [clip(mono, 0.0, 4.0, nome="l1", loop_on=True)]),
        ("MX", [clip(mono, 0.0, 4.0), clip(estereo, 4.0, 8.0)]),
        ("VIDEO", [clip(video, 0.0, 8.0)]),
    ]
    als = montar_set(tmp_path, tracks, bpm=104.0, midi=("MIDI 1",))
    _adicionar_clip_midi(als, "MIDI 1")

    res = app_set.read_set({"direction": "to_aaf", "input": str(als)})

    assert res["ok"] is True
    assert res["can_leave_out"] is True
    dot = app_view.MIDDLE_DOT
    rows = {row["name"]: row for row in res["tracks"]}
    assert rows["3-Audio"]["state"] == {"st": "err", "label": f"render first {dot} warp 2"}
    assert rows["3-Audio"]["pick"] is None
    assert rows["3-Audio"]["render_clips"] == 2
    assert rows["3-Audio"]["mixed_channels"] is False
    assert rows["4-Loop"]["state"] == {"st": "err", "label": f"render first {dot} loop 1"}
    assert rows["4-Loop"]["pick"] is None
    assert rows["4-Loop"]["render_clips"] == 1
    assert rows["MX"]["state"] == {"st": "err", "label": f"render first {dot} mono+stereo"}
    assert rows["MX"]["pick"] is None
    assert rows["MX"]["mixed_channels"] is True
    assert rows["VIDEO"]["kind"] == "video"
    assert rows["VIDEO"]["state"] == {"st": "off", "label": f"video {dot} not in the AAF"}
    assert rows["VIDEO"]["pick"] is None
    assert rows["MIDI 1"]["kind"] == "MIDI"
    assert rows["MIDI 1"]["state"] == {"st": "err", "label": "render first"}
    assert rows["MIDI 1"]["pick"] is None
    assert res["guide"] == [
        "3-Audio: 2 clips with warp",
        "4-Loop: 1 clip with loop",
        f"MX: {MISTA}",
        "MIDI 1",
    ]


def test_set_com_registro_retorna_made_in_aafton_e_sem_campos_de_inicio(tmp_path):
    wav = escrever_wav(tmp_path, "dx.wav")
    als = montar_set(tmp_path, [("DX", [clip(wav, 0.0, 4.0)])])
    write_sidecar(
        als,
        Anchor(
            live_origin_samples=3600 * 48000,
            session_start_samples=3600 * 48000,
            sample_rate=48000,
            timecode_rate=TimecodeRate(24, 1),
            bpm=120.0,
        ),
    )

    res = app_set.read_set({"direction": "to_aaf", "input": str(als)})

    assert res["ok"] is True
    assert res["made_in"] == "aafton"
    assert res["facts"][0] == {"k": "Made in", "v": "aafton", "sub": None, "st": None}
    assert res["start"] is None
    assert res["can_leave_out"] is False


def test_export_com_start_e_frame_rate_inicia_aaf_em_10h_a_25fps(tmp_path):
    wav = escrever_wav(tmp_path, "dx.wav")
    als = montar_set(tmp_path, [("DX", [clip(wav, 0.0, 4.0)])])
    saida = tmp_path / "saida.aaf"
    pedido = _pedido(als, saida, [0], start_tc="10:00:00:00", frame_rate="25")

    res = app_set.convert_to_aaf(pedido)

    assert res["ok"] is True
    assert res["written"] is True
    assert res["verdict"]["word"] == "Positions verified"
    aaf = read_aaf(saida, Report(strict=False))
    assert aaf.timecode_rate == TimecodeRate(25, 1)
    inicio_aaf = samples_to_label(aaf.start_timecode_samples, aaf.timecode_rate, aaf.sample_rate)
    clip_aaf = samples_to_label(
        aaf.tracks[0].clips[0].timeline_start_samples, aaf.timecode_rate, aaf.sample_rate
    )
    assert inicio_aaf == "10:00:00:00"
    assert clip_aaf == "10:00:00:00"


def test_export_sem_e_com_leave_out_render_em_set_com_warp(tmp_path):
    wav = escrever_wav(tmp_path, "stem.wav")
    tracks = [
        (
            "3-Audio",
            [
                clip(wav, 0.0, 8.0, nome="esticado", warp=ESTICADO_104),
                clip(wav, 8.0, 12.0, nome="exato"),
            ],
        )
    ]
    als = montar_set(tmp_path, tracks, bpm=104.0)
    recusado_path = tmp_path / "recusado.aaf"
    pedido_recusado = _pedido(
        als,
        recusado_path,
        [0],
        start_tc="10:00:00:00",
        frame_rate="25",
        leave_out_render=False,
    )

    recusado = app_set.convert_to_aaf(pedido_recusado)

    assert recusado["ok"] is True
    assert recusado["written"] is False
    assert recusado["verdict"]["word"] == "Stopped"
    assert not recusado_path.exists()
    assert "ALS_RENDER_IN_LIVE" in [n["code"] for n in recusado["notes"]["main"]]
    assert any("3-Audio: 1 clip with warp" in linha for linha in recusado["verdict"]["detail"])

    aceito_path = tmp_path / "aceito.aaf"
    pedido_aceito = _pedido(
        als,
        aceito_path,
        [0],
        start_tc="10:00:00:00",
        frame_rate="25",
        leave_out_render=True,
    )

    aceito = app_set.convert_to_aaf(pedido_aceito)

    assert aceito["ok"] is True
    assert aceito["written"] is True
    assert aceito["verdict"]["word"] == "Positions verified"
    aaf = read_aaf(aceito_path, Report(strict=False))
    assert [c.name for c in aaf.tracks[0].clips] == ["exato"]


def test_studio_exporta_e_verifica_subconjunto_e_track_vazia(tmp_path):
    w1 = escrever_wav(tmp_path, "um.wav")
    w2 = escrever_wav(tmp_path, "dois.wav")
    als_dois = montar_set(tmp_path, [("A", [clip(w1, 0.0, 4.0)]), ("B", [clip(w2, 4.0, 8.0)])])
    saida_sub = tmp_path / "subconjunto.aaf"

    res_sub = app_set.convert_to_aaf(
        _pedido(als_dois, saida_sub, [1], start_tc="01:00:00:00", frame_rate="24")
    )

    assert res_sub["ok"] is True
    assert res_sub["written"] is True
    assert res_sub["verdict"]["word"] == "Positions verified"

    als_vazia = montar_set(
        tmp_path, [("Vazia", []), ("DX", [clip(w1, 0.0, 4.0)])], nome="com_vazia.als"
    )
    lido = app_set.read_set({"direction": "to_aaf", "input": str(als_vazia)})
    selecao = [t["audio_index"] for t in lido["tracks"] if t["pick"]]
    assert selecao == [1]

    saida_vazia = tmp_path / "sem_vazia.aaf"
    res_vazia = app_set.convert_to_aaf(
        _pedido(als_vazia, saida_vazia, selecao, start_tc="01:00:00:00", frame_rate="24")
    )

    assert res_vazia["ok"] is True
    assert res_vazia["written"] is True
    assert res_vazia["verdict"]["word"] == "Positions verified"


def test_studio_exporta_track_pronta_com_warp_e_mista_nao_selecionadas(tmp_path):
    mono = escrever_wav(tmp_path, "mono.wav", canais=1)
    estereo = escrever_wav(tmp_path, "estereo.wav", canais=2)
    tracks = [
        ("DX", [clip(mono, 0.0, 4.0)]),
        ("DX", [clip(mono, 0.0, 8.0, warp=ESTICADO_104)]),
        ("MX", [clip(mono, 0.0, 4.0), clip(estereo, 4.0, 8.0)]),
    ]
    als = montar_set(tmp_path, tracks, bpm=104.0)

    lido = app_set.read_set({"direction": "to_aaf", "input": str(als)})
    assert [t["pick"] for t in lido["tracks"]] == [True, None, None]
    selecao = [t["audio_index"] for t in lido["tracks"] if t["pick"]]
    assert selecao == [0]

    saida = tmp_path / "pronta.aaf"
    res = app_set.convert_to_aaf(
        _pedido(als, saida, selecao, start_tc="01:00:00:00", frame_rate="24")
    )

    assert res["ok"] is True
    assert res["written"] is True
    assert res["verdict"]["word"] == "Positions verified"
    aaf = read_aaf(saida, Report(strict=False))
    assert [t.name for t in aaf.tracks] == ["DX"]
    assert [c.name for c in aaf.tracks[0].clips] == ["mono"]


def test_studio_subconjunto_misto_maioria_44k1_so_48k_le_e_verifica(tmp_path):
    w44a = escrever_wav(tmp_path, "w44a.wav", taxa=44100)
    w44b = escrever_wav(tmp_path, "w44b.wav", taxa=44100)
    w48 = escrever_wav(tmp_path, "exata48.wav", taxa=48000)
    tracks = [
        ("W1", [clip(w44a, 0.0, 8.0, warp=ESTICADO_104)]),
        ("W2", [clip(w44b, 0.0, 8.0, warp=ESTICADO_104)]),
        ("EXATA", [clip(w48, 0.0, 4.0, loop_start=0.5)]),
    ]
    als = montar_set(tmp_path, tracks, bpm=104.0)

    lido = app_set.read_set({"direction": "to_aaf", "input": str(als)})
    assert lido["sample_rate"] == 44100
    assert [t["pick"] for t in lido["tracks"]] == [None, None, True]

    saida = tmp_path / "sub_48_em_44k1.aaf"
    res = app_set.convert_to_aaf(_pedido(als, saida, [2], start_tc="01:00:00:00", frame_rate="24"))

    assert res["ok"] is True
    assert res["written"] is True
    assert res["verdict"]["word"] == "Positions verified"
    assert read_aaf(saida, Report(strict=False)).sample_rate == 44100


def test_studio_subconjunto_ignora_crossfade_nao_provado_e_isola_mesmo_nome(tmp_path):
    wav = escrever_wav(tmp_path, "dx.wav")
    a = clip(wav, 0.0, 4.0, nome="a", warp=[(0.0, 0.0), (4.0, 4.0)], fade_out=1.0)
    b = clip(wav, 4.0, 8.0, nome="b", crossfade=True, loop_start=2.0)
    als = montar_set(tmp_path, [("DX", [clip(wav, 0.0, 4.0)]), ("DX", [a, b])], bpm=60.0)

    lido = app_set.read_set({"direction": "to_aaf", "input": str(als)})
    t0_row, t1_row = lido["tracks"]
    assert t0_row["state"] == {"st": "ok", "label": "ready"}
    assert t0_row["pick"] is True
    assert t1_row["state"] == {"st": "err", "label": "1 error"}
    assert t1_row["pick"] is False
    assert any("crossfade" in r.lower() for r in t1_row["reasons"])

    saida_ok = tmp_path / "limpa.aaf"
    res_ok = app_set.convert_to_aaf(
        _pedido(als, saida_ok, [0], start_tc="01:00:00:00", frame_rate="24")
    )
    assert res_ok["ok"] is True
    assert res_ok["written"] is True
    assert res_ok["verdict"]["word"] == "Positions verified"

    saida_err = tmp_path / "nao_provado.aaf"
    res_err = app_set.convert_to_aaf(
        _pedido(als, saida_err, [1], start_tc="01:00:00:00", frame_rate="24")
    )
    assert res_err["ok"] is True
    assert res_err["written"] is False
    assert res_err["verdict"]["word"] == "Stopped"
    assert "ALS_CROSSFADE_NOT_PROVEN" in [n["code"] for n in res_err["notes"]["main"]]


def test_studio_linha_mista_com_video_conta_apenas_clips_de_audio(tmp_path):
    wav, vid = escrever_wav(tmp_path, "dx.wav"), tmp_path / "corte.mp4"
    w_clip = clip(wav, 0.0, 8.0, nome="esticado", warp=ESTICADO_104)
    exact = clip(wav, 8.0, 12.0, nome="exato", loop_start=4.0)
    tracks = [
        ("3-Audio", [w_clip, exact, clip(vid, 12.0, 16.0, nome="c1")]),
        ("4-SoWarp", [w_clip, clip(vid, 8.0, 12.0, nome="c2")]),
        ("VIDEO", [clip(vid, 0.0, 8.0, nome="unico")]),
    ]
    als = montar_set(tmp_path, tracks, bpm=104.0)
    res = app_set.read_set({"direction": "to_aaf", "input": str(als)})
    assert res["ok"] is True
    rows = {r["name"]: r for r in res["tracks"]}
    assert (rows["3-Audio"]["clips"], rows["3-Audio"]["render_clips"]) == (2, 1)
    assert (rows["4-SoWarp"]["clips"], rows["4-SoWarp"]["render_clips"]) == (1, 1)
    assert (rows["VIDEO"]["kind"], rows["VIDEO"]["clips"]) == ("video", 1)
