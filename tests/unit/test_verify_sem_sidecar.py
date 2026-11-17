"""Verificacao de sets do Live sem sidecar contra o AAF exportado."""

from __future__ import annotations

import gzip
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

import aaf2

from aafton.als_ancora import StartSpec, start_padrao
from aafton.pipeline import export_session
from aafton.report import Report
from aafton.verify import VerifyResult, verify_conversion
from tests.unit.test_als_sem_sidecar import ESTICADO_104, clip, escrever_wav, montar_set


def _adicionar_grupo(als_path: Path, nome_grupo: str = "Grupo") -> Path:
    """Envolve todas as AudioTracks do set num GroupTrack para testar sem sidecar."""
    root = ET.parse(gzip.open(als_path, "rb")).getroot()
    tracks_node = root.find("./LiveSet/Tracks")
    assert tracks_node is not None
    grupo = ET.Element("GroupTrack", Id="5")
    nome = ET.SubElement(grupo, "Name")
    ET.SubElement(nome, "EffectiveName", Value=nome_grupo)
    for track_node in tracks_node.findall("AudioTrack"):
        ET.SubElement(track_node, "TrackGroupId", Value="5")
    tracks_node.insert(0, grupo)
    with gzip.open(als_path, "wb") as handle:
        handle.write(ET.tostring(root, encoding="utf-8"))
    return als_path


def _renomear_slot_aaf(aaf_path: Path, indice_sound: int, novo_nome: str) -> None:
    """Renomeia o i-esimo slot Sound da CompositionMob no AAF."""
    with aaf2.open(str(aaf_path), "rw") as f:
        comp = next(m for m in f.content.mobs if type(m).__name__ == "CompositionMob")
        sound_slots = []
        for slot in comp.slots:
            if getattr(getattr(slot, "segment", None), "media_kind", "") == "Sound":
                sound_slots.append(slot)
        sound_slots[indice_sound].name = novo_nome


def _deslocar_starttime_aaf(aaf_path: Path, nome_slot: str, delta: int) -> None:
    """Soma ``delta`` amostras do arquivo no StartTime do primeiro SourceClip do slot."""
    with aaf2.open(str(aaf_path), "rw") as f:
        comp = next(m for m in f.content.mobs if type(m).__name__ == "CompositionMob")
        slot = next(s for s in comp.slots if s.name == nome_slot)
        seg = slot.segment
        while type(seg).__name__ != "SourceClip":
            filhos = getattr(seg, "components", None) or getattr(seg, "segments", None)
            seg = next(c for c in filhos if type(c).__name__ != "Filler")
        seg.start = int(seg.start or 0) + delta


def test_sem_sidecar_exportado_verifica_e_reporta_inicio_do_aaf(tmp_path):
    wav = escrever_wav(tmp_path, "dx.wav")
    als = montar_set(tmp_path, [("DX", [clip(wav, 0.0, 4.0)])])
    aaf = tmp_path / "saida.aaf"
    export_session(als, aaf, Report(strict=False), start=start_padrao())

    resultado = verify_conversion(aaf, als)

    assert resultado.ok is True
    assert resultado.audio_expected == 1
    assert resultado.audio_compared == 1
    assert resultado.rows[0].delta_samples == 0
    esperado = "start taken from the AAF (01:00:00:00 at 24 fps)"
    assert esperado in resultado.coverage_msg
    assert esperado in resultado.to_table()
    assert esperado in resultado.to_markdown()


def test_start_igual_passa_e_diferente_falha_citando_os_dois(tmp_path):
    wav = escrever_wav(tmp_path, "dx.wav")
    als = montar_set(tmp_path, [("DX", [clip(wav, 0.0, 4.0)])])
    aaf = tmp_path / "saida.aaf"
    inicio_export = StartSpec("10:00:00:00", Fraction(25), False)
    export_session(als, aaf, Report(strict=False), start=inicio_export)

    ok_res = verify_conversion(aaf, als, start=inicio_export)
    assert ok_res.ok is True
    assert "start taken from the AAF" not in ok_res.coverage_msg

    tc_errado = verify_conversion(aaf, als, start=StartSpec("01:00:00:00", Fraction(25), False))
    assert tc_errado.ok is False
    texto_tc = tc_errado.to_table()
    assert "01:00:00:00 at 25 fps" in texto_tc
    assert "10:00:00:00 at 25 fps" in texto_tc

    outro_fps = StartSpec("10:00:00:00", Fraction(24), False)
    fps_errado = verify_conversion(aaf, als, start=outro_fps)
    assert fps_errado.ok is False
    texto_fps = fps_errado.to_table()
    assert "10:00:00:00 at 24 fps" in texto_fps
    assert "10:00:00:00 at 25 fps" in texto_fps


def test_leave_out_render_verifica_com_contagens_de_excluidos(tmp_path):
    wav = escrever_wav(tmp_path, "stem.wav")
    video = tmp_path / "cena.mp4"
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
        ("VID", [clip(video, 0.0, 4.0)]),
        ("DX", [clip(wav, 0.0, 4.0)]),
    ]
    midis = ("MIDI 1", "MIDI 2")
    als = montar_set(tmp_path, tracks, bpm=104.0, midi=midis, midi_clips=midis)
    aaf = tmp_path / "saida.aaf"
    export_session(als, aaf, Report(strict=False), start=start_padrao(), leave_out_render=True)

    sem_flag = verify_conversion(aaf, als)
    assert sem_flag.ok is False

    resultado = verify_conversion(aaf, als, leave_out_render=True)
    assert resultado.ok is True
    assert resultado.audio_expected == 2
    assert resultado.audio_compared == 2
    esperado = "left out: 2 clips (render in Live), 2 MIDI tracks, 2 video"
    assert esperado in resultado.coverage_msg
    assert esperado in resultado.to_table()
    assert esperado in resultado.to_markdown()


def test_pareamento_4_canais_estereo_nomes_iguais_e_grupo(tmp_path):
    wav_mono = escrever_wav(tmp_path, "mono.wav", canais=1)
    wav_st = escrever_wav(tmp_path, "st.wav", canais=2)
    wav_4ch = escrever_wav(tmp_path, "amb.wav", canais=4)
    tracks = [
        ("AMB", [clip(wav_4ch, 0.0, 2.0, loop_start=0.5)]),
        ("X", [clip(wav_mono, 0.0, 2.0)]),
        ("X ch1", [clip(wav_mono, 2.0, 4.0)]),
        ("VOZ", [clip(wav_mono, 0.0, 2.0)]),
        ("VOZ", [clip(wav_mono, 4.0, 6.0)]),
        ("ST", [clip(wav_st, 0.0, 2.0, loop_start=0.5)]),
    ]
    als = _adicionar_grupo(montar_set(tmp_path, tracks))
    aaf = tmp_path / "saida.aaf"
    export_session(als, aaf, Report(strict=False), start=start_padrao())

    resultado = verify_conversion(aaf, als)

    assert resultado.ok is True
    assert resultado.audio_expected == 9
    assert resultado.audio_compared == 9


def test_slot_renomeado_no_aaf_falha_citando_slot_e_nomes(tmp_path):
    wav = escrever_wav(tmp_path, "dx.wav")
    als = montar_set(tmp_path, [("X", [clip(wav, 0.0, 2.0)]), ("X ch1", [clip(wav, 2.0, 4.0)])])
    aaf = tmp_path / "saida.aaf"
    export_session(als, aaf, Report(strict=False), start=start_padrao())
    _renomear_slot_aaf(aaf, 1, "OUTRA")

    resultado = verify_conversion(aaf, als)

    assert resultado.ok is False
    assert "slot 1: expected X ch1, found OUTRA" in resultado.errors
    assert "slot 1: expected X ch1, found OUTRA" in resultado.to_table()


def test_offset_48k_em_sessao_44k1_e_desvio_de_1_sample(tmp_path):
    w44a = escrever_wav(tmp_path, "a44.wav", taxa=44100)
    w44b = escrever_wav(tmp_path, "b44.wav", taxa=44100)
    w48 = escrever_wav(tmp_path, "stick48.wav", taxa=48000)
    tracks = [
        ("A", [clip(w44a, 0.0, 2.0)]),
        ("B", [clip(w44b, 0.0, 2.0)]),
        ("STICK", [clip(w48, 0.0, 2.0, nome="stick48", loop_start=17 / 44100)]),
    ]
    als = montar_set(tmp_path, tracks)
    aaf = tmp_path / "saida.aaf"
    export_session(als, aaf, Report(strict=False), start=start_padrao())

    ok_res = verify_conversion(aaf, als)
    assert ok_res.ok is True
    assert ok_res.audio_compared == 3

    _deslocar_starttime_aaf(aaf, "STICK", -1)
    falha = verify_conversion(aaf, als)
    assert falha.ok is False
    assert any("stick48" in erro for erro in falha.errors)


def test_relatorio_sem_sidecar_tira_offset_de_fora_de_escopo_e_mantem_com_sidecar(tmp_path):
    wav = escrever_wav(tmp_path, "som.wav")
    als = montar_set(tmp_path, [("DX", [clip(wav, 0.0, 2.0)])])
    aaf = tmp_path / "saida.aaf"
    export_session(als, aaf, Report(strict=False), start=start_padrao())

    res = verify_conversion(aaf, als)
    tabela, markdown = res.to_table(), res.to_markdown()
    escopo_sem = "audio content, clip gain/fader/pan and envelopes were not verified"
    assert "media offset verified" in res.coverage_msg
    assert "media offset verified" in tabela
    assert "media offset verified" in markdown
    assert f"Fields out of scope: {escopo_sem}." in tabela
    assert f"- **Out of scope**: {escopo_sem}" in markdown

    com_sidecar = VerifyResult()
    escopo_com = "media offset, " + escopo_sem
    assert f"Fields out of scope: {escopo_com}." in com_sidecar.to_table()
    assert f"- **Out of scope**: {escopo_com}" in com_sidecar.to_markdown()


def test_verify_sem_sidecar_com_selecao_de_tracks_e_track_vazia(tmp_path):
    wav_a = escrever_wav(tmp_path, "a.wav")
    wav_b = escrever_wav(tmp_path, "b.wav")
    tracks = [
        ("Vazia", []),
        ("A", [clip(wav_a, 0.0, 4.0)]),
        ("B", [clip(wav_b, 4.0, 8.0)]),
    ]
    als = montar_set(tmp_path, tracks)
    aaf = tmp_path / "saida.aaf"
    export_session(als, aaf, Report(strict=False), tracks=[2], start=start_padrao())

    sem_selecao = verify_conversion(aaf, als)
    assert sem_selecao.ok is False

    com_selecao = verify_conversion(aaf, als, tracks=[2])
    assert com_selecao.ok is True
    assert com_selecao.audio_expected == 1
    assert com_selecao.audio_compared == 1
    assert [row.track for row in com_selecao.rows] == ["B"]


def test_verify_sem_sidecar_reprova_taxas_diferentes_citando_as_duas(tmp_path):
    w44 = escrever_wav(tmp_path, "a44.wav", taxa=44100)
    w48 = escrever_wav(tmp_path, "a48.wav", taxa=48000)
    als_44 = montar_set(tmp_path, [("DX", [clip(w44, 0.0, 4.0)])], nome="set44.als")
    als_48 = montar_set(tmp_path, [("DX", [clip(w48, 0.0, 4.0)])], nome="set48.als")
    aaf_44 = tmp_path / "saida44.aaf"
    export_session(als_44, aaf_44, Report(strict=False), start=start_padrao())

    resultado = verify_conversion(aaf_44, als_48)

    assert resultado.ok is False
    assert resultado.rows == []
    assert any("44100" in erro and "48000" in erro for erro in resultado.errors)


def test_verify_sem_sidecar_nao_diz_offset_verificado_quando_offset_difere(tmp_path):
    wav = escrever_wav(tmp_path, "som.wav")
    als = montar_set(tmp_path, [("DX", [clip(wav, 0.0, 2.0)])])
    aaf = tmp_path / "saida.aaf"
    export_session(als, aaf, Report(strict=False), start=start_padrao())
    _deslocar_starttime_aaf(aaf, "DX", 123)
    res = verify_conversion(aaf, als)
    assert res.ok is False and any("media offset differs" in e for e in res.errors)
    assert "media offset verified" not in res.coverage_msg + res.to_table() + res.to_markdown()


def test_verify_sem_sidecar_nao_diz_offset_verificado_sem_eventos_brutos(tmp_path, monkeypatch):
    from aafton import verify_sem_sidecar as vss

    wav = escrever_wav(tmp_path, "som.wav")
    als = montar_set(tmp_path, [("DX", [clip(wav, 0.0, 2.0)])])
    aaf = tmp_path / "saida.aaf"
    export_session(als, aaf, Report(strict=False), start=start_padrao())
    info = vss._ler_aaf_bruto(aaf)
    vazia = vss._AafInfo(info.frames_inicio, info.tc_rate, 48000, [])
    monkeypatch.setattr(vss, "_ler_aaf_bruto", lambda _: vazia)
    assert "media offset verified" not in verify_conversion(aaf, als).coverage_msg
    als_vazio = montar_set(tmp_path, [("DX", [])], midi=("MIDI 1",), nome="vazio.als")
    res_zero = verify_conversion(aaf, als_vazio)
    assert res_zero.audio_compared == 0 and "media offset verified" not in res_zero.coverage_msg


def test_verify_sem_sidecar_nao_aaf_nao_fabrica_inicio_e_diz_offset_nao_verificado(tmp_path):
    from aafton import verify_sem_sidecar as vss

    wav = escrever_wav(tmp_path, "som.wav")
    als = montar_set(tmp_path, [("A1", [clip(wav, 0.0, 2.0)])], bpm=60.0)
    fcpxml = tmp_path / "saida.fcpxml"
    export_session(als, fcpxml, Report(strict=False), start=start_padrao())
    assert vss._ler_aaf_bruto(fcpxml) is None
    res = verify_conversion(fcpxml, als)
    assert res.ok is True and res.audio_compared == 1 and res.rows[0].delta_samples == 0
    msg = res.coverage_msg
    assert "01:00:00:00 at 24 fps" in msg and "start taken from the AAF" not in msg
    assert "media offset not verified" in msg and "media offset verified" not in msg
