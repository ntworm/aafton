"""Integracao dos dialetos de gravador no leitor de som direto (read_production)."""

from __future__ import annotations

import pytest

from aafton.bwf_meta import read_bwf
from aafton.report import Report
from aafton.som_direto import Arquivo, read_production
from aafton.timecode import TimecodeRate
from tests.support.gravadores import (
    bext,
    descricao,
    sound_devices,
    sound_devices_duplicata_letra,
    sound_devices_duplicata_mais,
    sound_devices_pedacos,
    tascam_dr40x,
    tascam_dr701d_par,
    tascam_dr701d_poly,
    tascam_x8_faixa,
    tascam_x8_mix,
    tascam_x8_par,
    zoom_poly,
    zoom_tr,
    zoom_tr_par,
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


def test_arquivo_preenche_partes_no_post_init(tmp_path):
    caminho = tascam_dr40x(tmp_path, par="12")
    arq = Arquivo(caminho, read_bwf(caminho), caminho.parent)
    assert arq.partes is not None
    assert arq.partes.dialeto == "tascam_dr40"
    assert arq.partes.canais_do_nome == (1, 2)


def test_dr40x_pares_s12_e_s34_e_mono_com_production_name_channels(tmp_path):
    pasta = tmp_path / "dr40"
    tascam_dr40x(pasta, par="12", tc=10 * HORA, frames=4800)
    tascam_dr40x(pasta, par="34", tc=10 * HORA, frames=4800)
    report = Report(strict=False)
    session = read_production(pasta, report)

    assert [t.name for t in session.tracks] == ["Ch 1", "Ch 2", "Ch 3", "Ch 4"]
    assert _clips(session) == {
        "Ch 1": [("180113_0003S12", 10 * RATE, 10 * RATE + 4800)],
        "Ch 2": [("180113_0003S12", 10 * RATE, 10 * RATE + 4800)],
        "Ch 3": [("180113_0003S34", 10 * RATE, 10 * RATE + 4800)],
        "Ch 4": [("180113_0003S34", 10 * RATE, 10 * RATE + 4800)],
    }
    assert len(session.layout_blocks) == 1
    assert session.layout_blocks[0]["day"] == "dr40"
    assert session.layout_blocks[0]["takes"] == 1
    assert "PRODUCTION_NAME_CHANNELS" not in _codigos(report)
    dialeto = next(i for i in report.issues if i.code == "PRODUCTION_DIALECT")
    assert dialeto.context == {"dialects": {"tascam_dr40": 2}}

    mono = tmp_path / "dr40_mono"
    tascam_dr40x(mono, num="0004", par="12", canais=1, tc=10 * HORA, frames=4800)
    tascam_dr40x(mono, num="0005", par="34", canais=1, tc=10 * HORA + 2 * RATE, frames=4800)
    report_mono = Report(strict=False)
    session_mono = read_production(mono, report_mono)

    assert [t.name for t in session_mono.tracks] == ["Ch 1"]
    assert _clips(session_mono) == {
        "Ch 1": [
            ("180113_0004S12", 10 * RATE, 10 * RATE + 4800),
            ("180113_0005S34", 12 * RATE, 12 * RATE + 4800),
        ],
    }
    assert session_mono.layout_blocks[0]["takes"] == 2
    aviso = next(i for i in report_mono.issues if i.code == "PRODUCTION_NAME_CHANNELS")
    assert aviso.severity == "warning"
    assert aviso.context == {"count": 2}


def test_tascam_x8_e_dr701d_agrupamento_canais_e_alternativas_separadas(tmp_path):
    x8 = tmp_path / "x8"
    tascam_x8_faixa(x8, num="0001", canal=1, tc=10 * HORA)
    tascam_x8_faixa(x8, num="0001", canal=2, tc=10 * HORA)
    tascam_x8_faixa(x8, num="0002", canal=1, tc=10 * HORA + 3 * RATE)
    report = Report(strict=False)
    session = read_production(x8, report)

    assert [t.name for t in session.tracks] == ["Ch 1", "Ch 2"]
    assert _clips(session) == {
        "Ch 1": [
            ("TASCAM_0001_1", 10 * RATE, 10 * RATE + 4800),
            ("TASCAM_0002_1", 13 * RATE, 13 * RATE + 4800),
        ],
        "Ch 2": [("TASCAM_0001_2", 10 * RATE, 10 * RATE + 4800)],
    }
    assert session.layout_blocks[0]["day"] == "x8"
    assert session.layout_blocks[0]["takes"] == 2
    assert next(i for i in report.issues if i.code == "PRODUCTION_DIALECT").context == {
        "dialects": {"tascam_x8": 3},
    }

    dr701 = tmp_path / "dr701"
    tascam_dr701d_par(dr701, base="141001", num="0001", par=(1, 2), tc=10 * HORA)
    tascam_dr701d_poly(dr701, num="0002", canais=6, tc=10 * HORA + 2 * RATE)
    report_dr = Report(strict=False)
    session_dr = read_production(dr701, report_dr)

    assert [t.name for t in session_dr.tracks] == [f"Ch {i}" for i in range(1, 7)]
    clips_dr = _clips(session_dr)
    assert clips_dr["Ch 1"] == [
        ("141001_0001_12", 10 * RATE, 10 * RATE + 4800),
        ("TASCAM_0002_6CH", 12 * RATE, 12 * RATE + 4800),
    ]
    assert clips_dr["Ch 6"] == [("TASCAM_0002_6CH", 12 * RATE, 12 * RATE + 4800)]
    assert session_dr.layout_blocks[0]["takes"] == 2

    sep = tmp_path / "x8_sep"
    tascam_x8_faixa(sep, num="0001", canal=1, tc=10 * HORA)
    tascam_x8_faixa(sep, num="0001", canal=1, duplo_float=True, tc=10 * HORA)
    tascam_x8_mix(sep, num="0001", tc=10 * HORA)
    tascam_x8_par(sep, num="0003", div="A", tc=10 * HORA + 2 * RATE)
    tascam_x8_par(sep, num="0003", div="B", tc=10 * HORA + 2 * RATE + 4800)
    session_sep = read_production(sep, Report(strict=False))
    # Float e mix saem dos takes e os pedacos A/B seguidos viram um take (passo 7).
    assert session_sep.layout_blocks[0]["takes"] == 2


def test_zoom_pastas_take_juntam_com_mesmo_tape_e_meia_noite(tmp_path):
    grav = tmp_path / "gravacao"
    zoom_tr(grav, cena="Scene001", take="003", tr=1, tape="R01", data="2025-05-06", tc=10 * HORA)
    zoom_tr(grav, cena="Scene001", take="003", tr=2, tape="R01", data="2025-05-06", tc=10 * HORA)
    zoom_tr(
        grav,
        cena="Scene001",
        take="004",
        tr=1,
        tape="R01",
        data="2025-05-06",
        tc=10 * HORA + 3 * RATE,
    )
    report = Report(strict=False)
    session = read_production(grav, report)

    assert len(session.layout_blocks) == 1
    assert session.layout_blocks[0]["day"] == "gravacao"
    assert session.layout_blocks[0]["date"] == "2025-05-06"
    assert session.layout_blocks[0]["takes"] == 2
    assert {asset.raw["day"] for asset in session.media} == {"gravacao"}
    assert [t.name for t in session.tracks] == [f"In 1 {SEP} TR1", f"In 2 {SEP} TR2"]
    assert _clips(session) == {
        f"In 1 {SEP} TR1": [
            ("Scene001/003", 10 * RATE, 10 * RATE + 4800),
            ("Scene001/004", 13 * RATE, 13 * RATE + 4800),
        ],
        f"In 2 {SEP} TR2": [("Scene001/003", 10 * RATE, 10 * RATE + 4800)],
    }
    assert "PRODUCTION_TAKE_FOLDERS_KEPT" not in _codigos(report)
    assert next(i for i in report.issues if i.code == "PRODUCTION_DIALECT").context == {
        "dialects": {"zoom": 3},
    }

    noite = tmp_path / "noite"
    zoom_tr(noite, cena="Scene001", take="001", tr=1, tape="R01", data="2025-05-05", tc=23 * HORA)
    zoom_tr(noite, cena="Scene001", take="002", tr=1, tape="R01", data="2025-05-06", tc=HORA // 2)
    report_noite = Report(strict=False)
    session_noite = read_production(noite, report_noite)
    assert len(session_noite.layout_blocks) == 1
    assert session_noite.layout_blocks[0]["day"] == "noite"
    assert session_noite.layout_blocks[0]["takes"] == 2
    assert "PRODUCTION_TAKE_FOLDERS_KEPT" not in _codigos(report_noite)
    assert "PRODUCTION_MIDNIGHT" in _codigos(report_noite)


@pytest.mark.parametrize(
    ("caso", "tapes", "datas", "palavra_causa"),
    [
        ("tape_diferente", ("R01", "R02"), ("2025-05-06", "2025-05-06"), "TAPE"),
        ("misto_data", ("R01", "R01"), ("2025-05-06", ""), "OriginationDate"),
        ("sem_data", ("R01", "R01"), ("", ""), "OriginationDate"),
        ("dois_dias", ("R01", "R01"), ("2025-05-05", "2025-05-07"), "1 day"),
    ],
)
def test_zoom_pastas_take_ficam_separadas_com_aviso(tmp_path, caso, tapes, datas, palavra_causa):
    grav = tmp_path / caso
    zoom_tr(grav, cena="Scene001", take="003", tr=1, tape=tapes[0], data=datas[0], tc=10 * HORA)
    zoom_tr(
        grav,
        cena="Scene001",
        take="004",
        tr=1,
        tape=tapes[1],
        data=datas[1],
        tc=10 * HORA + 2 * RATE,
    )
    report = Report(strict=False)
    session = read_production(grav, report)

    assert [b["day"] for b in session.layout_blocks] == [
        "Scene001_003.TAKE",
        "Scene001_004.TAKE",
    ]
    assert {asset.raw["day"] for asset in session.media} == {
        "Scene001_003.TAKE",
        "Scene001_004.TAKE",
    }
    inicio2 = -(-(10 * RATE + 4800 + 10 * RATE) // RATE) * RATE
    assert _clips(session) == {
        f"In 1 {SEP} TR1": [
            ("Scene001/003", 10 * RATE, 10 * RATE + 4800),
            ("Scene001/004", inicio2 + 10 * RATE, inicio2 + 10 * RATE + 4800),
        ],
    }
    aviso = next(i for i in report.issues if i.code == "PRODUCTION_TAKE_FOLDERS_KEPT")
    assert aviso.severity == "warning"
    assert palavra_causa in aviso.message
    assert palavra_causa in aviso.context["cause"]


def test_zoom_entrada_direta_na_pasta_take_nao_sobe_para_o_pai(tmp_path):
    grav = tmp_path / "grav"
    zoom_tr(grav, cena="Scene001", take="003", tr=1, tape="R01", data="2025-05-06", tc=10 * HORA)
    report = Report(strict=False)
    session = read_production(grav / "Scene001_003.TAKE", report)

    assert [b["day"] for b in session.layout_blocks] == ["Scene001_003.TAKE"]
    assert {asset.raw["day"] for asset in session.media} == {"Scene001_003.TAKE"}
    aviso = next(i for i in report.issues if i.code == "PRODUCTION_TAKE_FOLDERS_KEPT")
    assert "outside" in aviso.context["cause"]


def test_bext_fallback_z_e_s_e_taxa_de_timecode_do_ixml_real(tmp_path):
    pasta_z = tmp_path / "bext_z"
    zoom_poly(pasta_z, cena="12A", take="003", canais=2, com_ixml=False, com_z_bext=True)
    wav(
        pasta_z / "Z_real.wav",
        1,
        4800,
        extras=(
            (b"bext", bext(time_reference=10 * HORA + 3 * RATE, data="2025-05-06")),
            (b"iXML", ixml("12A", "004", "fam-real", [(3, "BOOM")], rate="25/1", flag="NDF")),
        ),
    )
    report_z = Report(strict=False)
    session_z = read_production(pasta_z, report_z)

    assert session_z.timecode_rate == TimecodeRate(25, 1, False)
    assert "PRODUCTION_TC_RATE_ASSUMED" not in _codigos(report_z)
    assert [t.name for t in session_z.tracks] == [f"In 3 {SEP} BOOM", "Ch 1", "Ch 2"]
    assert _clips(session_z) == {
        f"In 3 {SEP} BOOM": [("12A/004", 13 * RATE, 13 * RATE + 4800)],
        "Ch 1": [("12A/003", 10 * RATE, 10 * RATE + 4800)],
        "Ch 2": [("12A/003", 10 * RATE, 10 * RATE + 4800)],
    }
    assert session_z.layout_blocks[0]["takes"] == 2
    fallback = next(i for i in report_z.issues if i.code == "PRODUCTION_BEXT_FALLBACK")
    assert fallback.severity == "info"
    assert fallback.context == {"count": 1}
    assert "PRODUCTION_DIALECT_UNCONFIRMED" not in _codigos(report_z)
    assert not [
        i for i in report_z.issues if i.code == "PRODUCTION_FILE_NOTE" and i.severity == "warning"
    ]

    pasta_s = tmp_path / "bext_s"
    sound_devices(pasta_s, cena="S01", take="02", com_ixml=False, bext_s=True, tc=10 * HORA)
    report_s = Report(strict=False)
    session_s = read_production(pasta_s, report_s)

    assert [t.name for t in session_s.tracks] == ["Ch 1", "Ch 2"]
    assert _clips(session_s) == {
        "Ch 1": [("S01/02", 10 * RATE, 10 * RATE + 4800)],
        "Ch 2": [("S01/02", 10 * RATE, 10 * RATE + 4800)],
    }
    assert session_s.timecode_rate == TimecodeRate(24, 1, False)
    assert "PRODUCTION_TC_RATE_ASSUMED" in _codigos(report_s)
    assert "PRODUCTION_BEXT_FALLBACK" in _codigos(report_s)
    unconf = next(i for i in report_s.issues if i.code == "PRODUCTION_DIALECT_UNCONFIRMED")
    assert unconf.severity == "warning"
    assert unconf.context == {"count": 1}
    assert not [
        i for i in report_s.issues if i.code == "PRODUCTION_FILE_NOTE" and i.severity == "warning"
    ]


def test_zoom_canais_do_nome_sem_ixml_e_track_list_real_vence(tmp_path):
    pasta = tmp_path / "zoom_canais"
    zoom_tr(pasta, cena="Scene001", take="001", tr=3, com_ixml=False, tc=10 * HORA)
    zoom_tr_par(
        pasta,
        cena="Scene001",
        take="002",
        par=(1, 2),
        com_ixml=False,
        tc=10 * HORA + 2 * RATE,
    )
    wav(
        pasta / "Scene001-003_Tr1.wav",
        2,
        4800,
        extras=(
            (
                b"bext",
                bext(
                    descricao(zSCENE="Scene001", zTAKE="003", zTAPE="250506"),
                    time_reference=10 * HORA + 4 * RATE,
                ),
            ),
            (b"iXML", ixml("Scene001", "003", "fam3", [(4, "LAP1"), (5, "LAP2")])),
        ),
    )
    report = Report(strict=False)
    session = read_production(pasta, report)

    assert [t.name for t in session.tracks] == [
        f"In 4 {SEP} LAP1",
        f"In 5 {SEP} LAP2",
        "Ch 1",
        "Ch 2",
        "Ch 3",
    ]
    assert _clips(session) == {
        f"In 4 {SEP} LAP1": [("Scene001/003", 14 * RATE, 14 * RATE + 4800)],
        f"In 5 {SEP} LAP2": [("Scene001/003", 14 * RATE, 14 * RATE + 4800)],
        "Ch 1": [("Scene001/002", 12 * RATE, 12 * RATE + 4800)],
        "Ch 2": [("Scene001/002", 12 * RATE, 12 * RATE + 4800)],
        "Ch 3": [("Scene001/001", 10 * RATE, 10 * RATE + 4800)],
    }
    assert "PRODUCTION_NAME_CHANNELS" not in _codigos(report)


def test_circulado_pelo_arroba_com_circled_only(tmp_path):
    pasta = tmp_path / "circled"
    sound_devices(
        pasta,
        cena="S01",
        take="01",
        circulado=True,
        com_ixml=False,
        bext_s=True,
        tc=10 * HORA,
    )
    sound_devices(
        pasta,
        cena="S01",
        take="02",
        circulado=False,
        com_ixml=False,
        bext_s=True,
        tc=10 * HORA + 2 * RATE,
    )
    zoom_tr(
        pasta,
        cena="Scene002",
        take="001",
        tr=1,
        circulado=True,
        com_ixml=False,
        com_z_bext=False,
        tc=10 * HORA + 4 * RATE,
    )
    report = Report(strict=False)
    session = read_production(pasta, report, circled_only=True)

    assert [t.name for t in session.tracks] == ["Ch 1", "Ch 2"]
    assert _clips(session) == {
        "Ch 1": [
            ("S01/01", 10 * RATE, 10 * RATE + 4800),
            ("@Scene002-001_Tr1", 14 * RATE, 14 * RATE + 4800),
        ],
        "Ch 2": [("S01/01", 10 * RATE, 10 * RATE + 4800)],
    }
    assert all(c.raw["production"]["circled"] is True for c in session.all_clips())
    takes = next(i for i in report.issues if i.code == "PRODUCTION_TAKES")
    assert takes.context == {"takes": 3, "circled": 2}
    filtro = next(i for i in report.issues if i.code == "PRODUCTION_CIRCLED_ONLY")
    assert filtro.context == {"left_out": 1}


def test_sound_devices_duplicatas_pedacos_e_contagem_dialetos(tmp_path):
    pasta = tmp_path / "sd"
    sound_devices(pasta, cena="S01", take="01", com_ixml=False, bext_s=True, tc=10 * HORA)
    sound_devices_duplicata_mais(
        pasta,
        cena="S01",
        take="01",
        com_ixml=False,
        bext_s=True,
        tc=10 * HORA + 2 * RATE,
    )
    sound_devices_duplicata_letra(
        pasta,
        cena="S01",
        take="01",
        letra="A",
        com_ixml=False,
        bext_s=True,
        tc=10 * HORA + 4 * RATE,
    )
    sound_devices_pedacos(pasta, cena="S02", take="01", letras=("A", "B"), tc=10 * HORA + 6 * RATE)
    report = Report(strict=False)
    session = read_production(pasta, report)

    assert session.layout_blocks[0]["takes"] == 5
    assert next(i for i in report.issues if i.code == "PRODUCTION_DIALECT").context == {
        "dialects": {"sound_devices": 5},
    }

    sem_dialeto = tmp_path / "sem"
    wav(sem_dialeto / "livre.wav", 1, 4800)
    report_sem = Report(strict=False)
    read_production(sem_dialeto, report_sem)
    assert "PRODUCTION_DIALECT" not in _codigos(report_sem)

    misto = tmp_path / "misto"
    zoom_poly(misto, tc=10 * HORA)
    sound_devices(misto, tc=10 * HORA + 2 * RATE)
    tascam_x8_faixa(misto, tc=10 * HORA + 4 * RATE)
    tascam_dr40x(misto, tc=10 * HORA + 6 * RATE)
    wav(
        misto / "livre.wav",
        1,
        4800,
        extras=((b"bext", bext(time_reference=10 * HORA + 8 * RATE)),),
    )
    report_misto = Report(strict=False)
    read_production(misto, report_misto)
    dial = next(i for i in report_misto.issues if i.code == "PRODUCTION_DIALECT")
    assert dial.severity == "info"
    assert dial.context == {
        "dialects": {"sound_devices": 1, "tascam_dr40": 1, "tascam_x8": 1, "zoom": 1},
    }


def test_pasta_take_com_arquivo_sem_ixml_nao_quebra_e_fica_separada(tmp_path):
    from tests.unit.test_som_direto_leitor import wav

    grav = tmp_path / "gravacao"
    zoom_tr(grav, cena="Scene001", take="003", tr=1, tape="R01", data="2025-05-06", tc=10 * HORA)
    wav(grav / "Scene001_004.TAKE" / "solto.wav", 1, 4800)
    report = Report(strict=False)
    session = read_production(grav, report)
    assert len(session.layout_blocks) == 2
    assert "PRODUCTION_TAKE_FOLDERS_KEPT" in _codigos(report)


def test_um_wav_so_dentro_de_pasta_take_nao_avisa(tmp_path):
    caminho = zoom_tr(
        tmp_path / "g", cena="Scene001", take="003", tr=1, tape="R01", data="2025-05-06", tc=0
    )
    report = Report(strict=False)
    read_production(caminho, report)
    assert "PRODUCTION_TAKE_FOLDERS_KEPT" not in _codigos(report)
