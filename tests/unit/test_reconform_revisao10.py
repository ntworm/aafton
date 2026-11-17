"""Regressoes da revisao de entrega 10 do reconform.

Trechos removidos diferentes com a mesma emenda e a mesma duracao no corte
novo nao dividem uma track [saiu]; e o verify mede a posicao da peca removida
a partir do comeco do trecho, nao do plano antes de uma lacuna de V1.
"""

from __future__ import annotations

from pathlib import Path

from aafton.als_paths import AUDIO_CLIP_FIELDS, TRACK_FIELDS
from aafton.als_xml import get_value, load_als
from aafton.cut_ir import CutIR
from aafton.reconform_apply import apply_reconform
from aafton.reconform_diff import diff_cuts
from aafton.reconform_verify import verify_reconform
from aafton.report import Report
from tests.unit.test_reconform_revisao7 import _clip, _lacuna
from tests.unit.test_reconform_tracks import HOUR_F25, RATE_25, _make_set, _track_spec

H = HOUR_F25


def _aplica(tmp_path: Path, antigo: CutIR, novo: CutIR, frames: tuple[int, int]):
    set_path = _make_set(tmp_path, [_track_spec(0, "Voz", frames)], bpm=60.0)
    res = apply_reconform(set_path, diff_cuts(antigo, novo, Report()), Report(strict=False))
    assert res.written
    faixas: dict[str, list[tuple[float, float]]] = {}
    for track in load_als(res.output).findall("./LiveSet/Tracks/AudioTrack"):
        faixas[get_value(track, TRACK_FIELDS["name"])] = [
            (
                float(get_value(c, AUDIO_CLIP_FIELDS["current_start"])),
                float(get_value(c, AUDIO_CLIP_FIELDS["current_end"])),
            )
            for c in track.findall(".//AudioClip")
        ]
    verificado = verify_reconform(antigo, novo, set_path, res.output, Report(strict=False))
    return faixas, verificado


def _sem_sobreposicao(clips: list[tuple[float, float]]) -> bool:
    ordem = sorted(clips)
    return all(a[1] <= b[0] + 1e-9 for a, b in zip(ordem, ordem[1:]))


def test_tres_trechos_com_a_mesma_emenda_ficam_em_tres_tracks(tmp_path: Path) -> None:
    antigo = CutIR(
        "o",
        RATE_25,
        H,
        [
            _clip(0, "A", H, H + 20, 100),
            _clip(1, "X1", H + 20, H + 30, 500),
            _lacuna(2, H + 30, H + 40),
            _clip(3, "X2", H + 40, H + 50, 700),
            _clip(4, "C", H + 50, H + 60, 300),
        ],
    )
    novo = CutIR(
        "n", RATE_25, H, [_clip(0, "A", H, H + 20, 100), _clip(1, "C", H + 20, H + 30, 300)]
    )
    faixas, verificado = _aplica(tmp_path, antigo, novo, (22, 48))
    saiu = {nome: clips for nome, clips in faixas.items() if "[saiu]" in nome}
    assert len(saiu) == 3
    assert all(len(clips) == 1 and _sem_sobreposicao(clips) for clips in saiu.values())
    assert verificado.ok, verificado.problems


def test_verify_aceita_peca_removida_depois_de_lacuna_mantida(tmp_path: Path) -> None:
    antigo = CutIR(
        "o",
        RATE_25,
        H,
        [
            _clip(0, "A", H, H + 20, 100),
            _lacuna(1, H + 20, H + 40),
            _clip(2, "X", H + 40, H + 60, 500),
        ],
    )
    novo = CutIR("n", RATE_25, H, [_clip(0, "A", H, H + 20, 100)])
    faixas, verificado = _aplica(tmp_path, antigo, novo, (45, 55))
    # A peca de X vai para a emenda (fim de A, 20f) mais 5f dentro de X.
    assert faixas["Voz [saiu]"] == [(1.0, 1.4)]
    assert verificado.ok, verificado.problems


def test_verify_aceita_peca_removida_depois_de_lacuna_fechada(tmp_path: Path) -> None:
    antigo = CutIR(
        "o",
        RATE_25,
        H,
        [
            _clip(0, "A", H, H + 20, 100),
            _lacuna(1, H + 20, H + 40),
            _clip(2, "X", H + 40, H + 60, 500),
            _clip(3, "C", H + 60, H + 70, 300),
        ],
    )
    novo = CutIR(
        "n", RATE_25, H, [_clip(0, "A", H, H + 20, 100), _clip(1, "C", H + 20, H + 30, 300)]
    )
    _faixas, verificado = _aplica(tmp_path, antigo, novo, (45, 55))
    assert verificado.ok, verificado.problems
