"""Tempo no .als cai exatamente na amostra (D12a, calibracao P03).

A calibracao mostrou perda por seis casas decimais na fonte. SaveAs humanos
posteriores provaram que clips tambem passam por uma grade antes de tocar.
Fonte, video, fades, marcadores e envelopes conservam precisao double; clips
recuperam os mesmos inteiros de inicio/fim. O teste independente do relogio
fica em test_als_clip_clock.py, com os valores salvos pelo proprio Live.
"""

from __future__ import annotations

import math
import random
from pathlib import Path

import pytest

from aafton.als_paths import AUDIO_CLIP_FIELDS, find_prototype
from aafton.als_read import read_session
from aafton.als_write import (
    SOURCE,
    TIMELINE,
    WriteOptions,
    build_audio_clip,
    samples_to_beats,
    write_session,
)
from aafton.als_xml import (
    IdAllocator,
    classify_id_tags,
    format_float,
    get_value,
    load_als,
)
from aafton.ir import ClipIR, FadeIR, MarkerIR, MediaAsset, SessionIR, TrackIR
from aafton.report import Report
from aafton.timecode import TimecodeRate

TEMPLATE = Path("templates/live12.als")
SR = 48000
UMA_HORA = 3600 * SR
TEMPOS = (60.0, 120.0, 97.0)


def _volta(texto: str, bpm: float, sample_rate: int = SR) -> float:
    """Conversao double, sem a grade interna dos clips."""
    return float(texto) * 60.0 / bpm * sample_rate


def _na_timeline(texto: str, esperado: int, bpm: float) -> None:
    valor = _volta(texto, bpm)
    assert valor <= esperado, f"{texto} passa de {esperado}: {valor!r}"
    assert esperado - valor < 1e-6, f"{texto} longe de {esperado}: {valor!r}"


def _na_fonte(texto: str, esperado: int, bpm: float) -> None:
    valor = _volta(texto, bpm)
    assert valor >= esperado, f"{texto} fica antes de {esperado}: {valor!r}"
    assert valor - esperado < 1e-6, f"{texto} longe de {esperado}: {valor!r}"


def _na_posicao_clip(texto: str, esperado: int, bpm: float) -> None:
    valor = _volta(texto, bpm)
    assert round(valor) == esperado
    assert math.ceil(valor) == esperado


@pytest.fixture
def contexto():
    root = load_als(TEMPLATE)
    posicionais, unicos = classify_id_tags(root)
    return IdAllocator.from_root(root), find_prototype(root, "AudioClip"), posicionais, unicos


def _asset() -> MediaAsset:
    return MediaAsset(
        id="m",
        filename="mono.wav",
        channels=1,
        sample_rate=SR,
        duration_samples=3 * SR,
        status="resolved",
        resolved_path="C:/audio/mono.wav",
    )


def _clip(start: int, duration: int, offset: int) -> ClipIR:
    return ClipIR(
        id=f"c{start}",
        track_id="t",
        channel_index=0,
        event_index=0,
        name="mono",
        media_id="m",
        timeline_start_samples=UMA_HORA + start,
        timeline_end_samples=UMA_HORA + start + duration,
        source_offset_samples=offset,
    )


@pytest.mark.parametrize("bpm", TEMPOS)
@pytest.mark.parametrize(
    "start,duration,offset",
    [
        (48007, 48000, 0),  # CAL_MONO: inicio fracionario em 6 casas
        (48000, 48000, 617),  # CAL_OFFSET: offset fracionario em 6 casas
        (96013, 24000, 48211),  # segundo clip de CAL_TWO_CLIPS
    ],
)
def test_clip_cai_na_amostra_exata(contexto, bpm, start, duration, offset):
    allocator, prototipo, posicionais, unicos = contexto
    clip = _clip(start, duration, offset)
    clip.fade_in = FadeIR(kind="in", duration_samples=4801)
    clip.fade_out = FadeIR(kind="out", duration_samples=4799)
    node = build_audio_clip(
        clip,
        _asset(),
        prototipo,
        allocator,
        posicionais,
        unicos,
        WriteOptions(sample_rate=SR, bpm=bpm, live_origin_samples=UMA_HORA),
        Report(strict=False),
    )

    _na_posicao_clip(node.get("Time"), start, bpm)
    _na_posicao_clip(get_value(node, AUDIO_CLIP_FIELDS["current_start"]), start, bpm)
    _na_posicao_clip(get_value(node, AUDIO_CLIP_FIELDS["current_end"]), start + duration, bpm)
    _na_fonte(get_value(node, AUDIO_CLIP_FIELDS["loop_start"]), offset, bpm)
    _na_fonte(get_value(node, AUDIO_CLIP_FIELDS["loop_end"]), offset + duration, bpm)
    _na_fonte(get_value(node, AUDIO_CLIP_FIELDS["out_marker"]), offset + duration, bpm)
    _na_timeline(get_value(node, AUDIO_CLIP_FIELDS["fade_in_length"]), 4801, bpm)
    _na_timeline(get_value(node, AUDIO_CLIP_FIELDS["fade_out_length"]), 4799, bpm)


def test_precisao_double_da_fonte_e_do_video_nao_e_truncada(contexto):
    allocator, prototipo, posicionais, unicos = contexto
    node = build_audio_clip(
        _clip(48007, 48000, 617),
        _asset(),
        prototipo,
        allocator,
        posicionais,
        unicos,
        WriteOptions(sample_rate=SR, bpm=60.0, live_origin_samples=UMA_HORA),
        Report(),
        is_video=True,
    )
    assert get_value(node, AUDIO_CLIP_FIELDS["current_start"]) == "1.0001458333333333"
    assert float(get_value(node, AUDIO_CLIP_FIELDS["loop_start"])) * SR == 617.0


@pytest.mark.parametrize("bpm", TEMPOS)
def test_set_inteiro_volta_sample_exato_com_marcador_e_envelope(tmp_path, bpm):
    asset = MediaAsset(
        id="m",
        filename="mono.wav",
        channels=1,
        sample_rate=SR,
        duration_samples=3 * SR,
        status="resolved",
        resolved_path=str(tmp_path / "mono.wav"),
    )
    clips = [_clip(48007, 12000, 0), _clip(96013, 24000, 48211)]
    track = TrackIR(
        id="t",
        source_index=0,
        name="CAL_TWO_CLIPS",
        kind="audio",
        channel_count=1,
        clips=clips,
        gain_points=[(UMA_HORA + 48007, 1.0), (UMA_HORA + 96013, 0.5)],
    )
    session = SessionIR(
        name="precisao",
        sample_rate=SR,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=UMA_HORA,
        tracks=[track],
        media=[asset],
        markers=[MarkerIR(id="k", name="marca", position_samples=UMA_HORA + 96013)],
    )
    destino = tmp_path / "precisao.als"
    write_session(
        session,
        TEMPLATE,
        destino,
        WriteOptions(sample_rate=SR, bpm=bpm, live_origin_samples=UMA_HORA),
        Report(strict=False),
    )

    root = load_als(destino)
    audio_clips = root.findall(".//LiveSet/Tracks/AudioTrack//AudioClip")
    assert [c.find("CurrentStart").get("Value") for c in audio_clips] == [
        c.get("Time") for c in audio_clips
    ]
    for node, (start, end, offset) in zip(audio_clips, [(48007, 60007, 0), (96013, 120013, 48211)]):
        _na_posicao_clip(node.find("CurrentStart").get("Value"), start, bpm)
        _na_posicao_clip(node.find("CurrentEnd").get("Value"), end, bpm)
        _na_fonte(node.find("Loop/LoopStart").get("Value"), offset, bpm)
    eventos = [
        e
        for e in root.findall(".//LiveSet/Tracks/AudioTrack//FloatEvent")
        if float(e.get("Time")) >= 0
    ]
    for evento, esperado in zip(eventos, (48007, 96013)):
        _na_timeline(evento.get("Time"), esperado, bpm)
    marca = [
        item
        for item in root.findall(".//LiveSet/Locators/Locators/Locator")
        if item.find("Name").get("Value") == "marca"
    ][0]
    _na_timeline(marca.find("Time").get("Value"), 96013, bpm)

    lido = read_session(destino, Report(strict=False))
    assert [
        (
            c.timeline_start_samples - UMA_HORA,
            c.timeline_end_samples - UMA_HORA,
            c.source_offset_samples,
        )
        for c in lido.tracks[0].clips
    ] == [(48007, 60007, 0), (96013, 120013, 48211)]


@pytest.mark.parametrize("side", (TIMELINE, SOURCE))
def test_conversao_fica_no_lado_certo_e_abaixo_de_um_milionesimo(side):
    rng = random.Random(20261013)
    for bpm in (60.0, 120.0, 97.0, 133.7):
        for sample_rate in (44100, 48000, 96000):
            for _ in range(300):
                samples = rng.randrange(0, 2**31)
                beats = samples_to_beats(samples, sample_rate, bpm, side)
                texto = format_float(beats)
                assert float(texto) == beats
                volta = float(texto) * 60.0 / bpm * sample_rate
                if side == TIMELINE:
                    assert volta <= samples
                else:
                    assert volta >= samples
                assert abs(volta - samples) < 1e-6


def test_formato_tem_precisao_total_e_nunca_usa_expoente():
    for valor in (
        0.0,
        -0.0,
        1.0,
        3600.0,
        1 / SR,
        617 / SR,
        1.0001458333333333,
        56.664675168581418,
        1e-9,
        123456789.123,
    ):
        texto = format_float(valor)
        assert "e" not in texto.lower()
        assert float(texto) == valor
    assert format_float(48.0) == "48"
    assert format_float(-0.0) == "0"
    with pytest.raises(ValueError):
        format_float(math.inf)
