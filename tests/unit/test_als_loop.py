"""Expansao de clips em loop de um set do Live sem o registro do aafton."""

from __future__ import annotations

import gzip
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

import aaf2
import pytest

from aafton.als_ancora import start_padrao
from aafton.als_loop import (
    CAUDA_S,
    DESLOCAMENTO_DO_MODO,
    XFADE_VOLTA_S,
    Passada,
    loop_expansivel,
    passadas,
)
from aafton.als_read import read_session
from aafton.als_read_clips import ReadContext, _beats_to_samples
from aafton.als_render import precisa_render
from aafton.als_xml import set_value
from aafton.ir import FadeIR, SessionIR
from aafton.pipeline import export_session
from aafton.report import Report
from aafton.sidecar import Anchor
from aafton.timecode import TimecodeRate
from aafton.verify import verify_conversion
from tests.unit.test_als_sem_sidecar import (
    UMA_HORA_24,
    UM_PARA_UM_120,
    clip,
    escrever_wav,
    montar_set,
    no_do_clip,
)

TAXA = 48000
L_48 = round(XFADE_VOLTA_S * TAXA)
T_48 = round(CAUDA_S * TAXA)


def _no_loop(
    inicio: float = 0.0,
    fim: float = 8.0,
    *,
    loop_start: float = 2.0,
    loop_end: float | str = 4.0,
    start_relative: float | str | None = 0.0,
    warp: list[tuple[float, float]] | None = None,
    modo: str = "3",
    loop_on: bool = True,
    **extras,
) -> ET.Element:
    """Constroi um AudioClip em loop com LoopEnd e StartRelative customizados."""
    marcadores = UM_PARA_UM_120 if warp is None else warp
    dados = clip(
        "stem.wav",
        inicio,
        fim,
        loop_start=loop_start,
        warp=marcadores,
        modo=modo,
        loop_on=loop_on,
        **extras,
    )
    node = no_do_clip(dados)
    set_value(node, "Loop/LoopEnd", loop_end)
    if start_relative is None:
        loop_elem = node.find("Loop")
        sr_elem = node.find("Loop/StartRelative")
        if loop_elem is not None and sr_elem is not None:
            loop_elem.remove(sr_elem)
    else:
        set_value(node, "Loop/StartRelative", start_relative)
    return node


def _amostras(beats: float, taxa: int = TAXA, bpm: float = 120.0) -> int:
    return _beats_to_samples(beats, taxa, bpm)


def test_constantes_do_modelo_medido():
    assert XFADE_VOLTA_S == 0.004
    assert CAUDA_S == 47 / 48000
    assert DESLOCAMENTO_DO_MODO == {"3": 0, "4": 1, "6": 1}
    assert L_48 == 192
    assert T_48 == 47


def test_loop_sem_start_relative_fim_numa_volta_com_cauda():
    # P = 2 beats (48000 samples a 120 BPM / 48 kHz), duracao 6 beats -> 3 passadas.
    # Voltas em beat 2 e beat 4; fim em beat 6 (exatamente numa volta -> cauda T=47).
    node = _no_loop(0.0, 6.0, loop_start=2.0, loop_end=4.0, modo="3")
    ini = UMA_HORA_24
    fim = UMA_HORA_24 + _amostras(6.0)

    assert loop_expansivel(node, Fraction(120), TAXA, UMA_HORA_24, ini, fim)
    assert precisa_render(node, Fraction(120), TAXA, UMA_HORA_24, ini, fim) == []

    lista = passadas(node, Fraction(120), TAXA, UMA_HORA_24, ini, fim)
    v1 = UMA_HORA_24 + 48000
    v2 = UMA_HORA_24 + 96000
    assert lista == [
        Passada(ini, v1, 48000, 0, L_48),
        Passada(v1 - L_48, v2, 48000 - L_48, L_48, L_48),
        Passada(v2 - L_48, fim + T_48, 48000 - L_48, L_48, T_48),
    ]


def test_loop_com_start_relative_e_ultima_passada_parcial():
    # LoopStart=2, LoopEnd=6 (P=4 beats = 96000 samples), StartRelative=1.
    # Clip de 0 a 8 beats:
    # b_1 = 0 + 4 - 1 = 3 beats (72000 samples) -> entra
    # b_2 = 0 + 8 - 1 = 7 beats (168000 samples) -> entra
    # b_3 = 0 + 12 - 1 = 11 beats > 8 -> fim no meio da passada (sem cauda).
    node = _no_loop(0.0, 8.0, loop_start=2.0, loop_end=6.0, start_relative=1.0, modo="3")
    ini = UMA_HORA_24
    fim = UMA_HORA_24 + _amostras(8.0)

    assert loop_expansivel(node, Fraction(120), TAXA, UMA_HORA_24, ini, fim)
    assert precisa_render(node, Fraction(120), TAXA, UMA_HORA_24, ini, fim) == []

    lista = passadas(node, Fraction(120), TAXA, UMA_HORA_24, ini, fim)
    v1 = UMA_HORA_24 + 72000
    v2 = UMA_HORA_24 + 168000
    # Passada 0 comeca em LoopStart + StartRelative = 3 beats -> 72000 samples.
    assert lista == [
        Passada(ini, v1, 72000, 0, L_48),
        Passada(v1 - L_48, v2, 48000 - L_48, L_48, L_48),
        Passada(v2 - L_48, fim, 48000 - L_48, L_48, 0),
    ]


@pytest.mark.parametrize(
    "modo,deslocamento",
    [("3", 0), ("4", 1), ("6", 1)],
)
def test_leitura_com_floor_e_deslocamento_de_complex_e_complex_pro(modo, deslocamento):
    # k06: LoopStart = 1/7 beat a 80 BPM (60/80 = 0.75 s/beat; 16 beats = 12 s).
    # 1/7 beat = 3/28 s = 5142.857... amostras a 48 kHz; ou a 96 BPM (0.625 s/beat):
    # a 96 BPM, 1 beat = 30000 amostras, 1/7 beat = 4285.714... amostras -> floor = 4285.
    marcadores = [(0.0, 0.0), (16.0, 10.0)]  # 96 BPM -> 0.625 s/beat
    loop_start_str = str(1.0 / 7.0)
    node = _no_loop(
        0.0,
        4.0,
        loop_start=float(loop_start_str),
        loop_end=2.0,
        warp=marcadores,
        modo=modo,
    )
    set_value(node, "Loop/LoopStart", loop_start_str)
    ini = 0
    fim = _amostras(4.0, TAXA, 96.0)

    lista = passadas(node, Fraction(96), TAXA, 0, ini, fim)

    esperado_ini = 4285 + deslocamento
    assert lista[0].source_offset == esperado_ini
    assert lista[1].source_offset == esperado_ini - L_48


@pytest.mark.parametrize("modo,deslocamento", [("3", 0), ("4", 1)])
def test_floor_tolera_decimal_do_live_logo_abaixo_da_amostra(modo, deslocamento):
    # k07: StartRelative 1/3 beat escrito como float; 1 + 1/3 beat a 96 BPM da
    # 39999.99999... amostras e o Live leu a amostra 40000 (+1 em Complex).
    node = _no_loop(
        0.0, 4.0, loop_start=1.0, loop_end=3.0, warp=[(0.0, 0.0), (16.0, 10.0)], modo=modo
    )
    set_value(node, "Loop/StartRelative", str(1.0 / 3.0))
    fim = _amostras(4.0, TAXA, 96.0)

    lista = passadas(node, Fraction(96), TAXA, 0, 0, fim)

    assert lista[0].source_offset == 40000 + deslocamento


@pytest.mark.parametrize("current_start,periodo", [(4.0, 4.0), (8.0, 4.0), (4.0, 8.0), (8.0, 8.0)])
def test_fronteira_110_bpm_48_khz_fim_numa_volta_da_cauda_sem_passada_fantasma(
    current_start, periodo
):
    bpm = 110.0
    marcadores = [(0.0, 0.0), (11.0, 6.0)]  # 110 BPM: 11 beats = 6 s
    current_end = current_start + 2 * periodo
    node = _no_loop(
        current_start,
        current_end,
        loop_start=2.0,
        loop_end=2.0 + periodo,
        warp=marcadores,
        modo="4",
    )
    ini = UMA_HORA_24 + _amostras(current_start, TAXA, bpm)
    fim = UMA_HORA_24 + _amostras(current_end, TAXA, bpm)

    lista = passadas(node, Fraction(110), TAXA, UMA_HORA_24, ini, fim)

    assert len(lista) == 2
    assert lista[-1].timeline_end == fim + T_48
    assert lista[-1].fade_out_volta == T_48


def test_fronteira_128_bpm_44100_hz_periodo_um_terco_ate_beat_4():
    taxa = 44100
    bpm = 128.0
    node = _no_loop(
        0.0,
        4.0,
        loop_start=0.0,
        loop_end="0.3333333333333333",
        warp=[(0.0, 1.0), (16.0, 8.5)],
        modo="3",
    )
    ini = _amostras(0.0, taxa, bpm)
    fim = _amostras(4.0, taxa, bpm)
    t_esperado = round(CAUDA_S * taxa)

    lista = passadas(node, Fraction(128), taxa, 0, ini, fim)

    assert len(lista) == 12
    assert lista[-1].timeline_end == fim + t_esperado
    assert lista[-1].fade_out_volta == t_esperado


def test_loop_em_tercinas_float_do_live_fim_numa_volta():
    # P = 1/3 beat como float do Live, 3 voltas ate CurrentEnd = 1.0 beat.
    terco = repr(1.0 / 3.0)
    node = _no_loop(
        0.0,
        1.0,
        loop_start=2.0,
        loop_end=str(2.0 + 1.0 / 3.0),
        modo="3",
    )
    node_terco = _no_loop(
        0.0,
        1.0,
        loop_start=0.0,
        loop_end=terco,
        warp=[(0.0, 1.0), (16.0, 9.0)],
        modo="3",
    )
    for n in (node, node_terco):
        ini = UMA_HORA_24
        fim = UMA_HORA_24 + _amostras(1.0)
        lista = passadas(n, Fraction(120), TAXA, UMA_HORA_24, ini, fim)
        assert len(lista) == 3
        assert lista[-1].timeline_end == fim + T_48
        assert lista[-1].fade_out_volta == T_48


def test_round_de_meia_amostra_igual_ao_de_ctx_samples():
    # A 120 BPM e 48001 Hz, 1 beat = 24000.5 amostras -> round(24000.5) = 24000.
    # 3 beats = 72001.5 amostras -> round(72001.5) = 72002.
    taxa = 48001
    anchor = Anchor(
        sample_rate=taxa,
        bpm=120.0,
        timecode_rate=TimecodeRate(24, 1),
        session_start_samples=0,
        live_origin_samples=100,
    )
    session = SessionIR(
        name="s",
        sample_rate=taxa,
        timecode_rate=TimecodeRate(24, 1),
        start_timecode_samples=0,
    )
    ctx = ReadContext(Path("set.als"), anchor, Report(), session, sem_sidecar=True)
    node = _no_loop(0.0, 4.0, loop_start=2.0, loop_end=3.0, modo="3")
    ini = ctx.absolute(0.0)
    fim = ctx.absolute(4.0)

    lista = passadas(node, ctx.tempo, taxa, anchor.live_origin_samples, ini, fim)

    assert lista[0].timeline_end == ctx.absolute(1.0)
    assert lista[1].timeline_end == ctx.absolute(2.0)
    assert lista[2].timeline_end == ctx.absolute(3.0)


@pytest.mark.parametrize(
    "ajustes,causas_esperadas",
    [
        # P <= L (192 amostras = 0.008 beats a 120 BPM / 48 kHz)
        ({"loop_start": 2.0, "loop_end": 2.005}, ["loop"]),
        # StartRelative >= P
        ({"loop_start": 2.0, "loop_end": 4.0, "start_relative": 2.0}, ["loop", "StartRelative"]),
        # StartRelative < 0
        ({"loop_start": 2.0, "loop_end": 4.0, "start_relative": -0.5}, ["loop", "StartRelative"]),
        # StartRelative sem loop
        ({"loop_on": False, "start_relative": 0.5}, ["StartRelative"]),
        # Leitura de LoopStart < L (k05: LoopStart = 0 com marcador (0, 0))
        ({"loop_start": 0.0, "loop_end": 2.0}, ["loop"]),
        # Primeira passada <= L (volta_1 - inicio_amostras <= 192: b_1 = 0.005 beats = 120 samples)
        (
            {"loop_start": 2.0, "loop_end": 4.0, "start_relative": 1.995},
            ["loop", "StartRelative"],
        ),
        # CurrentEnd <= CurrentStart
        ({"inicio": 4.0, "fim": 4.0}, ["loop"]),
        # Marcadores invertidos em SecTime
        ({"warp": [(0.0, 2.0), (4.0, 0.0)]}, ["warp", "loop"]),
        # Transposicao junto com loop
        ({"pitch_coarse": 2}, ["loop", "transpose"]),
        ({"pitch_fine": -5}, ["loop", "transpose"]),
        # Modo Beats
        ({"modo": "0"}, ["beats mode", "loop"]),
        # Modo Tones / Texture (nao medidos)
        ({"modo": "1"}, ["warp mode", "loop"]),
        # Warp que nao e 1:1
        ({"warp": [(0.0, 0.0), (8.0, 5.0)]}, ["warp", "loop"]),
        # Loop sem warp (M5, k08)
        ({"warp": []}, ["loop"]),
    ],
)
def test_casos_de_borda_nao_expansiveis_voltam_a_pedir_render(ajustes, causas_esperadas):
    opcoes = dict(ajustes)
    inicio = opcoes.pop("inicio", 0.0)
    fim = opcoes.pop("fim", 8.0)
    node = _no_loop(inicio, fim, **opcoes)
    ini_abs = UMA_HORA_24 + _amostras(inicio)
    fim_abs = UMA_HORA_24 + _amostras(fim)

    assert not loop_expansivel(node, Fraction(120), TAXA, UMA_HORA_24, ini_abs, fim_abs)
    causas = precisa_render(node, Fraction(120), TAXA, UMA_HORA_24, ini_abs, fim_abs)
    assert causas == causas_esperadas
    with pytest.raises(ValueError, match="expandable"):
        passadas(node, Fraction(120), TAXA, UMA_HORA_24, ini_abs, fim_abs)


def test_start_relative_ausente_vale_zero():
    node = _no_loop(0.0, 4.0, loop_start=2.0, loop_end=4.0, start_relative=None)
    ini = UMA_HORA_24
    fim = UMA_HORA_24 + _amostras(4.0)

    assert loop_expansivel(node, Fraction(120), TAXA, UMA_HORA_24, ini, fim)
    lista = passadas(node, Fraction(120), TAXA, UMA_HORA_24, ini, fim)
    assert len(lista) == 2
    assert lista[0].source_offset == 48000


def test_precisa_render_sem_limites_mantem_loop_e_start_relative():
    node = _no_loop(0.0, 8.0, loop_start=2.0, loop_end=4.0, start_relative=0.5)

    assert precisa_render(node, Fraction(120), TAXA) == ["loop", "StartRelative"]


def _ajustar_loop_end_no_als(als: Path, ids: tuple[str, ...], loop_end: float) -> None:
    root = ET.parse(gzip.open(als, "rb")).getroot()
    for cid in ids:
        el = root.find(f".//AudioClip[@Id='{cid}']")
        assert el is not None
        set_value(el, "Loop/LoopEnd", loop_end)
    with gzip.open(als, "wb") as handle:
        handle.write(ET.tostring(root, encoding="utf-8"))


def test_unicidade_de_id_e_event_index_e_um_so_crossfade_not_proven(tmp_path):
    wav = escrever_wav(tmp_path, "dx.wav")
    c0 = clip(
        wav,
        0.0,
        6.0,
        nome="loop_xf",
        loop_start=2.0,
        warp=UM_PARA_UM_120,
        modo="3",
        loop_on=True,
        crossfade=True,
    )
    c1 = clip(wav, 8.0, 10.0, nome="depois")
    als = montar_set(tmp_path, [("DX", [c0, c1])])
    _ajustar_loop_end_no_als(als, ("0",), 4.0)

    report = Report(strict=False)
    session = read_session(als, report, start=start_padrao())

    clips = session.tracks[0].clips
    assert [c.id for c in clips] == ["t0c0", "t0c0p1", "t0c0p2", "t0c1"]
    assert [c.event_index for c in clips] == [0, 2, 3, 1]
    assert len({c.id for c in clips}) == len(clips)
    assert len({c.event_index for c in clips}) == len(clips)
    assert [c.crossfade_in_state for c in clips] == [True, False, False, False]

    erros_xf = [i for i in report.issues if i.code == "ALS_CROSSFADE_NOT_PROVEN"]
    assert len(erros_xf) == 1
    assert erros_xf[0].context["clip_id"] == "t0c0"


def test_fades_do_clip_limitados_na_primeira_e_ultima_passada_e_corte_da_cauda(tmp_path):
    wav = escrever_wav(tmp_path, "dx.wav")
    # Clip A: termina no meio da 2a passada (0 a 3 beats, P=2 beats), fade_in=1.5, fade_out=1.5.
    # Passada 0: 0 a 2 beats (duracao 48000, fade_out_volta 192) -> fade_in fica 36000.
    # Passada 1: 2 beats - L a 3 beats (duracao 24192, fade_in_volta 192) -> max fade_out = 24000.
    c_meio = clip(
        wav,
        0.0,
        3.0,
        nome="meio",
        loop_start=2.0,
        warp=UM_PARA_UM_120,
        modo="3",
        loop_on=True,
        fade_in=1.5,
        fade_out=1.5,
    )
    # Clip B: termina numa volta (4 a 8 beats, P=2 beats) com fade_out=0.5 beat (12000 samples).
    # Encostado em Clip C em beat 8: cauda cortada, ALS_LOOP_TAIL_DROPPED e fade_out do clip.
    c_volta = clip(
        wav,
        4.0,
        8.0,
        nome="com_cauda",
        loop_start=2.0,
        warp=UM_PARA_UM_120,
        modo="3",
        loop_on=True,
        fade_out=0.5,
    )
    c_seguinte = clip(wav, 8.0, 10.0, nome="seguinte")
    als = montar_set(tmp_path, [("DX", [c_meio, c_volta, c_seguinte])])
    _ajustar_loop_end_no_als(als, ("0", "1"), 4.0)

    report = Report(strict=False)
    session = read_session(als, report, start=start_padrao())
    clips = session.tracks[0].clips

    assert clips[0].fade_in == FadeIR(kind="in", duration_samples=36000)
    assert clips[0].fade_out == FadeIR(kind="out", duration_samples=L_48)
    assert clips[1].fade_in == FadeIR(kind="in", duration_samples=L_48)
    assert clips[1].fade_out == FadeIR(kind="out", duration_samples=24000)

    assert clips[3].timeline_end_samples == clips[4].timeline_start_samples
    assert clips[3].fade_out == FadeIR(kind="out", duration_samples=12000)
    notas = [i for i in report.issues if i.code == "ALS_LOOP_TAIL_DROPPED"]
    assert len(notas) == 1
    assert notas[0].severity == "info"
    assert notas[0].context["clip"] == "com_cauda"


def test_export_e_verify_sem_sidecar_com_clip_em_loop_cria_transition_por_volta(tmp_path):
    wav = escrever_wav(tmp_path, "loop.wav")
    c_loop = clip(
        wav,
        0.0,
        7.0,
        nome="loop_clip",
        loop_start=2.0,
        warp=UM_PARA_UM_120,
        modo="4",
        loop_on=True,
    )
    als = montar_set(tmp_path, [("DX", [c_loop])])
    # P = 2 beats (LoopStart=2, LoopEnd=4); clip de 0 a 7 beats -> 3 voltas e 4 passadas.
    _ajustar_loop_end_no_als(als, ("0",), 4.0)

    aaf = tmp_path / "saida.aaf"
    report = Report(strict=True)
    export_session(als, aaf, report, start=start_padrao())

    codigos = [i.code for i in report.issues]
    assert "ALS_RENDER_IN_LIVE" not in codigos
    assert not [i for i in report.issues if i.severity == "error"]

    with aaf2.open(str(aaf), "r") as f:
        comp = next(m for m in f.content.mobs if type(m).__name__ == "CompositionMob")
        slot = next(
            s
            for s in comp.slots
            if getattr(getattr(s, "segment", None), "media_kind", "") == "Sound"
        )
        seq = slot.segment
        while type(seq).__name__ != "Sequence":
            seq = next(iter(seq.segments))
        transicoes = [c for c in seq.components if type(c).__name__ == "Transition"]
        assert len(transicoes) == 3
        assert all(int(t.length) == L_48 for t in transicoes)

    resultado = verify_conversion(aaf, als)
    assert resultado.ok is True
    assert resultado.errors == []
    assert resultado.audio_expected == 4
    assert resultado.audio_compared == 4
    assert all(row.delta_samples == 0 and row.delta_end_samples == 0 for row in resultado.rows)
