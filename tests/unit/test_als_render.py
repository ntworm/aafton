"""Regra de render de um clip do Live (``als_render.precisa_render``).

Warp e exato so a 1:1 nos modos medidos (3, 4 e 6), com o desvio acumulado de
ate meia amostra da sessao no clip; transposicao pede render em qualquer clip,
e loop e StartRelative saem das causas quando o clip e expansivel.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from fractions import Fraction

import pytest

from aafton.als_leitura import offset_do_warp
from aafton.als_render import (
    RenderLog,
    e_video,
    fechar_leitura,
    precisa_render,
    segundos_no_beat,
    warp_um_para_um,
)
from aafton.als_xml import set_value
from aafton.ir import ClipIR, SessionIR, TrackIR
from aafton.report import Report
from aafton.timecode import TimecodeRate
from tests.unit.test_als_sem_sidecar import clip, no_do_clip

TAXA = 48000


def _no(inicio=0.0, fim=4.0, **campos):
    return no_do_clip(clip("stem.wav", inicio, fim, **campos))


def test_razao_0_866667_e_recusada():
    node = _no(0.0, 8.0, warp=[(0.0, 0.0), (8.0, 4.0)])

    assert precisa_render(node, Fraction(104), TAXA) == ["warp"]


@pytest.mark.parametrize(
    "marcadores",
    [
        [(0.0, 0.0), (8.0, 4.0)],
        [(0.0, 0.0), (4.0, 2.0), (8.0, 4.0)],
    ],
)
def test_clip_1_1_de_2_e_3_marcadores_e_aceito(marcadores):
    node = _no(warp=marcadores)

    assert warp_um_para_um(node, Fraction(120), TAXA)
    assert precisa_render(node, Fraction(120), TAXA) == []


def test_clip_longo_um_milionesimo_mais_rapido_passa_de_meia_amostra():
    node = _no(0.0, 1000.0, warp=[(0.0, 0.0), (1000.0, 500.0005)])

    assert precisa_render(node, Fraction(120), TAXA) == ["warp"]


def test_clip_curto_um_milionesimo_mais_rapido_fica_dentro_de_meia_amostra():
    node = _no(0.0, 4.0, warp=[(0.0, 0.0), (4.0, 2.000002)])

    assert precisa_render(node, Fraction(120), TAXA) == []


def test_warp_sem_dois_marcadores_nao_prova_1_1():
    node = _no(warp=[(0.0, 0.0)])

    assert precisa_render(node, Fraction(120), TAXA) == ["warp"]


def test_offset_e_o_sectime_interpolado_no_loop_start():
    node = _no(warp=[(0.0, 0.0), (4.0, 2.0), (8.0, 4.0)], loop_start=5.0)

    assert segundos_no_beat(node, Fraction(5)) == Fraction(5, 2)
    # Modo padrao do helper e Complex (4): uma amostra depois.
    assert offset_do_warp(node, TAXA) == 120001


# Kit 3 (Live 12.4.25b1, 48 kHz, 96 BPM): a amostra que o Live leu em cada caso.
@pytest.mark.parametrize(
    "modo,loop_start,lida",
    [
        ("3", "0", 0),
        ("4", "0", 1),
        ("6", "0", 1),
        ("3", str(5 / 7), 21428),
        ("4", str(5 / 7), 21429),
        ("6", str(5 / 7), 21429),
        ("3", str(1 / 3), 10000),
        ("4", str(1 / 3), 10001),
        ("4", "1", 30001),
    ],
)
def test_offset_e_a_amostra_que_o_live_le(modo, loop_start, lida):
    node = _no(0.0, 6.0, warp=[(0.0, 0.0), (16.0, 10.0)], modo=modo)
    set_value(node, "Loop/LoopStart", loop_start)

    assert offset_do_warp(node, 48000) == lida


def test_offset_antes_e_depois_dos_marcadores_e_extrapolado():
    node = _no(warp=[(2.0, 1.0), (10.0, 5.0)])

    assert segundos_no_beat(node, Fraction(0)) == 0
    assert segundos_no_beat(node, Fraction(12)) == 6


def test_clip_sem_warp_e_reto_nao_pede_render():
    assert precisa_render(_no(), Fraction(120), TAXA) == []


def test_varias_causas_saem_juntas():
    node = _no(warp=[(0.0, 0.0), (8.0, 4.0)], modo="0", loop_on=True, pitch_coarse=-1)

    assert precisa_render(node, Fraction(120), TAXA) == ["beats mode", "loop", "transpose"]


def test_video_pela_extensao():
    assert e_video("C:/filme/roughcut 2.MOV")
    assert e_video("1015.mp4")
    assert not e_video("dialogo.wav")


def test_fechar_leitura_mantem_automacao_de_track_mantida_com_mesmo_nome():
    log = RenderLog(leave_out=True)
    t0 = TrackIR(id="t0", source_index=0, name="DX", kind="audio", channel_count=1)
    t1_clip = ClipIR(
        id="c1",
        track_id="t1",
        channel_index=0,
        event_index=0,
        name="ok",
        media_id=None,
        timeline_start_samples=0,
        timeline_end_samples=TAXA,
    )
    t1 = TrackIR(
        id="t1",
        source_index=1,
        name="DX",
        kind="audio",
        channel_count=1,
        clips=[t1_clip],
    )
    t2 = TrackIR(id="t2", source_index=2, name="AMB", kind="audio", channel_count=1)
    log.clip(t0, "esticado", ["warp"], ET.Element("AudioClip"))
    log.clip(t2, "esticado2", ["warp"], ET.Element("AudioClip"))
    msg_dx = "automation of 'DX' is not yet reconciled for export"
    msg_amb = "automation of 'AMB' is not yet reconciled for export"
    log.features = {"t0": msg_dx, "t1": msg_dx, "t2": msg_amb}
    session = SessionIR(
        name="s",
        sample_rate=TAXA,
        timecode_rate=TimecodeRate(24, 1),
        start_timecode_samples=0,
        tracks=[t0, t1, t2],
        unsupported_export_features=[msg_dx, msg_dx, msg_amb],
    )
    report = Report(strict=False)
    report.warning("ALS_AUTOMATION_UNSUPPORTED", msg_dx, {"track": "DX", "envelopes": ["1"]})
    report.warning("ALS_AUTOMATION_UNSUPPORTED", msg_dx, {"track": "DX", "envelopes": ["2"]})
    report.warning("ALS_AUTOMATION_UNSUPPORTED", msg_amb, {"track": "AMB", "envelopes": ["3"]})

    fechar_leitura(log, report, session, [])

    assert [t.id for t in session.tracks] == ["t1"]
    assert session.unsupported_export_features == [msg_dx]
    automacoes = [i for i in report.issues if i.code == "ALS_AUTOMATION_UNSUPPORTED"]
    assert [i.context["track"] for i in automacoes] == ["DX", "DX"]


def test_warp_tempo_zero_ou_negativo_nao_divide_por_zero():
    node = _no(0.0, 4.0, warp=[(0.0, 0.0), (4.0, 2.0)])

    assert warp_um_para_um(node, Fraction(0), TAXA) is False
    assert warp_um_para_um(node, Fraction(-120), TAXA) is False
    assert precisa_render(node, Fraction(0), TAXA) == ["warp"]


def test_warp_um_para_um_com_inicio_antes_do_arquivo_pede_render(tmp_path):
    """O SecTime extrapolado no LoopStart cai antes do segundo 0: o AAF nao leva."""
    from tests.unit.test_als_sem_sidecar import clip, no_do_clip

    antes = no_do_clip(clip("a.wav", 0.0, 4.0, warp=[(4.0, 0.0), (20.0, 8.0)], loop_start=0.0))
    dentro = no_do_clip(clip("a.wav", 0.0, 4.0, warp=[(0.0, 0.0), (16.0, 8.0)], loop_start=2.0))

    assert precisa_render(antes, Fraction(120), TAXA) == ["warp"]
    assert precisa_render(dentro, Fraction(120), TAXA) == []
