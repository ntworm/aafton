"""Testes do leitor de lista de corte de imagem (V1) a partir de AAF."""

from __future__ import annotations

from pathlib import Path

import aaf2
from aaf2.auid import AUID
from aaf2.rational import AAFRational
import pytest

from aafton.cut_ir import CutEvent
from aafton.cut_read import read_cut
from aafton.cut_read_aaf import read_cut_aaf
from aafton.report import Report, StrictModeError
from aafton.timecode import TimecodeRate
from tests.cut_factory import (
    _criar_cadeia_reel,
    _criar_cdci_descriptor,
    _criar_source_clip,
    build_picture_cut_aaf,
)


def _codigos(report: Report) -> list[str]:
    """Codigos de todas as ocorrencias registradas no relatorio."""
    return [issue.code for issue in report.issues]


def _nova_comp(f, nome: str, start_tc: int | None = None, tc_len: int = 24):
    """Cria CompositionMob com slot opcional de Timecode."""
    comp = f.create.CompositionMob(nome)
    comp.usage = "Usage_TopLevel"
    f.content.mobs.append(comp)
    if start_tc is not None:
        tc = f.create.Timecode(fps=24, drop=False)
        tc.start, tc.length = start_tc, tc_len
        comp.create_timeline_slot(edit_rate=24).segment = tc
    return comp


def _criar_wrapped_aaf(caminho: Path) -> Path:
    """AAF com OperationGroup, Selector e Sequence aninhada na faixa V1."""
    with aaf2.open(str(caminho), "w") as f:
        comp = _nova_comp(f, "WRAPPED", start_tc=86400, tc_len=84)
        op_def = f.create.OperationDef(AUID("9d2ea896-0968-11d3-8a38-0050040ef7d2"), "Resize", "")
        op_def["IsTimeWarp"].value, op_def["NumberInputs"].value = False, 1
        op_def["DataDefinition"].value = f.dictionary.lookup_datadef("Picture")
        f.dictionary["OperationDefinitions"].append(op_def)

        cache, rate, mobs = {}, AAFRational(24, 1), {}
        for reel, tc_s, st in [("R_OP", 100, 5), ("R_SEL", 200, 10), ("R_ALT", 999, 0)]:
            ev = {"reel": reel, "tape_start": tc_s, "start": st, "length": 24}
            mobs[reel] = _criar_cadeia_reel(f, cache, ev, rate, 24, False)
        ev_seq = {"reel": "R_SEQ", "tape_start": 300, "start": 0, "length": 24}
        mobs["R_SEQ"] = _criar_cadeia_reel(f, cache, ev_seq, rate, 24, False)

        og = f.create.OperationGroup(op_def)
        og.media_kind, og.length = "picture", 24
        og.segments.append(_criar_source_clip(f, mobs["R_OP"], 5, 24))
        sel = f.create.Selector(media_kind="picture", length=24)
        sel["Selected"].value = _criar_source_clip(f, mobs["R_SEL"], 10, 24)
        sel["Alternates"].value = [_criar_source_clip(f, mobs["R_ALT"], 0, 24)]
        nested = f.create.Sequence(media_kind="picture")
        nested.length = 36
        nested.components.extend(
            [f.create.Filler("picture", length=12), _criar_source_clip(f, mobs["R_SEQ"], 0, 24)]
        )
        slot = comp.create_timeline_slot(edit_rate=24)
        slot.name, slot.segment = "V1", f.create.Sequence(media_kind="picture")
        slot.segment.components.extend([og, sel, nested])
    return caminho


def _criar_aaf_sem_tc_de_fonte(caminho: Path) -> Path:
    """AAF com cadeia MasterMob (48 fps) -> file SourceMob sem TapeMob nem TC."""
    with aaf2.open(str(caminho), "w") as f:
        comp = _nova_comp(f, "NO_TC", start_tc=86400, tc_len=24)
        file_mob = f.create.SourceMob()
        file_mob.name = "IgnoredFileMob"
        file_mob["EssenceDescription"].value = _criar_cdci_descriptor(
            f, AAFRational(24, 1), 1000, "Clip_X.mov"
        )
        f.content.mobs.append(file_mob)
        fslot = file_mob.create_timeline_slot(edit_rate=24)
        fslot.slot_id, fslot.segment = 1, f.create.Filler("picture", length=1000)
        master = f.create.MasterMob()
        master.name = "IgnoredMaster"
        f.content.mobs.append(master)
        mslot = master.create_timeline_slot(edit_rate=48)
        mslot.slot_id, mslot.segment = 1, _criar_source_clip(f, file_mob, 6, 1000)
        vslot = comp.create_timeline_slot(edit_rate=24)
        vslot.name, vslot.segment = "V1", f.create.Sequence(media_kind="picture")
        vslot.segment.components.append(_criar_source_clip(f, master, 20, 24))
    return caminho


def test_3_clips_e_1_gap_24fps(tmp_path: Path):
    eventos = [
        {"kind": "clip", "reel": "R001", "tape_start": 86400, "start": 0, "length": 48},
        {"kind": "gap", "length": 24},
        {"kind": "clip", "reel": "R002", "tape_start": 172800, "start": 12, "length": 36},
        {"kind": "clip", "reel": "Cam A 003", "tape_start": 259200, "start": 24, "length": 60},
    ]
    caminho = build_picture_cut_aaf(
        tmp_path / "cut24.aaf", eventos, rate=(24, 1), start_tc_frames=86400
    )
    report = Report(strict=True)
    corte = read_cut_aaf(caminho, report)
    assert corte is not None and corte.path == str(caminho)
    assert (corte.rate, corte.start_frames, report.issues) == (TimecodeRate(24, 1), 86400, [])
    assert corte.events == [
        CutEvent(0, "clip", "R001", "R001", 86400, 86448, 86400, 86448, None, 0),
        CutEvent(1, "gap", None, None, None, None, 86448, 86472, None, 0),
        CutEvent(2, "clip", "R002", "R002", 172812, 172848, 86472, 86508, None, 0),
        CutEvent(3, "clip", "CAMA003", "Cam A 003", 259224, 259284, 86508, 86568, None, 0),
    ]


def test_dissolve_24_frames_cutpoint_12(tmp_path: Path):
    eventos = [
        {"kind": "clip", "reel": "R001", "tape_start": 86400, "start": 0, "length": 60},
        {"kind": "transition", "length": 24, "cutpoint": 12},
        {"kind": "clip", "reel": "R002", "tape_start": 172800, "start": 88, "length": 60},
    ]
    caminho = build_picture_cut_aaf(
        tmp_path / "dissolve.aaf", eventos, rate=(24, 1), start_tc_frames=86400
    )
    corte = read_cut_aaf(caminho, Report(strict=True))
    assert corte is not None and len(corte.events) == 2
    e0, e1 = corte.events
    assert (e0.record_in, e0.record_out, e0.source_in, e0.source_out) == (
        86400,
        86448,
        86400,
        86448,
    )
    assert (e1.transition_start, e1.transition_frames) == (86436, 24)
    assert (e1.record_in, e1.record_out, e1.source_in, e1.source_out) == (
        86448,
        86496,
        172900,
        172948,
    )


@pytest.mark.parametrize(
    ("rate", "tc", "drop", "esperado"),
    [
        ((25, 1), 90000, False, TimecodeRate(25, 1)),
        ((24000, 1001), 86400, False, TimecodeRate(24000, 1001, False)),
        ((30000, 1001), 107892, True, TimecodeRate(30000, 1001, True)),
    ],
)
def test_taxas_suportadas_25_23976_2997df(tmp_path: Path, rate, tc, drop, esperado):
    eventos = [{"kind": "clip", "reel": "A001", "tape_start": tc, "start": 5, "length": 48}]
    caminho = build_picture_cut_aaf(
        tmp_path / f"cut_{esperado.num}.aaf",
        eventos,
        rate=rate,
        start_tc_frames=tc,
        drop_frame=drop,
    )
    corte = read_cut_aaf(caminho, Report(strict=True))
    assert corte is not None and (corte.rate, corte.start_frames) == (esperado, tc)
    exp = CutEvent(0, "clip", "A001", "A001", tc + 5, tc + 53, tc, tc + 48, None, 0)
    assert corte.events == [exp]


def test_v2_e_slot_de_som_ignorados(tmp_path: Path):
    eventos = [{"kind": "clip", "reel": "R001", "tape_start": 86400, "start": 0, "length": 24}]
    caminho = build_picture_cut_aaf(
        tmp_path / "multi_slots.aaf", eventos, slot_name="v1", with_v2=True, with_sound=True
    )
    corte = read_cut_aaf(caminho, Report(strict=True))
    assert corte is not None and len(corte.events) == 1 and corte.events[0].reel == "R001"


def test_fallback_primeiro_slot_picture_com_source_clip(tmp_path: Path):
    caminho = tmp_path / "fallback_slot.aaf"
    with aaf2.open(str(caminho), "w") as f:
        comp = _nova_comp(f, "FALLBACK", start_tc=86400, tc_len=24)
        s_empty = comp.create_timeline_slot(edit_rate=24)
        s_empty.name, s_empty.segment = "Video 1", f.create.Sequence(media_kind="picture")
        s_empty.segment.components.append(f.create.Filler("picture", length=24))
        ev = {"reel": "R007", "tape_start": 0, "start": 0, "length": 24}
        master = _criar_cadeia_reel(f, {}, ev, AAFRational(24, 1), 24, False)
        s_vid = comp.create_timeline_slot(edit_rate=24)
        s_vid.name, s_vid.segment = "Video 2", f.create.Sequence(media_kind="picture")
        s_vid.segment.components.append(_criar_source_clip(f, master, 0, 24))
    corte = read_cut_aaf(caminho, Report(strict=True))
    assert corte is not None and len(corte.events) == 1 and corte.events[0].reel == "R007"


def test_sem_slot_de_picture_reporta_erro(tmp_path: Path):
    caminho = tmp_path / "audio_only.aaf"
    with aaf2.open(str(caminho), "w") as f:
        comp = _nova_comp(f, "AUDIO_ONLY")
        slot = comp.create_timeline_slot(edit_rate=48000)
        slot.name, slot.segment = "A1", f.create.Sequence(media_kind="sound")
        slot.segment.components.append(f.create.Filler("sound", length=48000))
    report = Report(strict=False)
    assert read_cut_aaf(caminho, report) is None
    assert "RECONFORM_NO_PICTURE_TRACK" in _codigos(report)
    with pytest.raises(StrictModeError):
        read_cut_aaf(caminho, Report(strict=True))


def test_taxa_nao_suportada_reporta_erro(tmp_path: Path):
    eventos = [{"kind": "clip", "reel": "R001", "tape_start": 0, "start": 0, "length": 50}]
    caminho = build_picture_cut_aaf(tmp_path / "rate50.aaf", eventos, rate=(50, 1))
    report = Report(strict=False)
    assert read_cut_aaf(caminho, report) is None
    assert "RECONFORM_RATE_UNSUPPORTED" in _codigos(report)


def test_timewarp_reporta_erro(tmp_path: Path):
    caminho = tmp_path / "timewarp.aaf"
    with aaf2.open(str(caminho), "w") as f:
        comp = _nova_comp(f, "TW")
        op_def = f.create.OperationDef(
            AUID("9d2ea895-0968-11d3-8a38-0050040ef7d2"), "Motion Effect", ""
        )
        op_def["IsTimeWarp"].value, op_def["NumberInputs"].value = True, 1
        op_def["DataDefinition"].value = f.dictionary.lookup_datadef("Picture")
        f.dictionary["OperationDefinitions"].append(op_def)
        ev = {"reel": "R001", "tape_start": 0, "start": 0, "length": 24}
        master = _criar_cadeia_reel(f, {}, ev, AAFRational(24, 1), 24, False)
        og = f.create.OperationGroup(op_def)
        og.media_kind, og.length = "picture", 24
        og.segments.append(_criar_source_clip(f, master, 0, 24))
        slot = comp.create_timeline_slot(edit_rate=24)
        slot.name, slot.segment = "V1", f.create.Sequence(media_kind="picture")
        slot.segment.components.append(og)
    report = Report(strict=False)
    assert read_cut_aaf(caminho, report) is None
    assert "RECONFORM_TIMEWARP" in _codigos(report)


def test_unwrap_operation_group_selector_e_sequence_aninhada(tmp_path: Path):
    caminho = _criar_wrapped_aaf(tmp_path / "wrapped.aaf")
    corte = read_cut_aaf(caminho, Report(strict=True))
    assert corte is not None
    assert corte.events == [
        CutEvent(0, "clip", "R_OP", "R_OP", 105, 129, 86400, 86424, None, 0),
        CutEvent(1, "clip", "R_SEL", "R_SEL", 210, 234, 86424, 86448, None, 0),
        CutEvent(2, "gap", None, None, None, None, 86448, 86460, None, 0),
        CutEvent(3, "clip", "R_SEQ", "R_SEQ", 300, 324, 86460, 86484, None, 0),
    ]


def test_cadeia_sem_timecode_e_source_por_arquivo(tmp_path: Path):
    caminho = _criar_aaf_sem_tc_de_fonte(tmp_path / "sem_tc_fonte.aaf")
    report = Report(strict=True)
    corte = read_cut_aaf(caminho, report)
    assert corte is not None and "RECONFORM_SOURCE_TC_MISSING" in _codigos(report)
    esperado = CutEvent(0, "clip", "CLIP_X", None, 16, 40, 86400, 86424, None, 0)
    assert corte.events == [esperado]


def test_read_cut_despacha_aaf(tmp_path: Path):
    eventos = [{"kind": "clip", "reel": "R001", "tape_start": 86400, "start": 0, "length": 24}]
    caminho = build_picture_cut_aaf(tmp_path / "dispatch.AAF", eventos)
    via_read_cut = read_cut(caminho, Report(strict=True))
    via_direct = read_cut_aaf(caminho, Report(strict=True))
    assert via_read_cut is not None and via_read_cut == via_direct
