"""Revisao de entrega 12 do reconform: a cauda e medida pelo material do set.

A cauda (o material depois do ultimo evento do corte antigo) so vira marcador
'RC delete' quando o set tem material depois do ultimo evento, e o alcance
dela e o do material, nunca um numero sintetico. As pecas da cauda ocupam na
track [saiu] o alcance do material que elas tem, e nao um alcance artificial
que abriria uma track de overflow.
"""

from __future__ import annotations

import dataclasses
import random
from pathlib import Path

from aafton.als_paths import TRACK_FIELDS
from aafton.als_xml import get_value, load_als
from aafton.cut_ir import CutIR
from aafton.reconform_apply import apply_reconform
from aafton.reconform_diff import diff_cuts
from aafton.reconform_verify import verify_reconform
from aafton.report import Report
from tests.unit.test_reconform_fuzz import _faixas, _problemas, _set_do_fuzz, _wav
from tests.unit.test_reconform_revisao7 import _clip, _lacuna
from tests.unit.test_reconform_tracks import (
    HOUR_F25 as H,
    RATE_25,
    _track_spec,
)

# Sementes do fuzz da revisao: o mesmo do fuzz do revisor.
SEMENTES = range(900)


def _cortes() -> tuple[CutIR, CutIR]:
    """Corte antigo de tres planos e corte novo com o primeiro e o ultimo trocados."""
    antigo = CutIR(
        "antigo.edl",
        RATE_25,
        H,
        [
            _clip(0, "A", H, H + 10, 100),
            _clip(1, "B", H + 10, H + 20, 1100),
            _clip(2, "C", H + 20, H + 30, 2100),
        ],
    )
    novo = CutIR(
        "novo.edl",
        RATE_25,
        H,
        [
            _clip(0, "C", H, H + 10, 2100),
            _clip(1, "A", H + 10, H + 20, 100),
        ],
    )
    return antigo, novo


def _marcadores_rc(path: Path) -> list[tuple[str, str]]:
    """Marcadores da copia aplicada: (nome, anotacao)."""
    saida: list[tuple[str, str]] = []
    for loc in load_als(path).findall(".//LiveSet/Locators/Locators/Locator"):
        saida.append((get_value(loc, "Name") or "", get_value(loc, "Annotation") or ""))
    return saida


def _remocoes(path: Path) -> list[tuple[str, str]]:
    """Marcadores 'RC delete' da copia aplicada: (nome, anotacao)."""
    return [(nome, anotacao) for nome, anotacao in _marcadores_rc(path) if "delete" in nome]


def _nome_das_tracks(path: Path) -> list[str]:
    """Nomes das AudioTracks da copia aplicada, na ordem do XML."""
    return [
        get_value(track, TRACK_FIELDS["name"]) or ""
        for track in load_als(path).findall("./LiveSet/Tracks/AudioTrack")
    ]


def _arredondado(clips: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Clips com as posicoes arredondadas: a grade de ticks do Live tem resto."""
    return [(round(inicio, 4), round(fim, 4)) for inicio, fim in clips]


def _aplica(tmp_path: Path, nome: str, clip: tuple[int, int]) -> Path:
    """Aplica a reconformacao dos cortes da revisao no set de um clip so."""
    antigo, novo = _cortes()
    set_path = _set_do_fuzz(tmp_path, _wav(tmp_path), [_track_spec(0, "T", clip)], nome)
    cl = diff_cuts(antigo, novo, Report())
    assert cl is not None
    res = apply_reconform(set_path, cl, Report(strict=False))
    assert res.written
    assert res.output is not None
    resultado = verify_reconform(antigo, novo, set_path, res.output, Report(strict=False))
    assert resultado.ok, resultado.problems
    return res.output


def test_cauda_sem_material_do_set_nao_marca_remocao(tmp_path: Path) -> None:
    """Set sem material depois do ultimo evento: a cauda nao vira 'RC delete'."""
    saida = _aplica(tmp_path, "sem.als", (2, 8))
    remocoes = _remocoes(saida)
    # So a remocao de B vira marcador: a cauda nao tem material do set.
    assert [nome for nome, _ in remocoes] == ["RC delete 01:00:00:10"]
    assert [anotacao for _, anotacao in remocoes] == ["deleted 10 frames"]
    assert not any("10000000" in texto for _, texto in _marcadores_rc(saida))


def test_cauda_com_material_marca_o_material_real(tmp_path: Path) -> None:
    """Set com material depois do ultimo evento: o marcador mede esse material."""
    saida = _aplica(tmp_path, "com.als", (12, 40))
    remocoes = _remocoes(saida)
    # A remocao de B e a cauda: o alcance da cauda e o do material do set.
    assert sorted(remocoes) == [
        ("RC delete 01:00:00:10", "deleted 10 frames"),
        ("RC delete 01:00:01:05", "deleted 10 frames"),
    ]
    assert not any("10000000" in texto for _, texto in _marcadores_rc(saida))


def test_pecas_da_cauda_vao_para_a_mesma_track_saiu(tmp_path: Path) -> None:
    """A peca da cauda e a de B nao colidem: uma track [saiu] so."""
    saida = _aplica(tmp_path, "um.als", (12, 40))
    assert _nome_das_tracks(saida) == ["T", "T [saiu]"]
    faixas = _faixas(saida)
    # A peca da cauda ocupa a emenda da cauda, e a de B a emenda de B.
    assert _arredondado(faixas["T [saiu]"]) == [(0.4, 0.8), (0.88, 1.2)]
    assert _arredondado(faixas["T"]) == [(0.0, 0.4)]


def _cortes_aleatorios(rng: random.Random) -> tuple[list, list, int]:
    """Cortes aleatorios: planos com lacunas no antigo e planos recortados no novo."""
    shots = []
    pos = 0
    src = 100
    for i in range(rng.randint(2, 6)):
        if rng.random() < 0.3:
            d = rng.randint(2, 12)
            shots.append(("gap", pos, pos + d, None, None))
            pos += d
        d = rng.randint(6, 20)
        shots.append(("clip", pos, pos + d, f"S{i}", src))
        pos += d
        src += 1000
    if rng.random() < 0.3:
        d = rng.randint(2, 10)
        shots.append(("gap", pos, pos + d, None, None))
        pos += d
    antigo = []
    for k, (kind, a, b, s, si) in enumerate(shots):
        if kind == "clip":
            antigo.append(_clip(k, s, H + a, H + b, si))
        else:
            antigo.append(_lacuna(k, H + a, H + b))
    clips = [x for x in shots if x[0] == "clip"]
    kept = [c for c in clips if rng.random() > 0.3] or clips[:1]
    items = []
    for _kind, a, b, s, si in kept:
        duracao = b - a
        if duracao >= 10 and rng.random() < 0.3:
            meio = rng.randint(4, duracao - 4)
            items.append((s, si, meio))
            items.append((s, si + meio, duracao - meio))
        else:
            ti = rng.randint(0, 2) if rng.random() < 0.3 else 0
            to = rng.randint(0, 2) if rng.random() < 0.3 else 0
            items.append((s, si + ti, duracao - ti - to))
    if rng.random() < 0.5:
        rng.shuffle(items)
    for j in range(rng.randint(0, 2)):
        items.insert(rng.randint(0, len(items)), (f"N{j}", 50000 + j * 1000, rng.randint(3, 10)))
    novo = []
    p = 0
    for s, si, d in items:
        if rng.random() < 0.2:
            g = rng.randint(2, 6)
            novo.append(_lacuna(len(novo), H + p, H + p + g))
            p += g
        novo.append(_clip(len(novo), s, H + p, H + p + d, si))
        p += d
    if rng.random() < 0.3:
        g = rng.randint(2, 8)
        novo.append(_lacuna(len(novo), H + p, H + p + g))
        p += g
    return antigo, novo, pos


def _tracks_aleatorios(rng: random.Random, total: int) -> tuple[list, int]:
    """Tracks do fuzz: uma ou duas, cada uma com um ou dois clips."""
    tracks = []
    esperado = 0
    for ti in range(rng.randint(1, 2)):
        pts = sorted(rng.sample(range(0, total + 20), 4))
        spec = _track_spec(ti, f"T{ti}", (pts[0], pts[1]))
        if pts[3] > pts[2] and pts[2] > pts[1]:
            segundo = _track_spec(ti, f"T{ti}", (pts[2], pts[3]))
            spec.clips.append(dataclasses.replace(segundo.clips[0], id=f"c{ti}b", event_index=1))
        esperado += sum(c.timeline_end_samples - c.timeline_start_samples for c in spec.clips)
        tracks.append(spec)
    return tracks, esperado


def test_fuzz_da_revisao_12(tmp_path: Path) -> None:
    """Fuzz do revisor: cortes aleatorios aplicados e conferidos pelo verify."""
    ruins = []
    aplicados = 0
    wav = _wav(tmp_path)
    for seed in SEMENTES:
        rng = random.Random(seed)
        antigo, novo, total = _cortes_aleatorios(rng)
        tracks, esperado = _tracks_aleatorios(rng, total)
        try:
            set_path = _set_do_fuzz(tmp_path, wav, tracks, f"s{seed}.als")
        except Exception:
            continue
        a = CutIR("o", RATE_25, H, antigo)
        b = CutIR("n", RATE_25, H, novo)
        try:
            cl = diff_cuts(a, b, Report(strict=False))
            if cl is None:
                continue
            if cl.time_map is None:
                ruins.append((seed, "time_map None"))
                continue
            res = apply_reconform(set_path, cl, Report(strict=False))
            if not res.written:
                ruins.append((seed, "not written"))
                continue
            aplicados += 1
            problemas = _problemas(_faixas(res.output), esperado / 48000)
            v = verify_reconform(a, b, set_path, res.output, Report(strict=False))
            if not v.ok:
                problemas.append(("verify", v.problems[:2]))
            if problemas:
                ruins.append((seed, problemas))
        except Exception as exc:  # noqa: BLE001 - o fuzz reporta a falha do caso
            ruins.append((seed, "EXC", repr(exc)[:200]))
    # O fuzz so vale quando ele aplica casos: a contagem nao pode ser zero.
    assert aplicados > 100, aplicados
    assert not ruins, ruins[:8]
