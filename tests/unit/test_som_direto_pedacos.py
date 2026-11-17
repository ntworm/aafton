"""Testes de juncao de pedacos e separacao de alternativas no som direto."""

from __future__ import annotations

from pathlib import Path

from aafton.bwf_meta import read_bwf
from aafton.report import Report
from aafton.som_direto import Arquivo, ler_takes, read_production
from tests.support.gravadores import (
    bext,
    descricao,
    sound_devices,
    sound_devices_duplicata_letra,
    sound_devices_pedacos,
    tascam_x8_faixa,
    tascam_x8_mix,
    tascam_x8_par,
    zoom_32fp,
    zoom_pedaco,
)
from tests.unit.test_som_direto_leitor import HORA, RATE, ixml, wav

SEP = "\u00b7"


def _clips(session) -> dict[str, list[tuple[str, int, int]]]:
    return {
        t.name: [(c.name, c.timeline_start_samples, c.timeline_end_samples) for c in t.clips]
        for t in session.tracks
    }


def _codigos(report: Report) -> list[str]:
    return [issue.code for issue in report.issues]


def _zoom_arquivo(
    path: Path,
    cena: str,
    take: str,
    *,
    canais: int = 2,
    tc: int = 10 * HORA,
    frames: int = 4800,
    com_ixml: bool = True,
    family: str | None = None,
) -> Path:
    desc = descricao(zSCENE=cena, zTAKE=take, zTAPE="250506", zCIRCLED="FALSE")
    extras: list[tuple[bytes, bytes]] = [(b"bext", bext(desc, time_reference=tc))]
    if com_ixml:
        entradas = [(i, f"CH{i}") for i in range(1, canais + 1)]
        extras.append((b"iXML", ixml(cena, take, family or f"{cena}-{take}-fam", entradas)))
    return wav(path, canais, frames, extras=tuple(extras))


def test_zoom_pedacos_com_tc_exato_e_com_buraco(tmp_path):
    junto = tmp_path / "junto"
    _zoom_arquivo(junto / "Scene001_001.wav", "Scene001", "001", tc=10 * HORA, frames=4800)
    zoom_pedaco(junto, "Scene001", "001", pedaco=2, tc=10 * HORA + 4800, frames=3000)
    report = Report(strict=False)
    session = read_production(junto, report)

    assert [t.name for t in session.tracks] == [f"In 1 {SEP} CH1", f"In 2 {SEP} CH2"]
    assert _clips(session) == {
        f"In 1 {SEP} CH1": [
            ("Scene001/001", 10 * RATE, 10 * RATE + 4800),
            ("Scene001/001", 10 * RATE + 4800, 10 * RATE + 7800),
        ],
        f"In 2 {SEP} CH2": [
            ("Scene001/001", 10 * RATE, 10 * RATE + 4800),
            ("Scene001/001", 10 * RATE + 4800, 10 * RATE + 7800),
        ],
    }
    assert session.layout_blocks[0]["takes"] == 1
    assert next(i for i in report.issues if i.code == "PRODUCTION_TAKES").context["takes"] == 1
    assert "PRODUCTION_PIECES_KEPT" not in _codigos(report)

    buraco = tmp_path / "buraco"
    _zoom_arquivo(buraco / "Scene001_001.wav", "Scene001", "001", tc=10 * HORA, frames=4800)
    zoom_pedaco(buraco, "Scene001", "001", pedaco=2, tc=10 * HORA + 6000, frames=3000)
    report_buraco = Report(strict=False)
    session_buraco = read_production(buraco, report_buraco)

    assert [t.name for t in session_buraco.tracks] == [f"In 1 {SEP} CH1", f"In 2 {SEP} CH2"]
    assert _clips(session_buraco) == {
        f"In 1 {SEP} CH1": [
            ("Scene001/001", 10 * RATE, 10 * RATE + 4800),
            ("Scene001/001", 10 * RATE + 6000, 10 * RATE + 9000),
        ],
        f"In 2 {SEP} CH2": [
            ("Scene001/001", 10 * RATE, 10 * RATE + 4800),
            ("Scene001/001", 10 * RATE + 6000, 10 * RATE + 9000),
        ],
    }
    assert session_buraco.layout_blocks[0]["takes"] == 2
    aviso = next(i for i in report_buraco.issues if i.code == "PRODUCTION_PIECES_KEPT")
    assert aviso.severity == "info"
    assert aviso.context == {"count": 1}


def test_zoom_alternativas_32fp_e_tl_r_saem_com_principal(tmp_path):
    pasta = tmp_path / "zoom_alt"
    _zoom_arquivo(pasta / "Scene001_001.wav", "Scene001", "001", canais=1, tc=10 * HORA)
    zoom_32fp(pasta, "Scene001", "001", canais=2, tc=10 * HORA)
    _zoom_arquivo(pasta / "Scene001_001_TL_R.wav", "Scene001", "001", canais=2, tc=10 * HORA)
    report = Report(strict=False)
    session = read_production(pasta, report)

    assert [t.name for t in session.tracks] == [f"In 1 {SEP} CH1"]
    assert _clips(session) == {
        f"In 1 {SEP} CH1": [("Scene001/001", 10 * RATE, 10 * RATE + 4800)],
    }
    assert session.layout_blocks[0]["takes"] == 1
    nota = next(i for i in report.issues if i.code == "PRODUCTION_ALTERNATES")
    assert nota.severity == "info"
    assert nota.context == {"count": 2}


def test_tascam_alternativas_saem_e_mix_sozinho_fica(tmp_path):
    pasta = tmp_path / "tascam"
    tascam_x8_faixa(pasta, num="0001", canal=1, tc=10 * HORA)
    tascam_x8_faixa(pasta, num="0001", canal=2, tc=10 * HORA)
    tascam_x8_mix(pasta, num="0001", tc=10 * HORA)
    tascam_x8_faixa(pasta, num="0001", canal=1, duplo_float=True, tc=10 * HORA)
    tascam_x8_mix(pasta, num="0005", tc=10 * HORA + 2 * RATE)
    report = Report(strict=False)
    session = read_production(pasta, report)

    assert [t.name for t in session.tracks] == ["Ch 1", "Ch 2"]
    assert _clips(session) == {
        "Ch 1": [
            ("TASCAM_0001_1", 10 * RATE, 10 * RATE + 4800),
            ("TASCAM_0005_MIX", 12 * RATE, 12 * RATE + 4800),
        ],
        "Ch 2": [
            ("TASCAM_0001_2", 10 * RATE, 10 * RATE + 4800),
            ("TASCAM_0005_MIX", 12 * RATE, 12 * RATE + 4800),
        ],
    }
    assert session.layout_blocks[0]["takes"] == 2
    nota = next(i for i in report.issues if i.code == "PRODUCTION_ALTERNATES")
    assert nota.severity == "info"
    assert nota.context == {"count": 2}

    sozinho = tmp_path / "solo"
    tascam_x8_mix(sozinho, num="0005", tc=10 * HORA)
    report_solo = Report(strict=False)
    session_solo = read_production(sozinho, report_solo)
    assert [t.name for t in session_solo.tracks] == ["Ch 1", "Ch 2"]
    assert _clips(session_solo) == {
        "Ch 1": [("TASCAM_0005_MIX", 10 * RATE, 10 * RATE + 4800)],
        "Ch 2": [("TASCAM_0005_MIX", 10 * RATE, 10 * RATE + 4800)],
    }
    assert session_solo.layout_blocks[0]["takes"] == 1
    assert "PRODUCTION_ALTERNATES" not in _codigos(report_solo)


def test_sound_devices_pedacos_ab_juntam_e_a_sem_b_fica_separado(tmp_path):
    pasta = tmp_path / "sd"
    sound_devices(pasta, cena="S01", take="01", tc=10 * HORA, frames=4800)
    sound_devices_duplicata_letra(
        pasta,
        cena="S01",
        take="01",
        letra="A",
        tc=10 * HORA + 2 * RATE,
        frames=4800,
    )
    for idx, letra in enumerate(("A", "B")):
        meta = ixml("S02", "01", "S02T01-fam", [(1, "CH1"), (2, "CH2")])
        extras = (
            (b"bext", bext("", time_reference=10 * HORA + 4 * RATE + idx * 4800)),
            (b"iXML", meta),
        )
        wav(pasta / f"S02T01{letra}.wav", 2, 4800, extras=extras)
    report = Report(strict=False)
    session = read_production(pasta, report)

    assert [t.name for t in session.tracks] == [f"In 1 {SEP} CH1", f"In 2 {SEP} CH2"]
    assert _clips(session) == {
        f"In 1 {SEP} CH1": [
            ("S01/01", 10 * RATE, 10 * RATE + 4800),
            ("S01/01", 12 * RATE, 12 * RATE + 4800),
            ("S02/01", 14 * RATE, 14 * RATE + 4800),
            ("S02/01", 14 * RATE + 4800, 14 * RATE + 9600),
        ],
        f"In 2 {SEP} CH2": [
            ("S01/01", 10 * RATE, 10 * RATE + 4800),
            ("S01/01", 12 * RATE, 12 * RATE + 4800),
            ("S02/01", 14 * RATE, 14 * RATE + 4800),
            ("S02/01", 14 * RATE + 4800, 14 * RATE + 9600),
        ],
    }
    assert session.layout_blocks[0]["takes"] == 3
    assert "PRODUCTION_PIECES_KEPT" not in _codigos(report)

    sem_ixml = tmp_path / "sd_bext"
    sound_devices_pedacos(sem_ixml, cena="S02", take="01", com_ixml=False, bext_s=True)
    report_bext = Report(strict=False)
    session_bext = read_production(sem_ixml, report_bext)
    assert session_bext.layout_blocks[0]["takes"] == 1
    assert "PRODUCTION_PIECES_KEPT" not in _codigos(report_bext)

    fam_diff = tmp_path / "sd_fam_diff"
    sound_devices_pedacos(fam_diff, cena="S02", take="01")
    report_diff = Report(strict=False)
    session_diff = read_production(fam_diff, report_diff)
    assert session_diff.layout_blocks[0]["takes"] == 2
    kept = next(i for i in report_diff.issues if i.code == "PRODUCTION_PIECES_KEPT")
    assert kept.context == {"count": 1}


def test_tascam_x8_pedacos_a_b_e_canal_novo_no_pedaco_2(tmp_path):
    pares = tmp_path / "x8_ab"
    tascam_x8_par(pares, num="0003", div="A", tc=10 * HORA, frames=4800)
    tascam_x8_par(pares, num="0003", div="B", tc=10 * HORA + 4800, frames=3000)
    report_pares = Report(strict=False)
    session_pares = read_production(pares, report_pares)

    assert [t.name for t in session_pares.tracks] == ["Ch 1", "Ch 2"]
    assert _clips(session_pares) == {
        "Ch 1": [
            ("TASCAM_0003_A_1-2", 10 * RATE, 10 * RATE + 4800),
            ("TASCAM_0003_B_1-2", 10 * RATE + 4800, 10 * RATE + 7800),
        ],
        "Ch 2": [
            ("TASCAM_0003_A_1-2", 10 * RATE, 10 * RATE + 4800),
            ("TASCAM_0003_B_1-2", 10 * RATE + 4800, 10 * RATE + 7800),
        ],
    }
    assert session_pares.layout_blocks[0]["takes"] == 1
    assert "PRODUCTION_PIECES_KEPT" not in _codigos(report_pares)

    canal_novo = tmp_path / "x8_ch2_p2"
    tascam_x8_faixa(canal_novo, num="0003", canal=1, div="A", tc=10 * HORA, frames=4800)
    tascam_x8_par(canal_novo, num="0003", par=(1, 2), div="B", tc=10 * HORA + 4800, frames=4800)
    tascam_x8_faixa(canal_novo, num="0004", canal=2, tc=10 * HORA + 1200, frames=2400)
    report_novo = Report(strict=False)
    session_novo = read_production(canal_novo, report_novo)

    assert [t.name for t in session_novo.tracks] == ["Ch 1", "Ch 2"]
    assert _clips(session_novo) == {
        "Ch 1": [
            ("TASCAM_0003_A_1", 10 * RATE, 10 * RATE + 4800),
            ("TASCAM_0003_B_1-2", 10 * RATE + 4800, 10 * RATE + 9600),
        ],
        "Ch 2": [
            ("TASCAM_0004_2", 10 * RATE + 1200, 10 * RATE + 3600),
            ("TASCAM_0003_B_1-2", 10 * RATE + 4800, 10 * RATE + 9600),
        ],
    }
    assert session_novo.layout_blocks[0]["takes"] == 2
    assert "PRODUCTION_OVERLAP" not in _codigos(report_novo)
    assert "PRODUCTION_PIECES_KEPT" not in _codigos(report_novo)


def test_modelo_arquivo_offset_ordem_pedacos_e_sem_timereference(tmp_path):
    p1 = _zoom_arquivo(tmp_path / "tres" / "Scene001_001.wav", "Scene001", "001", tc=10 * HORA)
    arq = Arquivo(p1, read_bwf(p1), p1.parent)
    assert arq.offset == 0

    _zoom_arquivo(
        tmp_path / "tres" / "@Scene001_001_0002.wav",
        "Scene001",
        "001",
        tc=10 * HORA + 4800,
        frames=3000,
    )
    zoom_pedaco(tmp_path / "tres", "Scene001", "001", pedaco=3, tc=10 * HORA + 7800, frames=2000)
    takes, _ = ler_takes(tmp_path / "tres", Report(strict=False))
    assert len(takes) == 1
    (take,) = takes
    assert [a.path.name for a in take.arquivos] == [
        "Scene001_001.wav",
        "@Scene001_001_0002.wav",
        "Scene001_001_0003.wav",
    ]
    assert [a.offset for a in take.arquivos] == [0, 4800, 7800]
    assert take.timecode == 10 * HORA
    assert take.name_key == "scene001_001.wav"
    assert take.frames == 9800

    sem_tc = tmp_path / "sem_tc" / "Scene001_001.TAKE"
    desc_sem_tc = descricao(zSCENE="Scene001", zTAKE="001")
    wav(
        sem_tc / "Scene001_001.wav",
        1,
        4800,
        extras=((b"bext", bext(desc_sem_tc, time_reference=10 * HORA)),),
    )
    wav(sem_tc / "Scene001_001_0002.wav", 1, 3000)
    report_sem_tc = Report(strict=False)
    session_sem_tc = read_production(tmp_path / "sem_tc", report_sem_tc)
    assert len(session_sem_tc.layout_blocks) == 1
    assert session_sem_tc.layout_blocks[0]["takes"] == 2
    kept = next(i for i in report_sem_tc.issues if i.code == "PRODUCTION_PIECES_KEPT")
    assert kept.context == {"count": 1}


def test_alternativa_em_outra_diaria_nao_sai(tmp_path):
    d1 = tmp_path / "2025-05-05"
    d2 = tmp_path / "2025-05-06"
    tascam_x8_mix(d1, num="0001", tc=10 * HORA, data="2025-05-05")
    tascam_x8_faixa(d2, num="0001", canal=1, tc=10 * HORA, data="2025-05-06")
    tascam_x8_mix(d2, num="0001", tc=10 * HORA, data="2025-05-06")
    report = Report(strict=False)
    session = read_production(tmp_path, report)

    assert [b["takes"] for b in session.layout_blocks] == [1, 1]
    nota = next(i for i in report.issues if i.code == "PRODUCTION_ALTERNATES")
    assert nota.context == {"count": 1}
