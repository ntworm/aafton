"""Testes de unidade da classificacao de clips por sinais (P1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from aafton.bwf_meta import BwfInfo, Ixml
from aafton.organize_classify import (
    ALL_SIGNALS,
    AMBIENCE,
    CAMERA,
    MUSIC,
    PICTURE_REF,
    PRODUCTION,
    SFX,
    VO,
    classify,
    moves,
    read_media_meta,
)

BACH_NAME = "Bach Ich ruf zu dir, BWV 639 - Daniel Oyarzabal - Daniel Oyarzabal (youtube).mp3.mpeg"
FOGOS_PLAIN = (
    "SOM DE FOGOS DE ARTIFICIO PARA DESSENSIBILIZACAO DE CAES - Shih Tzu Educado (youtube).mp3"
)
FOGOS_ACCENTED = (
    "SOM DE FOGOS DE ARTIF\u00cdCIO PARA DESSENSIBILIZA\u00c7\u00c3O"
    " DE C\u00c3ES - Shih Tzu Educado (youtube).mp3"
)

REAL_30_NAMES = (
    "043CENA35APL1TK4-043.WAV",
    "025CENA37PL2TK1-025.WAV",
    "21CENA12APL01TK05-007.WAV",
    "38CENA19APL02TK03-026.WAV",
    "6CENA25PL02PKTK03-005.WAV",
    "CENA08PL01TK03-011.WAV",
    "009CENA29BOFF-001.WAV",
    "010CENA30BOFF-001.WAV",
    "008OFFJS-003.WAV",
    "A024_A021_0519VG_001.r3d",
    "A017_A013_0514N0_001.r3d",
    "A010_A009_0505HJ_001.R3D",
    "Texto 00.m4a",
    "Texto 01.m4a",
    "Texto 05.m4a",
    BACH_NAME,
    "Gymnope\u0301die No. 5.mp3.mpeg",
    "Rodrigo Rocha - Dodecafonismo (m\u00fasica atonal).mp3",
    FOGOS_PLAIN,
    FOGOS_ACCENTED,
    "CANTO DOS PASS\u00c1ROS.aac",
    "UCJS_TrechoDocParaVersaoLONGA.mp4",
    "UCJS_TrechoDocParaVers\u00e3oLONGA.mp4",
    "UCJS_Abertura e PedroTierra_semGC.mp4",
    "gemini_generated_video_57911400.mp4",
    "HomemJovem_006.mp3",
    "WhatsApp Audio 2026-05-11 at 22.02.55.mpeg",
    "Adeus Ano Velho Feliz Ano Novo.mp3",
    "Personagem_ 02_Texto_B.mp3",
    "C\u00f3pia de 3 caixas.wav",
)


def _bwf_ixml(scene: str | None = "35A", take: str | None = "4") -> BwfInfo:
    return BwfInfo(
        path=Path("dummy.wav"),
        channels=1,
        sample_rate=48000,
        bits=24,
        frames=48000,
        ixml=Ixml(scene=scene, take=take),
    )


def _bwf_bext(originator: str = "Sound Devices MixPre-10 II") -> BwfInfo:
    return BwfInfo(
        path=Path("dummy.wav"),
        channels=1,
        sample_rate=48000,
        bits=24,
        frames=48000,
        bext={"originator": originator},
    )


@pytest.mark.parametrize(
    ("name", "with_ixml", "expected_kind", "expected_score"),
    [
        ("043CENA35APL1TK4-043.WAV", True, PRODUCTION, 2),
        ("A024_A021_0519VG_001.r3d", False, CAMERA, 2),
        ("Texto 01.m4a", False, VO, 1),
        (BACH_NAME, False, MUSIC, 1),
        (FOGOS_PLAIN, False, SFX, 1),
        (FOGOS_ACCENTED, False, SFX, 1),
        ("CANTO DOS PASS\u00c1ROS.aac", False, AMBIENCE, 1),
        ("UCJS_TrechoDocParaVersaoLONGA.mp4", False, PICTURE_REF, 1),
        ("UCJS_TrechoDocParaVers\u00e3oLONGA.mp4", False, PICTURE_REF, 1),
        ("HomemJovem_006.mp3", False, None, 0),
        ("WhatsApp Audio 2026-05-11 at 22.02.55.mpeg", False, None, 0),
        ("Adeus Ano Velho Feliz Ano Novo.mp3", False, None, 0),
        ("009CENA29BOFF-001.WAV", True, VO, 2),
        ("008OFFJS-003.WAV", True, VO, 2),
        ("009CENA29BOFF-001.WAV", False, VO, 1),
        ("008OFFJS-003.WAV", False, VO, 1),
    ],
)
def test_expected_table_and_lr_suffix(
    name: str,
    with_ixml: bool,
    expected_kind: str | None,
    expected_score: int,
) -> None:
    bwf = _bwf_ixml() if with_ixml else None
    res = classify(name, name, bwf, ALL_SIGNALS)
    res_l = classify(f"{name}_L", f"{name}_L", bwf, ALL_SIGNALS)
    assert (res.kind, res.score, res.conflict) == (expected_kind, expected_score, False)
    assert (res_l.kind, res_l.score, res_l.conflict) == (expected_kind, expected_score, False)


def test_moves_levels_for_key_examples() -> None:
    wav = "043CENA35APL1TK4-043.WAV"
    r3d = "A024_A021_0519VG_001.r3d"
    prod = classify(wav, wav, _bwf_ixml(), ALL_SIGNALS)
    cam = classify(r3d, r3d, None, ALL_SIGNALS)
    vo1 = classify("Texto 01.m4a", "Texto 01.m4a", None, ALL_SIGNALS)
    res_vo = (
        moves(vo1, "permissive"),
        moves(vo1, "balanced"),
        moves(vo1, "strict"),
    )
    assert (moves(prod, "balanced"), moves(prod, "strict")) == (True, True)
    assert (moves(cam, "balanced"), moves(cam, "strict")) == (True, True)
    assert res_vo == (True, False, False)


def test_signal_toggles_and_bext_originator() -> None:
    r3d = "A024_A021_0519VG_001.r3d"
    no_name = classify(r3d, r3d, None, frozenset({"file", "meta"}))
    no_file = classify(r3d, r3d, None, frozenset({"name", "meta"}))
    bext_only = classify("qualquer.wav", "qualquer.wav", _bwf_bext(), frozenset({"meta"}))
    assert (no_name.kind, no_name.score) == (CAMERA, 1)
    assert (no_file.kind, no_file.score) == (CAMERA, 1)
    assert (bext_only.kind, bext_only.score) == (PRODUCTION, 1)


def test_all_signals_disabled_gives_none() -> None:
    empty: frozenset[str] = frozenset()
    results = [classify(name, name, _bwf_ixml(), empty).kind for name in REAL_30_NAMES]
    assert all(kind is None for kind in results)


def test_tie_and_conflict_resolution() -> None:
    tie = classify("Bach BWV 639.r3d", "Bach BWV 639.r3d", None, ALL_SIGNALS)
    conflict = classify(
        "A024_A021_0519VG_001.r3d",
        "A024_A021_0519VG_001.r3d",
        _bwf_ixml(),
        ALL_SIGNALS,
    )
    res_conf = (
        moves(conflict, "permissive"),
        moves(conflict, "balanced"),
        moves(conflict, "strict"),
    )
    assert (tie.kind, tie.conflict, moves(tie, "permissive")) == (None, True, False)
    assert (conflict.kind, conflict.score, conflict.conflict) == (CAMERA, 2, True)
    assert res_conf == (True, False, False)


@pytest.mark.parametrize("with_meta", [False, True])
def test_monotonicity_strict_subset_balanced_subset_permissive(with_meta: bool) -> None:
    assert len(REAL_30_NAMES) == 30
    bwf = _bwf_ixml() if with_meta else None
    for name in REAL_30_NAMES:
        meta = bwf if name.lower().endswith(".wav") else None
        cls = classify(name, name, meta, ALL_SIGNALS)
        assert moves(cls, "strict") <= moves(cls, "balanced") <= moves(cls, "permissive")


def test_read_media_meta_handles_missing_mp3_and_invalid_riff(tmp_path: Path) -> None:
    missing = tmp_path / "missing.wav"
    mp3_file = tmp_path / "audio.mp3"
    mp3_file.write_bytes(b"ID3-not-a-wav")
    bad_wav = tmp_path / "not_riff.WAV"
    bad_wav.write_bytes(b"NOT_A_RIFF_FILE_AT_ALL")
    assert read_media_meta(missing) is None
    assert read_media_meta(mp3_file) is None
    assert read_media_meta(bad_wav) is None


def test_midia_com_nome_de_uuid_usa_a_extensao_do_nome_do_clip():
    uuid = "77fba76b-50f9-60aa-a203-e29b0000007c.wav"
    assert classify("Texto 01.m4a", uuid).kind == VO
    assert classify("A024_A021_0519VG_001.r3d", uuid).kind == CAMERA


def test_sufixo_ponto_l_r_sai_como_o_sufixo_sublinhado():
    assert classify("Texto 01.m4a.L").kind == classify("Texto 01.m4a_L").kind == VO
