"""Unidades do .als e crossfade da ida na forma medida no Live (15/11/2026).

Num clip de audio sem warp os marcadores de loop (LoopStart, LoopEnd,
HiddenLoopEnd) sao segundos do arquivo e o OutMarker e o fim da fonte em beats;
posicao e fades sao beats do arranjo. A 60 BPM as duas unidades coincidem, e
so os outros tempos mostram a diferenca.

O crossfade da ida segue o modelo medido em
``.local/artifacts/aafton-live-aaf-qualquer-set-20261115/xfade-live/``: o Live
toca o crossfade inteiro antes da juncao, entao a juncao e o fim do clip que
sai no AAF e o clip que entra comeca ali, com o LoopStart avancado L.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aafton.aaf_read import read_aaf
from aafton.als_crossfade_ler import crossfade_do_live
from aafton.als_read import read_session
from aafton.als_xml import get_value, load_als
from aafton.cli import main
from aafton.ir import FadeIR, MediaAsset, SessionIR, TrackIR
from aafton.report import Report
from aafton.sidecar import read_anchor, sidecar_path_for
from aafton.timecode import TimecodeRate
from tests.aaf_factory import build_aaf_with_transition, build_linked_aaf, wav_bytes
from tests.unit.test_als_crossfade import (
    CROSSFADE,
    DURACAO,
    OFFSET_B,
    ORIGEM,
    TEMPLATE,
    _clip,
    _escrever,
    _sessao,
    _wav_pcm,
)

pytestmark = pytest.mark.skipif(
    not TEMPLATE.exists(), reason="Portao B: templates/live12.als ainda nao foi autorado"
)

SR = 48000
CAMINHO_CLIPS = ".//AudioTrack//ArrangerAutomation/Events/AudioClip"


# --- Ajudantes --------------------------------------------------------------


def _wav(pasta: Path, segundos: int) -> Path:
    """WAV mono de 48 kHz com a duracao pedida."""
    wav = pasta / "fonte.wav"
    wav.write_bytes(wav_bytes(SR * segundos, channels=1, sample_rate=SR))
    return wav


def _importar(origem: Path, pasta: Path, bpm: float) -> Path:
    """Importa o AAF para o .als no tempo pedido."""
    als = pasta / "saida.als"
    codigo = main(
        [
            "import",
            str(origem),
            str(als),
            "--media",
            "link",
            "--audio-dir",
            str(pasta),
            "--template",
            str(TEMPLATE),
            "--bpm",
            str(bpm),
        ]
    )
    assert codigo == 0
    return als


def _exportar(als: Path, destino: Path) -> list:
    """Exporta o .als de volta a AAF e devolve os clips de audio."""
    assert main(["export", str(als), str(destino)]) == 0
    return _clips_do_aaf(destino)


def _clips_do_aaf(caminho: Path) -> list:
    return read_aaf(caminho, Report(strict=False)).tracks[0].clips


def _clips_do_als(caminho: Path) -> list:
    """Os AudioClip do arranjo na ordem do inicio."""
    nos = load_als(caminho).findall(CAMINHO_CLIPS)
    return sorted(nos, key=lambda no: float(get_value(no, "CurrentStart")))


def _amostras(texto: str, bpm: float) -> int:
    """Beats do arranjo em samples, pela formula do leitor."""
    return round(float(texto) * 60.0 / bpm * SR)


def _mesma_geometria(exportado: list, origem: list) -> None:
    for destino, fonte in zip(exportado, origem, strict=True):
        assert destino.timeline_start_samples == fonte.timeline_start_samples
        assert destino.timeline_end_samples == fonte.timeline_end_samples
        assert destino.source_offset_samples == fonte.source_offset_samples


# --- Unidades ---------------------------------------------------------------


@pytest.mark.parametrize("bpm", [60.0, 96.0, 120.0, 59.0])
def test_ida_e_volta_das_unidades_em_varios_tempos(tmp_path, bpm):
    """LoopStart em segundos, OutMarker no fim do arquivo em beats, volta exata."""
    wav = _wav(tmp_path, 60)
    clips = [(0, 48007, 617), (96013, 24000, 48211)]
    origem = build_linked_aaf(tmp_path / "origem.aaf", clips, wav)
    als = _importar(origem, tmp_path, bpm)

    nos = _clips_do_als(als)
    assert len(nos) == len(clips)
    for no, (_inicio, _duracao, offset) in zip(nos, clips):
        assert float(get_value(no, "Loop/LoopStart")) * SR == pytest.approx(offset, abs=1e-6)
        # O arquivo tem 60 s: o fim dele em beats e 60 x tempo / 60.
        assert float(get_value(no, "Loop/OutMarker")) == pytest.approx(60.0 * bpm / 60.0)

    exportado = _exportar(als, tmp_path / "volta.aaf")
    _mesma_geometria(exportado, _clips_do_aaf(origem))


# --- Crossfade da ida -------------------------------------------------------


@pytest.mark.parametrize("bpm", [60.0, 96.0, 120.0])
def test_crossfade_da_ida_na_forma_medida(tmp_path, bpm):
    """B comeca no fim de A do AAF, L adiante na fonte, e a volta da a transicao."""
    wav = _wav(tmp_path, 60)
    origem = build_aaf_with_transition(tmp_path / "origem.aaf", wav)
    fonte_a, fonte_b = _clips_do_aaf(origem)
    L = fonte_a.timeline_end_samples - fonte_b.timeline_start_samples
    assert L == SR // 2

    als = _importar(origem, tmp_path, bpm)
    origem_live = read_anchor(als).live_origin_samples
    no_a, no_b = _clips_do_als(als)

    fim_a = get_value(no_a, "CurrentEnd")
    assert origem_live + _amostras(fim_a, bpm) == fonte_a.timeline_end_samples
    assert _amostras(get_value(no_a, "Fades/FadeOutLength"), bpm) == L
    assert get_value(no_b, "CurrentStart") == fim_a
    assert no_b.get("Time") == fim_a
    offset_b = (fonte_b.source_offset_samples or 0) + L
    assert float(get_value(no_b, "Loop/LoopStart")) * SR == pytest.approx(offset_b, abs=1e-6)
    assert float(get_value(no_b, "Fades/FadeInLength")) == 0.0
    assert get_value(no_b, "Fades/CrossfadeInState") == "1"

    exportado = _exportar(als, tmp_path / "volta.aaf")
    _mesma_geometria(exportado, [fonte_a, fonte_b])
    assert exportado[0].fade_out is not None and fonte_a.fade_out is not None
    assert exportado[0].fade_out.duration_samples == fonte_a.fade_out.duration_samples
    assert exportado[1].fade_in is not None and fonte_b.fade_in is not None
    assert exportado[1].fade_in.duration_samples == fonte_b.fade_in.duration_samples


def test_clip_que_entra_do_tamanho_do_crossfade_fica_em_fades(tmp_path):
    """Com B de duracao <= L a forma nativa deixaria B com comprimento zero."""
    wav = _wav_pcm(tmp_path / "fonte.wav", SR, SR * 4)
    clip_a = _clip(
        0,
        ORIGEM,
        ORIGEM + DURACAO,
        0,
        fade_out=FadeIR(kind="out", duration_samples=CROSSFADE),
    )
    clip_b = _clip(
        1,
        ORIGEM + DURACAO - CROSSFADE,
        ORIGEM + DURACAO,
        OFFSET_B,
        fade_in=FadeIR(kind="in", duration_samples=CROSSFADE),
    )
    destino = _escrever(tmp_path, _sessao(wav, [clip_a, clip_b]))

    no_a, no_b = load_als(destino).findall(CAMINHO_CLIPS)
    assert get_value(no_b, "Fades/CrossfadeInState") == "0"
    assert get_value(no_a, "CurrentEnd") != get_value(no_b, "CurrentStart")
    assert float(get_value(no_b, "Fades/FadeInLength")) == pytest.approx(0.5)

    lido = read_session(destino, Report())
    geometria = [(c.timeline_start_samples, c.timeline_end_samples) for c in lido.tracks[0].clips]
    assert geometria == [
        (ORIGEM, ORIGEM + DURACAO),
        (ORIGEM + DURACAO - CROSSFADE, ORIGEM + DURACAO),
    ]


# --- O modelo medido no Live ------------------------------------------------


def test_modelo_do_live_com_os_valores_medidos():
    """96 BPM, FadeOutLength 1,25 beat e LoopStart 0,625 s: L = 0,625 s."""
    bpm = 96.0
    juncao = round(9 * 60.0 / bpm * SR)
    fade_out = round(1.25 * 60.0 / bpm * SR)
    loop_start = round(0.625 * SR)

    L, inicio_b, offset_b = crossfade_do_live(juncao, fade_out, loop_start)

    assert L == round(0.625 * SR)
    assert inicio_b == juncao - round(0.625 * SR)
    assert offset_b == 0


def _sessao_medida(wav: Path) -> SessionIR:
    """O par do set medido no Live: A ate 9 beats, B de 9 a 40 beats, a 96 BPM."""
    juncao = 270000
    asset = MediaAsset(
        id="m1",
        filename=wav.name,
        channels=1,
        sample_rate=SR,
        duration_samples=SR * 20,
        status="resolved",
        resolved_path=str(wav),
    )
    clip_a = _clip(
        0, ORIGEM, ORIGEM + juncao, 0, fade_out=FadeIR(kind="out", duration_samples=37500)
    )
    clip_b = _clip(1, ORIGEM + juncao, ORIGEM + 1200000, 30000)
    clip_b.crossfade_in_state = True
    track = TrackIR(id="t0", source_index=0, name="XFADE", kind="audio", channel_count=1)
    track.clips.extend([clip_a, clip_b])
    return SessionIR(
        name="xfade",
        sample_rate=SR,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=ORIGEM,
        tracks=[track],
        media=[asset],
    )


def test_set_medido_a_96_bpm_le_pelo_modelo(tmp_path):
    """Os campos que o Live gravou no teste voltam com o crossfade que ele tocou."""
    wav = _wav(tmp_path, 20)
    destino = _escrever(tmp_path, _sessao_medida(wav), bpm=96.0)

    no_a, no_b = _clips_do_als(destino)
    assert float(get_value(no_a, "CurrentEnd")) == pytest.approx(9.0)
    assert float(get_value(no_a, "Fades/FadeOutLength")) == pytest.approx(1.25)
    assert float(get_value(no_a, "Loop/OutMarker")) == pytest.approx(32.0)
    assert float(get_value(no_a, "Loop/HiddenLoopEnd")) == pytest.approx(5.625)
    assert float(get_value(no_b, "CurrentStart")) == pytest.approx(9.0)
    assert float(get_value(no_b, "CurrentEnd")) == pytest.approx(40.0)
    assert float(get_value(no_b, "Loop/LoopStart")) == pytest.approx(0.625)
    assert get_value(no_b, "Fades/CrossfadeInState") == "1"
    assert float(get_value(no_b, "Fades/FadeInLength")) == 0.0
    sidecar = json.loads(sidecar_path_for(destino).read_text(encoding="utf-8"))
    assert sidecar["format_version"] == 3

    clip_a, clip_b = read_session(destino, Report()).tracks[0].clips
    assert clip_a.timeline_end_samples == ORIGEM + 270000
    assert clip_a.fade_out is not None and clip_a.fade_out.duration_samples == 30000
    assert clip_b.timeline_start_samples == ORIGEM + 270000 - 30000
    assert clip_b.timeline_end_samples == ORIGEM + 1200000
    assert clip_b.source_offset_samples == 0
    assert clip_b.fade_in is not None and clip_b.fade_in.duration_samples == 30000
