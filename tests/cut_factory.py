"""Construtores de AAF sinteticos de corte de imagem para testes.

O `f.create` do pyaaf2 e uma fabrica com estado: `f.create.X` guarda o nome
da classe e devolve o metodo que cria o objeto. Por isso nenhuma criacao do
pyaaf2 fica dentro dos argumentos de outra: o nome da classe interna
sobrescreveria o da externa e o objeto sairia da classe errada.
"""

from __future__ import annotations

import uuid
from fractions import Fraction
from pathlib import Path
from typing import Any

import aaf2
from aaf2.auid import AUID
from aaf2.rational import AAFRational

# O Media Composer grava o Speed Ratio do 2x com o par cru, sem reduzir.
SPEED_RATIO_DO_VID = (2000000, 1000000)


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
    """Adiciona evento (clip, gap, transition ou warp) na Sequence V1 e devolve delta."""
    kind, length = ev["kind"], ev["length"]
    if kind == "gap":
        v1_seq.components.append(f.create.Filler("picture", length=length))
        return length
    if kind == "grupo":
        return _criar_grupo_com_planos(
            f, v1_seq, ev, cache, defs, edit_rate, fps_nominal, drop_frame
        )
    if kind == "warp":
        master = _criar_cadeia_reel(f, cache, ev, edit_rate, fps_nominal, drop_frame)
        v1_seq.components.append(_criar_grupo_de_velocidade(f, defs, ev, master, length))
        return length
    if kind == "grupo_warp":
        master = _criar_cadeia_reel(f, cache, ev, edit_rate, fps_nominal, drop_frame)
        v1_seq.components.append(_criar_grupo_com_warp(f, defs, ev, master, length))
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


def _auid(nome: str) -> AUID:
    """AUID estavel para um nome, para nao repetir ids no dicionario."""
    return AUID(str(uuid.uuid5(uuid.NAMESPACE_URL, f"aafton/cut_factory/{nome}")))


def _op_def_de_velocidade(f, defs, nome_parametro: str):
    """OperationDef 'Video Speed Control' e o ParameterDef da velocidade."""
    chave = f"warp:{nome_parametro}"
    if chave not in defs:
        op_def = f.create.OperationDef(_auid(f"warp/{nome_parametro}"), "Video Speed Control", "")
        op_def["IsTimeWarp"].value, op_def["NumberInputs"].value = True, 1
        op_def["DataDefinition"].value = f.dictionary.lookup_datadef("Picture")
        param_def = f.create.ParameterDef(
            _auid(f"param/{nome_parametro}"),
            nome_parametro,
            "",
            f.dictionary.lookup_typedef("Rational"),
        )
        f.dictionary["ParameterDefinitions"].append(param_def)
        op_def["ParametersDefined"].value.append(param_def)
        f.dictionary["OperationDefinitions"].append(op_def)
        defs[chave] = op_def
        defs[f"param:{nome_parametro}"] = param_def
    return defs[chave], defs[f"param:{nome_parametro}"]


def _interp_def(f, defs):
    """InterpolationDef 'Linear', para os pontos de automacao.

    A definicao precisa existir antes de `f.create.VaryingValue` ser
    resolvido: o pyaaf2 quebra a criacao do VaryingValue quando uma
    InterpolationDef nova e criada depois que a classe foi resolvida.
    """
    if "interp" not in defs:
        interp = f.create.InterpolationDef(
            AUID("5b7199c0-6744-11d2-8326-00609719b0fb"), "Linear", ""
        )
        f.dictionary["InterpolationDefinitions"].append(interp)
        defs["interp"] = interp
    return defs["interp"]


def _rational_do_aaf(num: int, den: int) -> AAFRational:
    """Rational do AAF com o par cru, como o Avid grava o Speed Ratio.

    O AAFRational do pyaaf2 guarda o par como veio, sem reduzir: e assim que
    o arquivo fica com 2000000/1000000, como o Media Composer grava o 2x.
    """
    return AAFRational(int(num), int(den))


def _anexar_parametro(f, defs, grupo, param_def, ev) -> None:
    """Anexa ao grupo o valor do parametro de velocidade."""
    pontos = ev.get("pontos")
    if pontos is not None:
        interpolacao = _interp_def(f, defs)
        vv = f.create.VaryingValue(param_def, interpolacao)
        for (t_num, t_den), (v_num, v_den) in pontos:
            cp = f.create.ControlPoint()
            cp["Time"].value = _rational_do_aaf(t_num, t_den)
            cp["Value"].value = _rational_do_aaf(v_num, v_den)
            vv["PointList"].append(cp)
        grupo.parameters.append(vv)
        return
    num, den = ev.get("velocidade", (1, 1))
    grupo.parameters.append(f.create.ConstantValue(param_def, _rational_do_aaf(num, den)))


def _op_def_do_wrapper(f, defs):
    """OperationDef 'Picture Wrapper', criado e registrado uma vez so."""
    if "wrapper" not in defs:
        op_def = f.create.OperationDef(_auid("wrapper"), "Picture Wrapper", "")
        op_def["IsTimeWarp"].value, op_def["NumberInputs"].value = False, 1
        op_def["DataDefinition"].value = f.dictionary.lookup_datadef("Picture")
        f.dictionary["OperationDefinitions"].append(op_def)
        defs["wrapper"] = op_def
    return defs["wrapper"]


def _entrada_do_warp(f, defs, ev, master, comprimento):
    """Entrada do grupo de velocidade: o clip, um grupo comum ou outro warp."""
    if ev.get("aninhado"):
        interno = dict(ev)
        interno["aninhado"] = False
        return _criar_grupo_de_velocidade(f, defs, interno, master, comprimento)
    if ev.get("entrada_em_sequence"):
        return _criar_sequence_do_clip(f, ev, master, comprimento)
    clip = _criar_source_clip(f, master, ev["start"], comprimento)
    if not ev.get("entrada_em_grupo"):
        return clip
    wrapper_def = _op_def_do_wrapper(f, defs)
    grupo = f.create.OperationGroup(wrapper_def)
    grupo.media_kind, grupo.length = "picture", comprimento
    grupo.segments.append(clip)
    return grupo


def _criar_grupo_de_velocidade(f, defs, ev, master, comprimento):
    """OperationGroup 'Video Speed Control' sobre o material da entrada."""
    nome = ev.get("parametro", "Speed Ratio")
    op_def, param_def = _op_def_de_velocidade(f, defs, nome)
    grupo = f.create.OperationGroup(op_def)
    grupo.media_kind, grupo.length = "picture", comprimento
    grupo.segments.append(_entrada_do_warp(f, defs, ev, master, comprimento))
    _anexar_parametro(f, defs, grupo, param_def, ev)
    return grupo


def _criar_sequence_do_clip(f, ev, master, comprimento):
    """Sequence com um SourceClip, como entrada do grupo de velocidade."""
    clip = _criar_source_clip(f, master, ev["start"], ev.get("fonte_comprimento", comprimento))
    sequencia = f.create.Sequence(media_kind="picture")
    sequencia.length = clip.length
    sequencia.components.append(clip)
    return sequencia


def _criar_grupo_com_warp(f, defs, ev, master, comprimento):
    """OperationGroup comum por cima de um grupo de velocidade.

    O grupo de fora nao e efeito: ele e um embrulho comum, e o grupo de
    warping esta dentro dele. O leitor nao pode perder a velocidade por
    causa do embrulho.
    """
    wrapper_def = _op_def_do_wrapper(f, defs)
    grupo = f.create.OperationGroup(wrapper_def)
    grupo.media_kind, grupo.length = "picture", comprimento
    grupo.segments.append(_criar_grupo_de_velocidade(f, defs, ev, master, comprimento))
    return grupo


def _criar_grupo_com_planos(f, v1_seq, ev, cache, defs, edit_rate, fps_nominal, drop_frame) -> int:
    """OperationGroup comum que embrulha uma Sequence com varios planos.

    O grupo nao e efeito de velocidade: ele e um embrulho, e cada plano
    dentro dele tem de virar um evento do corte.
    """
    wrapper_def = _op_def_do_wrapper(f, defs)
    grupo = f.create.OperationGroup(wrapper_def)
    grupo.media_kind = "picture"
    interna = f.create.Sequence(media_kind="picture")
    total = 0
    for interno in ev["internos"]:
        total += _adicionar_evento_v1(
            f, interna, interno, cache, defs, edit_rate, fps_nominal, drop_frame
        )
    interna.length = total
    grupo.length = total
    grupo.segments.append(interna)
    v1_seq.components.append(grupo)
    return total


def _criar_camadas(f, camadas, cache, defs, edit_rate, fps_nominal, drop_frame) -> list:
    """Cria uma Sequence por camada, cada uma com o seu comprimento."""
    sequencias = []
    for camada in camadas:
        seq = f.create.Sequence(media_kind="picture")
        total = 0
        for ev in camada:
            total += _adicionar_evento_v1(
                f, seq, ev, cache, defs, edit_rate, fps_nominal, drop_frame
            )
        seq.length = total
        sequencias.append(seq)
    return sequencias


def _comprimento_das_camadas(sequencias, camadas) -> int:
    """Comprimento do NestedScope: o da primeira camada com clip.

    O Avid grava o NestedScope com o comprimento da camada de imagem (V1), que
    e a primeira com clip; as camadas de cima podem ser mais curtas.
    """
    for sequencia, camada in zip(sequencias, camadas):
        if any(ev["kind"] in ("clip", "warp") for ev in camada):
            return int(getattr(sequencia, "length", 0) or 0)
    return int(getattr(sequencias[0], "length", 0) or 0) if sequencias else 0


def _slot_de_timecode(f, comp, edit_rate, fps_nominal, drop_frame, start_tc_frames, total) -> None:
    """Cria o slot de Timecode da composicao."""
    tc_slot = comp.create_timeline_slot(edit_rate=edit_rate)
    tc_slot.name = "TC"
    tc = f.create.Timecode(fps=fps_nominal, drop=drop_frame)
    tc.start, tc.length, tc_slot.segment = start_tc_frames, max(1, total), tc


def build_scope_cut_aaf(
    path: str | Path,
    camadas: list[list[dict[str, Any]]],
    rate: tuple[int, int] | Fraction | int = (24, 1),
    start_tc_frames: int = 86400,
    *,
    drop_frame: bool = False,
    slot_name: str = "V1",
    em_grupo: bool = False,
) -> Path:
    """AAF de corte cujo slot de imagem e um NestedScope com uma camada por slot.

    Cada camada e uma lista de eventos (clip ou gap). O leitor escolhe a
    primeira camada que tem SourceClip, e nao a ultima. Com `em_grupo`, o
    slot de imagem tem um OperationGroup comum por cima do NestedScope.
    """
    caminho = Path(path)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    num, den = _normalizar_taxa(rate)
    edit_rate, fps_nominal = AAFRational(num, den), round(Fraction(num, den))

    with aaf2.open(str(caminho), "w") as f:
        comp = f.create.CompositionMob("PICTURE_CUT")
        comp.usage = "Usage_TopLevel"
        f.content.mobs.append(comp)
        slot = comp.create_timeline_slot(edit_rate=edit_rate)
        slot.name = slot_name
        escopo = f.create.NestedScope(media_kind="picture", length=1)
        cache, defs = {}, {}
        sequencias = _criar_camadas(f, camadas, cache, defs, edit_rate, fps_nominal, drop_frame)
        for seq in sequencias:
            escopo.slots.append(seq)
        total = _comprimento_das_camadas(sequencias, camadas)
        escopo.length = total
        if em_grupo:
            wrapper_def = _op_def_do_wrapper(f, defs)
            grupo = f.create.OperationGroup(wrapper_def)
            grupo.media_kind, grupo.length = "picture", total
            grupo.segments.append(escopo)
            slot.segment = grupo
        else:
            slot.segment = escopo
        _slot_de_timecode(f, comp, edit_rate, fps_nominal, drop_frame, start_tc_frames, total)
    return caminho


def build_scope_in_sequence_cut_aaf(
    path: str | Path,
    camadas: list[list[dict[str, Any]]],
    rate: tuple[int, int] | Fraction | int = (24, 1),
    start_tc_frames: int = 86400,
    *,
    drop_frame: bool = False,
) -> Path:
    """AAF de corte com um NestedScope dentro da Sequence do V1.

    O NestedScope tem uma camada por slot; a primeira que tem SourceClip e a
    escolhida pelo leitor.
    """
    caminho = Path(path)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    num, den = _normalizar_taxa(rate)
    edit_rate, fps_nominal = AAFRational(num, den), round(Fraction(num, den))

    with aaf2.open(str(caminho), "w") as f:
        comp = f.create.CompositionMob("PICTURE_CUT")
        comp.usage = "Usage_TopLevel"
        f.content.mobs.append(comp)
        v1 = comp.create_timeline_slot(edit_rate=edit_rate)
        v1.name, v1.segment = "V1", f.create.Sequence(media_kind="picture")
        cache, defs = {}, {}
        sequencias = _criar_camadas(f, camadas, cache, defs, edit_rate, fps_nominal, drop_frame)
        total = _comprimento_das_camadas(sequencias, camadas)
        escopo = f.create.NestedScope(media_kind="picture", length=total)
        for seq in sequencias:
            escopo.slots.append(seq)
        v1.segment.components.append(escopo)
        _slot_de_timecode(f, comp, edit_rate, fps_nominal, drop_frame, start_tc_frames, total)
    return caminho


def build_slot_without_sequence_cut_aaf(
    path: str | Path,
    ev: dict[str, Any],
    rate: tuple[int, int] | Fraction | int = (24, 1),
    start_tc_frames: int = 86400,
    *,
    drop_frame: bool = False,
) -> Path:
    """AAF de corte cujo slot de imagem nao tem Sequence.

    O segmento do slot e lido como esta, sem descer para uma Sequence.
    """
    caminho = Path(path)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    num, den = _normalizar_taxa(rate)
    edit_rate, fps_nominal = AAFRational(num, den), round(Fraction(num, den))

    with aaf2.open(str(caminho), "w") as f:
        comp = f.create.CompositionMob("PICTURE_CUT")
        comp.usage = "Usage_TopLevel"
        f.content.mobs.append(comp)
        cache = {}
        master = _criar_cadeia_reel(f, cache, ev, edit_rate, fps_nominal, drop_frame)
        slot = comp.create_timeline_slot(edit_rate=edit_rate)
        slot.name = "V1"
        slot.segment = _criar_source_clip(f, master, ev["start"], ev["length"])
        _slot_de_timecode(
            f, comp, edit_rate, fps_nominal, drop_frame, start_tc_frames, ev["length"]
        )
    return caminho


def build_scope_with_direct_clip_cut_aaf(
    path: str | Path,
    ev: dict[str, Any],
    rate: tuple[int, int] | Fraction | int = (24, 1),
    start_tc_frames: int = 86400,
    *,
    drop_frame: bool = False,
) -> Path:
    """AAF de corte cujo NestedScope tem uma camada que e o proprio clip.

    A camada nao tem Sequence em volta: a descida do slot de imagem nao
    acha Sequence nenhuma, e o leitor le o segmento do slot como esta.
    """
    caminho = Path(path)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    num, den = _normalizar_taxa(rate)
    edit_rate, fps_nominal = AAFRational(num, den), round(Fraction(num, den))

    with aaf2.open(str(caminho), "w") as f:
        comp = f.create.CompositionMob("PICTURE_CUT")
        comp.usage = "Usage_TopLevel"
        f.content.mobs.append(comp)
        cache = {}
        master = _criar_cadeia_reel(f, cache, ev, edit_rate, fps_nominal, drop_frame)
        slot = comp.create_timeline_slot(edit_rate=edit_rate)
        slot.name = "V1"
        escopo = f.create.NestedScope(media_kind="picture", length=ev["length"])
        escopo.slots.append(_criar_source_clip(f, master, ev["start"], ev["length"]))
        slot.segment = escopo
        _slot_de_timecode(
            f, comp, edit_rate, fps_nominal, drop_frame, start_tc_frames, ev["length"]
        )
    return caminho


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
