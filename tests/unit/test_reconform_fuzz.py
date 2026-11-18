"""Fuzz do reconform: cortes aleatorios aplicados e conferidos pelo verify.

Os geradores sao os da revisao 11: o corte antigo tem lacunas de V1 e planos
que o corte novo recorta, reordena e deixa de fora. Cada caso monta um set
real, aplica a reconformacao e confere a copia com o oraculo do verify.
"""

from __future__ import annotations

import dataclasses
import random
import wave
from pathlib import Path

import pytest

from aafton import reconform_diff
from aafton.als_paths import AUDIO_CLIP_FIELDS, TRACK_FIELDS
from aafton.als_xml import get_value, load_als
from aafton.als_write import WriteOptions, write_session
from aafton.cut_ir import CutIR
from aafton.ir import MediaAsset, SessionIR, TrackIR
from aafton.reconform_apply import apply_reconform
from aafton.reconform_diff import diff_cuts
from aafton.reconform_verify import verify_reconform
from aafton.report import Report
from tests.unit.test_reconform_revisao7 import _clip, _lacuna
from tests.unit.test_reconform_tracks import (
    HOUR,
    HOUR_F25 as H,
    RATE_25,
    _track_spec,
)

# Semeadas do fuzz: a faixa de um track so e a de duas tracks com dois clips.
SEMENTES_UNICO = range(250)
SEMENTES_DUAS = range(1000, 1300)

# O wav de silencio que os sets do fuzz compartilham.
_DURACAO_WAV = 192_000


def _shots_antigos(rng: random.Random) -> list[tuple]:
    """Planos e lacunas do corte antigo, com o TC de origem de cada plano."""
    shots = []
    pos = 0
    src = 100
    for i in range(rng.randint(3, 6)):
        if rng.random() < 0.25:
            d = rng.randint(3, 12)
            shots.append(("gap", pos, pos + d, None, None))
            pos += d
        d = rng.randint(6, 20)
        shots.append(("clip", pos, pos + d, f"S{i}", src))
        pos += d
        src += 1000
    return shots


def _eventos_antigos(shots: list[tuple]) -> list:
    """Eventos do corte antigo: os planos com TC de origem e as lacunas de V1."""
    eventos = []
    for k, (kind, a, b, s, si) in enumerate(shots):
        if kind == "clip":
            eventos.append(_clip(k, s, H + a, H + b, si))
        else:
            eventos.append(_lacuna(k, H + a, H + b))
    return eventos


def _planos_mantidos(rng: random.Random, clips: list[tuple]) -> list[tuple]:
    """Planos do corte antigo que o corte novo mantem, na ordem do corte novo."""
    kept = [c for c in clips if rng.random() > 0.3] or clips[:1]
    if rng.random() < 0.4:
        rng.shuffle(kept)
    return kept


def _eventos_novos(rng: random.Random, kept: list[tuple]) -> list:
    """Eventos do corte novo: os planos recortados, com lacunas de V1."""
    eventos = []
    pos = 0
    for kind, a, b, s, si in kept:
        ti = rng.randint(0, 2) if rng.random() < 0.3 else 0
        to = rng.randint(0, 2) if rng.random() < 0.3 else 0
        d = (b - a) - ti - to
        if rng.random() < 0.2:
            g = rng.randint(2, 6)
            eventos.append(_lacuna(len(eventos), H + pos, H + pos + g))
            pos += g
        eventos.append(_clip(len(eventos), s, H + pos, H + pos + d, si + ti))
        pos += d
    return eventos


def _gen(rng: random.Random) -> tuple[list, list, int]:
    """Corte antigo com lacunas de V1 e corte novo recortado e reordenado."""
    shots = _shots_antigos(rng)
    kept = _planos_mantidos(rng, [x for x in shots if x[0] == "clip"])
    return _eventos_antigos(shots), _eventos_novos(rng, kept), shots[-1][2]


def _wav(pasta: Path) -> Path:
    """Wav de silencio compartilhado pelos sets do fuzz."""
    wav = pasta / "source0.wav"
    with wave.open(str(wav), "wb") as handle:
        handle.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        handle.writeframes(b"\0\0" * _DURACAO_WAV)
    return wav


def _set_do_fuzz(pasta: Path, wav: Path, tracks: list[TrackIR], nome: str) -> Path:
    """Monta o set do fuzz com as tracks dadas e o wav compartilhado."""
    asset = MediaAsset(
        id="m0",
        filename=wav.name,
        channels=1,
        sample_rate=48000,
        duration_samples=_DURACAO_WAV,
        status="resolved",
        resolved_path=str(wav),
    )
    session = SessionIR(
        name="ReconformFuzz",
        sample_rate=48000,
        timecode_rate=RATE_25,
        start_timecode_samples=HOUR,
        tracks=tracks,
        media=[asset],
    )
    path = pasta / nome
    opts = WriteOptions(sample_rate=48000, bpm=60.0, live_origin_samples=HOUR, groups="none")
    write_session(session, Path("templates/live12.als"), path, opts, Report())
    return path


def _faixas(path: Path) -> dict[str, list[tuple[float, float]]]:
    """Faixa de tempo de cada clip do set aplicado, em beats."""
    out = {}
    for t in load_als(path).findall("./LiveSet/Tracks/AudioTrack"):
        out[get_value(t, TRACK_FIELDS["name"])] = sorted(
            (
                float(get_value(c, AUDIO_CLIP_FIELDS["current_start"])),
                float(get_value(c, AUDIO_CLIP_FIELDS["current_end"])),
            )
            for c in t.findall(".//AudioClip")
        )
    return out


def _problemas(faixas: dict[str, list[tuple[float, float]]], esperado: float) -> list[str]:
    """Problemas das faixas aplicadas: duracao perdida e clips sobrepostos."""
    problemas = []
    total = sum(fim - inicio for clips in faixas.values() for inicio, fim in clips)
    if abs(total - esperado) > 1e-4:
        problemas.append(f"duracao {total} em vez de {esperado}")
    for nome, clips in faixas.items():
        for anterior, seguinte in zip(clips, clips[1:]):
            if seguinte[0] < anterior[1] - 1e-6:
                problemas.append(f"clips sobrepostos em {nome}: {anterior} e {seguinte}")
    return problemas


def _confere(
    antigo: CutIR,
    novo: CutIR,
    set_path: Path,
    esperado: float,
) -> list[str]:
    """Aplica a reconformacao e confere a copia: escrita, duracao e verify."""
    cl = diff_cuts(antigo, novo, Report(strict=False))
    assert cl is not None
    report = Report(strict=False)
    res = apply_reconform(set_path, cl, report)
    if not res.written:
        return [f"a copia nao foi escrita: {report.to_json()[:300]}"]
    problemas = _problemas(_faixas(res.output), esperado)
    v = verify_reconform(antigo, novo, set_path, res.output, Report(strict=False))
    if not v.ok:
        problemas.append(f"verify: {v.problems[:2]}")
    return problemas


def test_fuzz_corte_unico(tmp_path: Path) -> None:
    """Corte antigo com lacunas e corte novo reordenado: apply, duracao e verify."""
    ruins = []
    wav = _wav(tmp_path)
    for seed in SEMENTES_UNICO:
        rng = random.Random(seed)
        old, new, total = _gen(rng)
        a = rng.randint(1, max(2, total - 4))
        b = rng.randint(a + 1, total + 3)
        set_path = _set_do_fuzz(tmp_path, wav, [_track_spec(0, "Voz", (a, b))], f"u{seed}.als")
        antigo = CutIR("o", RATE_25, H, old)
        novo = CutIR("n", RATE_25, H, new)
        problemas = _confere(antigo, novo, set_path, (b - a) / 25.0)
        if problemas:
            ruins.append((seed, (a, b), problemas))
    assert not ruins, ruins[:8]


def test_fuzz_duas_tracks(tmp_path: Path) -> None:
    """Duas tracks com dois clips cada: apply, duracao e verify."""
    ruins = []
    wav = _wav(tmp_path)
    for seed in SEMENTES_DUAS:
        rng = random.Random(seed)
        old, new, total = _gen(rng)
        tracks = []
        esperado = 0
        for ti in range(2):
            p = sorted(rng.sample(range(0, total + 2), 4))
            t = _track_spec(ti, f"T{ti}", (p[0], p[1]))
            t2 = _track_spec(ti, f"T{ti}", (p[2], p[3]))
            c2 = dataclasses.replace(t2.clips[0], id=f"c{ti}b", event_index=1)
            if p[1] > p[0] and p[3] > p[2]:
                t.clips.append(c2)
            esperado += sum((c.timeline_end_samples - c.timeline_start_samples) for c in t.clips)
            tracks.append(t)
        try:
            set_path = _set_do_fuzz(tmp_path, wav, tracks, f"d{seed}.als")
        except Exception:
            continue
        antigo = CutIR("o", RATE_25, H, old)
        novo = CutIR("n", RATE_25, H, new)
        problemas = _confere(antigo, novo, set_path, esperado / 48000)
        if problemas:
            ruins.append((seed, problemas))
    assert not ruins, ruins[:8]


def test_mapa_sobreposto_vira_erro(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Um mapa com sobreposicao vira RECONFORM_MAP_OVERLAP, sem TimeMap."""

    def _explode(*args: object, **kwargs: object) -> None:
        raise ValueError("RECONFORM_MAP_OVERLAP")

    monkeypatch.setattr(reconform_diff, "build_time_map", _explode)
    antigo = CutIR("o", RATE_25, H, [_clip(0, "A", H, H + 25, 100)])
    novo = CutIR("n", RATE_25, H, [_clip(0, "A", H, H + 25, 100)])
    report = Report(strict=False)
    cl = diff_cuts(antigo, novo, report)
    assert cl is not None
    assert cl.time_map is None
    assert [issue.code for issue in report.issues] == ["RECONFORM_MAP_OVERLAP"]
    # O apply recusa a ChangeList sem mapa de tempo.
    set_path = _set_do_fuzz(tmp_path, _wav(tmp_path), [_track_spec(0, "Voz", (0, 25))], "s.als")
    res = apply_reconform(set_path, cl, Report(strict=False))
    assert not res.written
