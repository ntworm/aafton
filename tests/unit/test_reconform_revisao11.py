"""Revisao de entrega 11 do reconform: cauda no espaco livre e clips sobrepostos.

A cauda (o material depois do ultimo evento do corte antigo) so ocupa o
espaco livre do corte novo: o que nao cabe vai para a track [saiu], na emenda.
O verify confere a posicao da cauda e acusa os clips que se sobrepoem na mesma
track da copia aplicada.
"""

from __future__ import annotations

import dataclasses
import random
import time
from pathlib import Path

from aafton.als_paths import AUDIO_CLIP_FIELDS, TRACK_FIELDS
from aafton.als_xml import get_value, load_als
from aafton.cut_ir import CutEvent, CutIR
from aafton.ir import TrackIR
from aafton.reconform_apply import apply_reconform
from aafton.reconform_diff import diff_cuts
from aafton.reconform_verify import verify_reconform
from aafton.report import Report
from tests.unit.test_reconform_revisao7 import _clip
from tests.unit.test_reconform_revisao10 import _aplica, _sem_sobreposicao
from tests.unit.test_reconform_tracks import (
    HOUR,
    HOUR_F25,
    RATE_25,
    SPF_25,
    _make_set,
    _track_spec,
)
from tests.unit.test_reconform_verify import _codigos, _mexer, _move_clip

# As fontes dos planos: cada uma so aparece uma vez em cada corte, para o
# casamento por TC de origem nunca ser ambiguo.
_FONTES = ("A", "B", "C", "D")
_TC_INICIAL = {"A": 1000, "B": 2000, "C": 3000, "D": 4000}


def _faixas(path: Path) -> dict[str, list[tuple[float, float]]]:
    """Clips de cada track da copia aplicada, em beats."""
    faixas: dict[str, list[tuple[float, float]]] = {}
    for track in load_als(path).findall("./LiveSet/Tracks/AudioTrack"):
        faixas[get_value(track, TRACK_FIELDS["name"])] = [
            (
                float(get_value(c, AUDIO_CLIP_FIELDS["current_start"])),
                float(get_value(c, AUDIO_CLIP_FIELDS["current_end"])),
            )
            for c in track.findall(".//AudioClip")
        ]
    return faixas


def _cortes_trocados() -> tuple[CutIR, CutIR]:
    """Cortes com os mesmos planos em ordem trocada."""
    antigo = CutIR(
        "antigo.edl",
        RATE_25,
        HOUR_F25,
        [
            _clip(0, "A", HOUR_F25, HOUR_F25 + 10, 100),
            _clip(1, "B", HOUR_F25 + 10, HOUR_F25 + 20, 200),
        ],
    )
    novo = CutIR(
        "novo.edl",
        RATE_25,
        HOUR_F25,
        [
            _clip(0, "B", HOUR_F25, HOUR_F25 + 10, 200),
            _clip(1, "A", HOUR_F25 + 10, HOUR_F25 + 20, 100),
        ],
    )
    return antigo, novo


def _arredondado(clips: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Clips com as posicoes arredondadas: a grade de ticks do Live tem resto."""
    return [(round(inicio, 4), round(fim, 4)) for inicio, fim in clips]


def test_cauda_que_nao_cabe_vai_para_a_track_saiu(tmp_path: Path) -> None:
    """A cauda que cai sobre o plano trocado vai para [saiu], sem sobrepor."""
    antigo, novo = _cortes_trocados()
    faixas, verificado = _aplica(tmp_path, antigo, novo, (0, 25))
    assert all(_sem_sobreposicao(clips) for clips in faixas.values()), faixas
    # A cauda (os cinco frames depois do ultimo evento) entra na emenda.
    assert _arredondado(faixas["Voz [saiu]"]) == [(0.4, 0.6)]
    assert _arredondado(faixas["Voz"]) == [(0.0, 0.4), (0.4, 0.8)]
    assert verificado.ok, verificado.problems


def _move_por_cima(root) -> None:
    """Move o clip mais novo da track para cima do clip mais antigo."""
    track = next(
        node
        for node in root.findall("./LiveSet/Tracks/AudioTrack")
        if get_value(node, TRACK_FIELDS["name"]) == "Voz"
    )
    clips = sorted(
        track.findall(".//AudioClip"),
        key=lambda c: float(get_value(c, AUDIO_CLIP_FIELDS["current_start"])),
    )
    _move_clip(clips[1], -int(0.4 * 48000))


def test_clip_sobreposto_na_mesma_track_e_acusado(tmp_path: Path) -> None:
    """Um clip movido por cima do outro e acusado com RECONFORM_VERIFY_OVERLAP."""
    antigo, novo = _cortes_trocados()
    set_path = _make_set(tmp_path, [_track_spec(0, "Voz", (0, 25))], bpm=60.0)
    res = apply_reconform(set_path, diff_cuts(antigo, novo, Report()), Report(strict=False))
    assert res.written
    mexido = _mexer(res.output, _move_por_cima)
    resultado = verify_reconform(antigo, novo, set_path, mexido, Report(strict=False))
    assert not resultado.ok
    assert "RECONFORM_VERIFY_OVERLAP" in _codigos(resultado)


def _move_a_cauda(root) -> None:
    """Move o clip da cauda na track [saiu] por cem amostras."""
    track = next(
        node
        for node in root.findall("./LiveSet/Tracks/AudioTrack")
        if get_value(node, TRACK_FIELDS["name"]) == "Voz [saiu]"
    )
    _move_clip(track.findall(".//AudioClip")[0], 100)


def test_cauda_movida_na_track_saiu_e_pega(tmp_path: Path) -> None:
    """O verify pega a cauda que saiu do lugar na track [saiu]."""
    antigo, novo = _cortes_trocados()
    set_path = _make_set(tmp_path, [_track_spec(0, "Voz", (0, 25))], bpm=60.0)
    res = apply_reconform(set_path, diff_cuts(antigo, novo, Report()), Report(strict=False))
    assert res.written
    mexido = _mexer(res.output, _move_a_cauda)
    resultado = verify_reconform(antigo, novo, set_path, mexido, Report(strict=False))
    assert not resultado.ok
    assert "RECONFORM_VERIFY_POSITION" in _codigos(resultado)


def _plano(idx: int, fonte: str, rec_in: int, rec_out: int, tc_in: int) -> CutEvent:
    """Evento de clip do corte, com o TC de origem seguindo a duracao."""
    return CutEvent(idx, "clip", fonte, None, tc_in, tc_in + (rec_out - rec_in), rec_in, rec_out)


def _lacuna(idx: int, rec_in: int, rec_out: int) -> CutEvent:
    """Evento de lacuna de V1 do corte: o som do set nessa parte nao tem imagem."""
    return CutEvent(idx, "gap", None, None, None, None, rec_in, rec_out)


def _corte_antigo(rng: random.Random) -> tuple[CutIR, int]:
    """Corte antigo com planos seguidos e o frame onde ele termina."""
    eventos: list[CutEvent] = []
    inicio = HOUR_F25 + rng.choice([0, 0, 4])
    rec = inicio
    for idx, fonte in enumerate(_FONTES[: rng.randint(2, 4)]):
        if idx and rng.random() < 0.25:
            lacuna = rng.randint(2, 5)
            eventos.append(_lacuna(len(eventos), rec, rec + lacuna))
            rec += lacuna
        duracao = rng.randint(6, 20)
        eventos.append(_plano(len(eventos), fonte, rec, rec + duracao, _TC_INICIAL[fonte]))
        rec += duracao
    return CutIR("antigo.edl", RATE_25, inicio, eventos), rec


def _corte_novo(rng: random.Random, antigos: list[CutEvent], inicio: int) -> list[CutEvent]:
    """Corte novo com os planos trocados, cortados ou removidos."""
    planos = [ev for ev in antigos if ev.kind == "clip"]
    ordem = list(range(len(planos)))
    if len(planos) > 1 and rng.random() < 0.6:
        rng.shuffle(ordem)
    escolhidos = [planos[i] for i in ordem]
    if len(escolhidos) > 1 and rng.random() < 0.4:
        escolhidos.pop(rng.randrange(len(escolhidos)))
    eventos: list[CutEvent] = []
    rec = inicio
    for ev in escolhidos:
        tc_in, tc_out = ev.source_in, ev.source_out
        duracao = tc_out - tc_in
        if rng.random() < 0.4:
            # O trim come material do plano: o que sobra fora vira [saiu].
            corte = rng.randint(1, max(1, min(3, duracao - 1)))
            if rng.random() < 0.5:
                tc_in += corte
            else:
                tc_out -= corte
            duracao = tc_out - tc_in
        eventos.append(_plano(len(eventos), ev.source, rec, rec + duracao, tc_in))
        rec += duracao
        if rng.random() < 0.2:
            # Uma lacuna de V1 abre um buraco no corte novo.
            lacuna = rng.randint(2, 5)
            eventos.append(_lacuna(len(eventos), rec, rec + lacuna))
            rec += lacuna
    return eventos


def _track_com_dois_clips(idx: int, nome: str, cortes: tuple[int, int, int, int]) -> TrackIR:
    """Track de teste com dois clips, nos intervalos informados em frames."""
    spec = _track_spec(idx, nome, (cortes[0], cortes[1]))
    primeiro = spec.clips[0]
    segundo = dataclasses.replace(
        primeiro,
        id=f"c{idx}b",
        event_index=1,
        timeline_start_samples=HOUR + cortes[2] * SPF_25,
        timeline_end_samples=HOUR + cortes[3] * SPF_25,
    )
    return dataclasses.replace(spec, clips=[primeiro, segundo])


def _set_com_duas_tracks(tmp_path: Path, cortes: list[tuple[int, int, int, int]]) -> Path:
    """Set com duas tracks, cada uma com dois clips."""
    specs = [
        _track_com_dois_clips(idx, nome, corte)
        for idx, (nome, corte) in enumerate(zip(("Voz", "Amb"), cortes))
    ]
    return _make_set(tmp_path, specs, bpm=60.0)


def _resumo(corte: CutIR) -> list[tuple]:
    """Eventos do corte em uma linha: tipo, fonte, TC de origem e registros."""
    return [
        (ev.kind, ev.source, ev.source_in, ev.source_out, ev.record_in, ev.record_out)
        for ev in corte.events
    ]


def test_cortes_aleatorios_passam_no_verify(tmp_path: Path) -> None:
    """Cortes com movimento, remocao, trim e lacunas nao quebram o apply."""
    inicio = time.perf_counter()
    for semente in range(100):
        rng = random.Random(semente)
        antigo, fim = _corte_antigo(rng)
        novo = CutIR(
            "novo.edl",
            RATE_25,
            antigo.start_frames,
            _corte_novo(rng, antigo.events, antigo.start_frames),
        )
        pasta = tmp_path / f"caso{semente}"
        pasta.mkdir()
        cauda = rng.choice([0, 3, 6])
        meio = rng.randint(1, max(1, (fim - HOUR_F25) // 2))
        cortes = [(0, meio, meio, fim - HOUR_F25 + cauda)]
        segundo = rng.randint(1, max(1, (fim - HOUR_F25) // 2))
        cortes.append((0, segundo, segundo, fim - HOUR_F25 + cauda))
        set_path = _set_com_duas_tracks(pasta, cortes)
        res = apply_reconform(set_path, diff_cuts(antigo, novo, Report()), Report(strict=False))
        assert res.written, semente
        faixas = _faixas(res.output)
        problema = (semente, _resumo(antigo), _resumo(novo), faixas)
        assert all(_sem_sobreposicao(clips) for clips in faixas.values()), problema
        resultado = verify_reconform(antigo, novo, set_path, res.output, Report(strict=False))
        assert resultado.ok, (semente, resultado.problems)
    # Os casos fixos rodam em segundos: a suite nao pode ficar lenta.
    assert time.perf_counter() - inicio < 20.0
