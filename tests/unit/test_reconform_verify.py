"""Verify independente do reconform: aprova o apply certo e pega cada desvio."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from aafton.als_paths import AUDIO_CLIP_FIELDS, TRACK_FIELDS
from aafton.als_read_clips import track_name
from aafton.als_write import write_session
from aafton.als_write_base import to_beats
from aafton.als_xml import format_value, get_value, load_als, save_als, set_value
from aafton.cut_ir import CutEvent, CutIR
from aafton.cut_read import read_cut
from aafton.reconform_apply import apply_reconform
from aafton.reconform_diff import diff_cuts
from aafton.reconform_verify import verify_reconform
from aafton.report import Report
from tests.unit.test_reconform_apply import (
    _NEW_EDL_25,
    _NEW_TRIM_EDL,
    _OLD_EDL_25,
    _OLD_TRIM_EDL,
    RATE_25,
    SEC,
    _asset,
    _build_spanning_set,
    _clip,
    _options,
    _session,
    _sha256,
    _track as _track_ir,
    _write_wav,
)
from tests.unit.test_review_robustness import HOUR

CLIPS = "DeviceChain/MainSequencer/Sample/ArrangerAutomation/Events/AudioClip"
# Amostra em que a grade de ticks do Live joga o clip uma amostra para frente
# (o CONTROLE de tests/fixtures/als/live_clip_clock_profile.json).
CONTROLE = 48007

# Cortes com o material antigo terminando antes do fim do set: o clip de cauda
# do arranjo fica depois do ultimo evento do corte.
_OLD_TAIL_EDL = "\n".join(
    [
        "TITLE: OLDTAIL",
        "FCM: NON-DROP FRAME",
        "",
        "001  R001     V     C        01:00:00:00 01:00:02:00 01:00:00:00 01:00:02:00",
        "002  R002     V     C        02:00:00:00 02:00:02:00 01:00:02:00 01:00:04:00",
        "003  R003     V     C        03:00:00:00 03:00:02:00 01:00:04:00 01:00:06:00",
        "",
    ]
)

_NEW_TAIL_EDL = "\n".join(
    [
        "TITLE: NEWTAIL",
        "FCM: NON-DROP FRAME",
        "",
        "001  R001     V     C        01:00:00:00 01:00:02:00 01:00:01:00 01:00:03:00",
        "002  R002     V     C        02:00:00:00 02:00:02:00 01:00:03:00 01:00:05:00",
        "003  R003     V     C        03:00:00:00 03:00:02:00 01:00:05:00 01:00:07:00",
        "",
    ]
)


def _aplicado_com_edl(tmp_path: Path, old_text: str, new_text: str):
    """Aplica um par de EDLs sobre o set de exemplo e devolve o contexto do verify."""
    pytest.importorskip("opentimelineio")
    old_p, new_p = tmp_path / "old.edl", tmp_path / "new.edl"
    old_p.write_text(old_text, encoding="utf-8")
    new_p.write_text(new_text, encoding="utf-8")
    old = read_cut(old_p, Report(), frame_rate=RATE_25)
    new = read_cut(new_p, Report(), frame_rate=RATE_25)
    set_path = _build_spanning_set(tmp_path)
    sha = _sha256(set_path)
    res = apply_reconform(set_path, diff_cuts(old, new, Report()), Report())
    assert res.written
    return old, new, set_path, res.output, sha


def _aplicado(tmp_path: Path):
    return _aplicado_com_edl(tmp_path, _OLD_EDL_25, _NEW_EDL_25)


def _track(root, nome):
    return next(n for n in root.findall("./LiveSet/Tracks/AudioTrack") if track_name(n) == nome)


def _mexer(caminho: Path, mudanca) -> Path:
    """Copia a saida aplicada com uma alteracao no XML."""
    destino = caminho.with_name("mexido.als")
    shutil.copyfile(caminho.with_suffix(".aafton.json"), destino.with_suffix(".aafton.json"))
    root = load_als(caminho)
    mudanca(root)
    save_als(root, destino)
    return destino


def _codigos(resultado) -> set[str]:
    return {p["code"] for p in resultado.problems}


def test_apply_certo_passa(tmp_path: Path) -> None:
    old, new, origem, aplicado, sha = _aplicado(tmp_path)
    report = Report(strict=False)
    resultado = verify_reconform(old, new, origem, aplicado, report, source_sha256=sha)
    assert resultado.ok, resultado.problems
    assert resultado.checked_clips == 3
    assert not report.incomplete


def _desloca_um_sample(root) -> None:
    clip = _track(root, "Track 0").findall(CLIPS)[1]
    inicio = float(get_value(clip, AUDIO_CLIP_FIELDS["current_start"]))
    novo = inicio + 1.0 / 48000
    clip.set("Time", format_value(novo))
    set_value(clip, AUDIO_CLIP_FIELDS["current_start"], novo)


def _loop_fora(root) -> None:
    clip = _track(root, "Track 0").findall(CLIPS)[1]
    loop = float(get_value(clip, AUDIO_CLIP_FIELDS["loop_start"]))
    set_value(clip, AUDIO_CLIP_FIELDS["loop_start"], loop + 10 / 48000)


def _desmuta(root) -> None:
    set_value(_track(root, "Track 0 [saiu]"), TRACK_FIELDS["mute"], "true")


def _fade_no_meio(root) -> None:
    clip = _track(root, "Track 0").findall(CLIPS)[1]
    set_value(clip, AUDIO_CLIP_FIELDS["fade_in_length"], 0.25)


@pytest.mark.parametrize(
    ("mudanca", "codigo"),
    [
        (_desloca_um_sample, "RECONFORM_VERIFY_POSITION"),
        (_loop_fora, "RECONFORM_VERIFY_POSITION"),
        (_desmuta, "RECONFORM_VERIFY_SAIU_UNMUTED"),
        (_fade_no_meio, "RECONFORM_VERIFY_FADE"),
    ],
)
def test_desvio_e_pego(tmp_path: Path, mudanca, codigo: str) -> None:
    old, new, origem, aplicado, _sha = _aplicado(tmp_path)
    mexido = _mexer(aplicado, mudanca)
    report = Report(strict=False)
    resultado = verify_reconform(old, new, origem, mexido, report)
    assert not resultado.ok
    assert codigo in _codigos(resultado)
    assert any(i.code == "RECONFORM_VERIFY_FAILED" for i in report.issues)


def test_origem_alterada_e_pega(tmp_path: Path) -> None:
    old, new, origem, aplicado, _sha = _aplicado(tmp_path)
    resultado = verify_reconform(
        old, new, origem, aplicado, Report(strict=False), source_sha256="0"
    )
    assert "RECONFORM_VERIFY_SOURCE_CHANGED" in _codigos(resultado)


def _set_com_clip_em(tmp_path: Path, inicio_rel: int) -> Path:
    """Set com um AudioClip na posicao relativa informada."""
    wav = tmp_path / "source.wav"
    _write_wav(wav, 1, 10 * SEC)
    asset = _asset("m0", wav.name, 1, SEC, 10 * SEC, "resolved", str(wav), str(wav))
    clip = _clip("c0", "t0", 0, 0, "Clip 0", "m0", HOUR + inicio_rel, HOUR + inicio_rel + SEC, 0)
    track = _track_ir("t0", 0, [101], "Track 0", "audio", 1, clips=[clip])
    sess = _session("Verify", SEC, RATE_25, HOUR, tracks=[track], media=[asset])
    path = tmp_path / "verify.als"
    write_session(sess, Path("templates/live12.als"), path, _options(SEC, 60, HOUR), Report())
    return path


def _inicio_plano(root) -> None:
    """Grava o inicio e o fim do clip com to_beats, fora da grade do relogio."""
    clip = _track(root, "Track 0").findall(CLIPS)[0]
    inicio = to_beats(HOUR + CONTROLE, HOUR, SEC, 60.0)
    fim = to_beats(HOUR + CONTROLE + SEC, HOUR, SEC, 60.0)
    clip.set("Time", format_value(inicio))
    set_value(clip, AUDIO_CLIP_FIELDS["current_start"], inicio)
    set_value(clip, AUDIO_CLIP_FIELDS["current_end"], fim)


def test_posicao_fora_da_grade_do_live_e_pega(tmp_path: Path) -> None:
    """Clip gravado com to_beats joga a posicao uma amostra e o verify pega."""
    set_path = _set_com_clip_em(tmp_path, CONTROLE)
    cut = CutIR(
        "cut.edl",
        RATE_25,
        90000,
        [CutEvent(0, "clip", "A", None, 100, 300, 90000, 90200)],
    )
    # A posicao gravada na grade do Live e a que o verify espera.
    certo = verify_reconform(cut, cut, set_path, set_path, Report(strict=False))
    assert certo.ok, certo.problems

    mexido = _mexer(set_path, _inicio_plano)
    resultado = verify_reconform(cut, cut, set_path, mexido, Report(strict=False))
    assert not resultado.ok
    assert "RECONFORM_VERIFY_POSITION" in _codigos(resultado)


def _inicio_do_clip(clip) -> float:
    """Inicio do clip em beats, como o XML do Live guarda."""
    return float(get_value(clip, AUDIO_CLIP_FIELDS["current_start"]))


def _clip_por_inicio(root, nome: str, ultimo: bool):
    """Clip da track pedida com o inicio mais novo, ou o mais antigo."""
    clips = _track(root, nome).findall(CLIPS)
    return max(clips, key=_inicio_do_clip) if ultimo else min(clips, key=_inicio_do_clip)


def _move_clip(clip, amostras: int) -> None:
    """Move um clip do arranjo por um numero de amostras, sem mudar o tamanho."""
    delta = amostras / SEC
    inicio = float(get_value(clip, AUDIO_CLIP_FIELDS["current_start"]))
    fim = float(get_value(clip, AUDIO_CLIP_FIELDS["current_end"]))
    clip.set("Time", format_value(inicio + delta))
    set_value(clip, AUDIO_CLIP_FIELDS["current_start"], inicio + delta)
    set_value(clip, AUDIO_CLIP_FIELDS["current_end"], fim + delta)


def test_apply_com_trim_passa_no_verify(tmp_path: Path) -> None:
    """Corte novo com trim: o verify divide o clip onde o apply divide."""
    old, new, origem, aplicado, sha = _aplicado_com_edl(tmp_path, _OLD_TRIM_EDL, _NEW_TRIM_EDL)
    resultado = verify_reconform(
        old, new, origem, aplicado, Report(strict=False), source_sha256=sha
    )
    assert resultado.ok, resultado.problems


def _move_a_peca_que_ficou(root) -> None:
    """Move a peca que sobrou do trim por uma amostra."""
    _move_clip(_clip_por_inicio(root, "Track 0", ultimo=False), 1)


def test_peca_que_ficou_movida_uma_amostra_e_pega(tmp_path: Path) -> None:
    """O verify pega a peca que sobrou do trim deslocada de uma amostra."""
    old, new, origem, aplicado, _sha = _aplicado_com_edl(tmp_path, _OLD_TRIM_EDL, _NEW_TRIM_EDL)
    mexido = _mexer(aplicado, _move_a_peca_que_ficou)
    resultado = verify_reconform(old, new, origem, mexido, Report(strict=False))
    assert not resultado.ok
    assert "RECONFORM_VERIFY_POSITION" in _codigos(resultado)


def test_caluda_do_corte_e_conferida(tmp_path: Path) -> None:
    """Clip depois do ultimo evento do corte entra na conta da cauda."""
    old, new, origem, aplicado, sha = _aplicado_com_edl(tmp_path, _OLD_TAIL_EDL, _NEW_TAIL_EDL)
    resultado = verify_reconform(
        old, new, origem, aplicado, Report(strict=False), source_sha256=sha
    )
    assert resultado.ok, resultado.problems


def _move_a_cauda(root) -> None:
    """Move o clip da cauda por cem amostras."""
    _move_clip(_clip_por_inicio(root, "Track 0", ultimo=True), 100)


def test_caluda_movida_cem_amostras_e_pega(tmp_path: Path) -> None:
    """O verify pega o clip da cauda deslocado de cem amostras."""
    old, new, origem, aplicado, _sha = _aplicado_com_edl(tmp_path, _OLD_TAIL_EDL, _NEW_TAIL_EDL)
    mexido = _mexer(aplicado, _move_a_cauda)
    resultado = verify_reconform(old, new, origem, mexido, Report(strict=False))
    assert not resultado.ok
    assert "RECONFORM_VERIFY_POSITION" in _codigos(resultado)


def _cortes_com_leader() -> tuple[CutIR, CutIR]:
    """Cortes que comecam depois do inicio do set: o material antes e leader."""
    antigo = CutIR(
        "old.edl",
        RATE_25,
        90100,
        [
            CutEvent(0, "clip", "A", None, 90000, 90050, 90100, 90150),
            CutEvent(1, "clip", "B", None, 180000, 180050, 90150, 90200),
        ],
    )
    novo = CutIR(
        "new.edl",
        RATE_25,
        90125,
        [
            CutEvent(0, "clip", "A", None, 90000, 90050, 90125, 90175),
            CutEvent(1, "clip", "B", None, 180000, 180050, 90175, 90225),
        ],
    )
    return antigo, novo


def test_leader_do_corte_e_conferido(tmp_path: Path) -> None:
    """Material antes do primeiro evento do corte entra na conta do leader."""
    pytest.importorskip("opentimelineio")
    antigo, novo = _cortes_com_leader()
    set_path = _build_spanning_set(tmp_path)
    sha = _sha256(set_path)
    res = apply_reconform(set_path, diff_cuts(antigo, novo, Report()), Report())
    assert res.written
    resultado = verify_reconform(
        antigo, novo, set_path, res.output, Report(strict=False), source_sha256=sha
    )
    assert resultado.ok, resultado.problems
