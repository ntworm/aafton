"""Construtores de AAF sinteticos de corte de imagem para testes."""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path
from typing import Any

import aaf2
from aaf2.auid import AUID
from aaf2.rational import AAFRational


def _normalizar_taxa(rate: tuple[int, int] | Fraction | int) -> tuple[int, int]:
    """Converte especificacao de taxa de frames em par (num, den)."""
    if isinstance(rate, tuple):
        return int(rate[0]), int(rate[1])
    if hasattr(rate, "num") and hasattr(rate, "den"):
        return int(rate.num), int(rate.den)
    frac = Fraction(rate)
    return frac.numerator, frac.denominator


def _criar_cdci_descriptor(f, edit_rate: AAFRational, length: int, filename: str | None = None):
    """Cria CDCIDescriptor valido para SourceMob de arquivo de video."""
    desc = f.create.CDCIDescriptor()
    props = (
        ("SampleRate", edit_rate),
        ("Length", length),
        ("StoredWidth", 1920),
        ("StoredHeight", 1080),
        ("FrameLayout", "FullFrame"),
        ("VideoLineMap", [0, 0]),
        ("ImageAspectRatio", AAFRational(16, 9)),
        ("ComponentWidth", 8),
        ("HorizontalSubsampling", 2),
    )
    for chave, valor in props:
        desc[chave].value = valor
    if filename:
        loc = f.create.NetworkLocator()
        loc["URLString"].value = f"file:///C:/media/{filename}"
        desc["Locator"].append(loc)
    return desc


def _criar_source_clip(f, mob, start: int, length: int):
    """Cria SourceClip de Picture apontando para o slot 1 de mob."""
    sc = f.create.SourceClip(media_kind="picture", start=start)
    sc.length, sc.mob, sc.slot_id = length, mob, 1
    return sc


def _criar_cadeia_reel(f, cache, ev, edit_rate: AAFRational, fps_nominal: int, drop_frame: bool):
    """Cria ou reutiliza cadeia TapeMob -> file SourceMob -> MasterMob."""
    reel, tape_start, filename = ev["reel"], ev.get("tape_start", 0), ev.get("filename")
    chave = (reel, tape_start, filename)
    if chave in cache:
        return cache[chave]
    tape_len = max(24 * 3600 * max(1, fps_nominal), ev["start"] + ev["length"] + 1000)

    tape = f.create.SourceMob()
    tape.name, tape["EssenceDescription"].value = reel, f.create.TapeDescriptor()
    f.content.mobs.append(tape)
    t_pic = tape.create_timeline_slot(edit_rate=edit_rate)
    t_pic.slot_id, t_pic.name = 1, "V1"
    t_pic.segment = f.create.Filler("picture", length=tape_len)
    t_tc = tape.create_timeline_slot(edit_rate=edit_rate)
    t_tc.slot_id, t_tc.name = 2, "TC"
    tc = f.create.Timecode(fps=fps_nominal, drop=drop_frame)
    tc.start, tc.length, t_tc.segment = tape_start, tape_len, tc

    file_mob = f.create.SourceMob()
    file_mob.name = f"{reel} <SOURCE MOB>"
    file_mob["EssenceDescription"].value = _criar_cdci_descriptor(f, edit_rate, tape_len, filename)
    f.content.mobs.append(file_mob)
    f_slot = file_mob.create_timeline_slot(edit_rate=edit_rate)
    f_slot.slot_id, f_slot.name = 1, "V1"
    f_slot.segment = _criar_source_clip(f, tape, 0, tape_len)

    master = f.create.MasterMob()
    master.name = reel
    f.content.mobs.append(master)
    m_slot = master.create_timeline_slot(edit_rate=edit_rate)
    m_slot.slot_id, m_slot.name = 1, "V1"
    m_slot.segment = _criar_source_clip(f, file_mob, 0, tape_len)
    cache[chave] = master
    return master


def _adicionar_slots_extras(
    f, comp, cache, edit_rate, fps_nominal, drop_frame, with_v2, with_sound
) -> None:
    """Adiciona slot V2 e slot de som opcionais para testar que sao ignorados."""
    if with_v2:
        ev_v2 = {"reel": "V2_IGNORED", "tape_start": 0, "start": 0, "length": 24}
        v2_mob = _criar_cadeia_reel(f, cache, ev_v2, edit_rate, fps_nominal, drop_frame)
        v2 = comp.create_timeline_slot(edit_rate=edit_rate)
        v2.name, v2.segment = "V2", f.create.Sequence(media_kind="picture")
        v2.segment.components.append(_criar_source_clip(f, v2_mob, 0, 24))
    if with_sound:
        a1 = comp.create_timeline_slot(edit_rate=48000)
        a1.name, a1.segment = "A1", f.create.Sequence(media_kind="sound")
        a1.segment.components.append(f.create.Filler("sound", length=48000))


def _adicionar_evento_v1(f, v1_seq, ev, cache, defs, edit_rate, fps_nominal, drop_frame) -> int:
    """Adiciona evento (clip, gap ou transition) na Sequence V1 e devolve delta."""
    kind, length = ev["kind"], ev["length"]
    if kind == "gap":
        v1_seq.components.append(f.create.Filler("picture", length=length))
        return length
    if kind == "transition":
        if "dissolve" not in defs:
            op_def = f.create.OperationDef(
                AUID("0c3bea41-fc05-11d2-8a29-0050040ef7d2"), "Video Dissolve", ""
            )
            op_def["IsTimeWarp"].value, op_def["NumberInputs"].value = False, 2
            op_def["DataDefinition"].value = f.dictionary.lookup_datadef("Picture")
            f.dictionary["OperationDefinitions"].append(op_def)
            defs["dissolve"] = op_def
        fade_op = f.create.OperationGroup(defs["dissolve"])
        fade_op.media_kind, fade_op.length = "picture", length
        trans = f.create.Transition(media_kind="picture", length=length)
        trans.cutpoint, trans["OperationGroup"].value = ev["cutpoint"], fade_op
        v1_seq.components.append(trans)
        return -length
    master = _criar_cadeia_reel(f, cache, ev, edit_rate, fps_nominal, drop_frame)
    v1_seq.components.append(_criar_source_clip(f, master, ev["start"], length))
    return length


def build_picture_cut_aaf(
    path: str | Path,
    events: list[dict[str, Any]],
    rate: tuple[int, int] | Fraction | int = (24, 1),
    start_tc_frames: int = 86400,
    *,
    drop_frame: bool = False,
    slot_name: str = "V1",
    with_v2: bool = False,
    with_sound: bool = False,
) -> Path:
    """Constroi AAF sintetico com slot Picture V1, Timecode e cadeia TapeMob -> MasterMob."""
    caminho = Path(path)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    num, den = _normalizar_taxa(rate)
    edit_rate, fps_nominal = AAFRational(num, den), round(Fraction(num, den))

    with aaf2.open(str(caminho), "w") as f:
        comp = f.create.CompositionMob("PICTURE_CUT")
        comp.usage = "Usage_TopLevel"
        f.content.mobs.append(comp)
        v1 = comp.create_timeline_slot(edit_rate=edit_rate)
        v1.name, v1.segment = slot_name, f.create.Sequence(media_kind="picture")

        cache, defs = {}, {}
        total = sum(
            _adicionar_evento_v1(f, v1.segment, ev, cache, defs, edit_rate, fps_nominal, drop_frame)
            for ev in events
        )
        _adicionar_slots_extras(
            f, comp, cache, edit_rate, fps_nominal, drop_frame, with_v2, with_sound
        )
        tc_slot = comp.create_timeline_slot(edit_rate=edit_rate)
        tc_slot.name = "TC"
        tc = f.create.Timecode(fps=fps_nominal, drop=drop_frame)
        tc.start, tc.length, tc_slot.segment = start_tc_frames, max(1, total), tc
    return caminho
