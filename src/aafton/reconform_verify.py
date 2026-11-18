"""Verify independente do reconform: a copia aplicada contra os dois cortes.

O oraculo nao usa o mapa de tempo nem o diff do reconform: ele monta as
proprias pecas a partir dos dois cortes (reconform_verify_pecas) e usa esse
conjunto de pecas como unico predicado de correspondencia. Assim um erro do
mapa nao se esconde atras do proprio mapa.

Os pedacos sao divididos onde o apply divide: nas fronteiras dos eventos do
corte antigo e nas bordas das pecas do verify. Leader e cauda tambem sao
conferidas.
"""

from __future__ import annotations

import hashlib
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from .als_paths import AUDIO_CLIP_FIELDS, TRACK_FIELDS
from .als_read_clips import track_name
from .als_write_base import LIVE_CLIP_TICKS_PER_BEAT
from .als_xml import DocumentInvalid, classify_id_tags, get_value, load_als, validate_document
from .cut_ir import CutEvent, CutIR
from .reconform_verify_pecas import Pecas, montar_pecas
from .reconform_verify_velocidade import Peca, frame_do_tc
from .report import Report
from .sidecar import Anchor, read_anchor

_CAMINHO_CLIPS = "DeviceChain/MainSequencer/Sample/ArrangerAutomation/Events/AudioClip"
_SAIU_RE = re.compile(r"^(?P<base>.*) \[saiu\](?: \d+)?$")
# Tolerancia do LoopStart em segundos do arquivo, em samples.
_TOLERANCIA_LOOP = 1


@dataclass
class VerifyResult:
    """Resultado do verify do reconform."""

    ok: bool
    checked_clips: int = 0
    problems: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class _Clip:
    """Clip do arranjo em samples absolutos, com a fonte e os fades."""

    start: int
    end: int
    loop_start: int
    fade_in: float
    fade_out: float
    crossfade_in: str


@dataclass
class _Ctx:
    """Os dois cortes, as pecas do verify e a lista de problemas."""

    old: CutIR
    new: CutIR
    sample_rate: int
    pecas: Pecas
    problems: list[dict] = field(default_factory=list)

    def problema(self, codigo: str, track: str, detalhe: str) -> None:
        self.problems.append({"code": codigo, "track": track, "detail": detalhe})


def _float(node: ET.Element, chave: str) -> float:
    valor = get_value(node, AUDIO_CLIP_FIELDS[chave])
    try:
        return float(valor) if valor is not None else 0.0
    except ValueError:
        return 0.0


def _grade_do_live(beats_inicio: float, beats_fim: float) -> tuple[float, float]:
    """Inicio e fim como o Live guarda: na grade de ticks do relogio.

    O Live arredonda o inicio e o comprimento do clip para a grade de ticks
    (3843840 por beat) e toca a partir da amostra seguinte.
    """
    ticks = LIVE_CLIP_TICKS_PER_BEAT
    inicio = round(beats_inicio * ticks) / ticks
    return inicio, inicio + round((beats_fim - beats_inicio) * ticks) / ticks


def _amostra_do_live(beats: float, anchor: Anchor) -> int:
    """Amostra em que o Live toca a posicao salva, em samples da origem."""
    return math.ceil(beats * 60.0 / anchor.bpm * anchor.sample_rate)


def _ler_clip(node: ET.Element, anchor: Anchor) -> _Clip:
    """Clip em samples absolutos pela ancora do set, como o Live toca."""
    inicio, fim = _grade_do_live(_float(node, "current_start"), _float(node, "current_end"))
    return _Clip(
        start=anchor.live_origin_samples + _amostra_do_live(inicio, anchor),
        end=anchor.live_origin_samples + _amostra_do_live(fim, anchor),
        loop_start=round(Fraction(_float(node, "loop_start")) * anchor.sample_rate),
        fade_in=_float(node, "fade_in_length"),
        fade_out=_float(node, "fade_out_length"),
        crossfade_in=get_value(node, AUDIO_CLIP_FIELDS["crossfade_in_state"]) or "0",
    )


def _tracks(root: ET.Element) -> list[ET.Element]:
    return list(root.findall("./LiveSet/Tracks/AudioTrack"))


def _clips_por_track(root: ET.Element, anchor: Anchor) -> dict[str, list[_Clip]]:
    saida: dict[str, list[_Clip]] = {}
    for node in _tracks(root):
        clips = [_ler_clip(c, anchor) for c in node.findall(_CAMINHO_CLIPS)]
        saida.setdefault(track_name(node), []).extend(clips)
    return saida


def _evento_em(eventos: list[CutEvent], frame: Fraction) -> CutEvent | None:
    for evento in eventos:
        if evento.record_in <= frame < evento.record_out:
            return evento
    return None


def _destinos(ctx: _Ctx, frame: int | Fraction) -> list[int | Fraction]:
    """Quadros do corte novo que casam com um frame do corte antigo.

    O conjunto de pecas do verify e o unico predicado de correspondencia: um
    frame do corte antigo casa quando alguma peca o cobre, e a peca sabe o
    frame do corte novo que corresponde.
    """
    peca = ctx.pecas.peca_de(frame)
    if peca is None:
        return []
    return [peca.new_in + (frame - peca.old_in)]


def _tem_tc(evento: CutEvent) -> bool:
    """Verdadeiro quando o evento tem chave de origem e intervalo de TC."""
    return (
        evento.kind == "clip"
        and evento.source is not None
        and evento.source_in is not None
        and evento.source_out is not None
    )


def _interseccoes(ctx: _Ctx) -> list[Peca]:
    """As intersecoes entre os dois cortes: as pecas do verify.

    A peca e o trecho que casa: ela sabe o que entrou e o que saiu de cada
    corte, e e a unica fonte de correspondencia do verify.
    """
    return list(ctx.pecas.pecas)


def _frames(ctx: _Ctx, samples: int) -> Fraction:
    return Fraction(samples) * ctx.old.rate.fps / ctx.sample_rate


def _samples(ctx: _Ctx, frames: Fraction) -> int:
    return round(frames * ctx.sample_rate / ctx.new.rate.fps)


def _para_samples(ctx: _Ctx, frames: int) -> int:
    """Converte um indice de frame do corte antigo em samples."""
    return round(Fraction(frames) * ctx.sample_rate / ctx.old.rate.fps)


def _destino_do_frame(ctx: _Ctx, evento: CutEvent, frame: int) -> int | None:
    """Frame do corte novo para um frame de um evento antigo, ou None."""
    peca = ctx.pecas.peca_de(frame)
    if peca is None or peca.evento_antigo != evento:
        return None
    return peca.new_in + (frame - peca.old_in)


def _delta_lider(ctx: _Ctx) -> int:
    """Deslocamento, em samples, do material antes do primeiro evento antigo."""
    return _samples(ctx, ctx.new.start_frames - ctx.old.start_frames)


def _maior_tc_coberto(ctx: _Ctx, evento: CutEvent) -> int | None:
    """Maior TC de origem do evento antigo que o corte novo cobre.

    A cobertura vem das pecas do verify: so o material que casa conta.
    """
    maior = None
    for peca in ctx.pecas.pecas:
        if peca.evento_antigo != evento:
            continue
        if maior is None or peca.tc_out - 1 > maior:
            maior = peca.tc_out - 1
    return maior


def _oraculo_fora(ctx: _Ctx, inicio: int, frame: Fraction) -> tuple[str, int | None]:
    """Material fora de todo evento antigo: leader no comeco, cauda no fim."""
    if ctx.old.events and frame < min(ev.record_in for ev in ctx.old.events):
        return "map", inicio + _delta_lider(ctx)
    from .reconform_verify_cauda import oraculo_cauda

    return oraculo_cauda(ctx, inicio, frame)


def _oraculo(ctx: _Ctx, inicio: int) -> tuple[str, int | None]:
    """('map', posicao nova), ('saiu', emenda) ou ('fora', None)."""
    frame = _frames(ctx, inicio)
    evento = _evento_em(ctx.old.events, frame)
    if evento is None:
        return _oraculo_fora(ctx, inicio, frame)
    if evento.kind != "clip" or evento.source_in is None:
        return "fora", None
    destinos = _destinos(ctx, frame)
    if not destinos:
        return "saiu", _emenda(ctx, frame)
    return "map", _samples(ctx, destinos[0])


def _tc_coberto(ctx: _Ctx, evento: CutEvent, limite: int) -> int | None:
    """Maior TC de origem do evento, abaixo do limite, que o corte novo cobre.

    A cobertura vem das pecas do verify: so o material que casa conta.
    """
    maior = None
    for peca in ctx.pecas.pecas:
        if peca.evento_antigo != evento:
            continue
        hi = min(peca.tc_out, limite)
        if hi > peca.tc_in and (maior is None or hi - 1 > maior):
            maior = hi - 1
    return maior


def _frame_guardado(ctx: _Ctx, evento: CutEvent, tc: int) -> int | None:
    """Ultimo frame guardado do corte antigo, antes do trecho removido."""
    guardado = _tc_coberto(ctx, evento, tc)
    if guardado is not None:
        return frame_do_tc(evento, guardado)
    for outro in sorted(ctx.old.events, key=lambda ev: ev.record_in, reverse=True):
        if not _tem_tc(outro):
            continue
        if outro.record_out > evento.record_in:
            continue
        coberto = _maior_tc_coberto(ctx, outro)
        if coberto is None:
            continue
        return frame_do_tc(outro, coberto)
    return None


def _frame_novo(ctx: _Ctx, frame: int) -> int | None:
    """Frame do corte novo para um frame do corte antigo, pela peca que o cobre."""
    evento = _evento_em(ctx.old.events, frame)
    if evento is None or evento.kind != "clip" or evento.source_in is None:
        return None
    return _destino_do_frame(ctx, evento, frame)


def _emenda(ctx: _Ctx, frame) -> int | None:
    """Ponto de emenda, em samples, do trecho removido que contem o frame.

    A emenda e o fim no corte novo da ultima peca que termina antes do trecho
    no corte antigo, como o mapa de tempo faz com prev.new_out: e ali que o
    material removido entra no corte novo.
    """
    evento = _evento_em(ctx.old.events, frame)
    if evento is None or evento.kind != "clip" or evento.source_in is None:
        return None
    peca = ctx.pecas.peca_antes(frame)
    if peca is None:
        return _samples(ctx, ctx.new.start_frames)
    return _samples(ctx, peca.new_out)


def _fronteiras(ctx: _Ctx) -> list[int]:
    """Fronteiras, em samples, onde os pedacos do set precisam ser divididos.

    Alem das fronteiras dos eventos do corte antigo, entram as bordas das
    pecas do verify: o apply corta os clips nesses pontos, e o verify precisa
    conferir os mesmos pedacos.
    """
    pontos: set[int] = set()
    for evento in ctx.old.events:
        for frame in (evento.record_in, evento.record_out):
            pontos.add(_para_samples(ctx, frame))
    for peca in _interseccoes(ctx):
        for frame in (peca.old_in, peca.old_out):
            pontos.add(_para_samples(ctx, frame))
    from .reconform_verify_cauda import fronteira_da_cauda

    cauda = fronteira_da_cauda(ctx)
    if cauda is not None:
        pontos.add(cauda)
    return sorted(pontos)


def _pedacos(clip: _Clip, fronteiras: list[int]) -> list[tuple[int, int]]:
    cortes = [clip.start] + [f for f in fronteiras if clip.start < f < clip.end] + [clip.end]
    return list(zip(cortes, cortes[1:]))


def _cobre(clip: _Clip, inicio: int, fim: int, loop: int) -> bool:
    """O clip cobre [inicio, fim) com a fonte certa no inicio."""
    if not (clip.start <= inicio and fim <= clip.end):
        return False
    return abs(clip.loop_start + (inicio - clip.start) - loop) <= _TOLERANCIA_LOOP


def _conferir_pedaco(ctx: _Ctx, nome: str, pedaco: tuple, aplicado: dict) -> None:
    inicio, fim, loop = pedaco
    tipo, novo = _oraculo(ctx, inicio)
    if tipo == "fora":
        return
    if tipo == "saiu":
        # A cauda que nao cabe no corte novo tem emenda propria: ela e
        # conferida pelo modulo da cauda, e nao pelo das pecas removidas.
        from .reconform_verify_cauda import conferir_cauda, e_cauda

        if e_cauda(ctx, inicio):
            conferir_cauda(ctx, nome, pedaco, aplicado)
            return
        # O modulo de saiu importa este aqui: a importacao fica dentro da
        # funcao para nao fechar o ciclo entre os dois.
        from .reconform_verify_saiu import conferir_saiu

        conferir_saiu(ctx, nome, pedaco, aplicado)
        return
    if not any(_cobre(c, novo, novo + (fim - inicio), loop) for c in aplicado.get(nome, [])):
        ctx.problema(
            "RECONFORM_VERIFY_POSITION", nome, f"piece from {inicio} expected at {novo} not found"
        )


def _base_saiu(nome: str) -> str | None:
    achado = _SAIU_RE.match(nome)
    return achado.group("base") if achado else None


def _conferir_posicoes(ctx: _Ctx, origem: dict, aplicado: dict) -> int:
    fronteiras = _fronteiras(ctx)
    total = 0
    for nome, clips in origem.items():
        for clip in clips:
            total += 1
            for inicio, fim in _pedacos(clip, fronteiras):
                loop = clip.loop_start + (inicio - clip.start)
                _conferir_pedaco(ctx, nome, (inicio, fim, loop), aplicado)
    return total


def _conferir_fades(ctx: _Ctx, origem: dict, aplicado: dict) -> None:
    for nome, clips in aplicado.items():
        base = _base_saiu(nome) or nome
        inicios = {c.loop_start for c in origem.get(base, [])}
        fins = {c.loop_start + (c.end - c.start) for c in origem.get(base, [])}
        for clip in clips:
            fim_fonte = clip.loop_start + (clip.end - clip.start)
            if clip.fade_in > 0 and not _perto(clip.loop_start, inicios):
                ctx.problema("RECONFORM_VERIFY_FADE", nome, f"fade-in on inner piece {clip.start}")
            if clip.fade_out > 0 and not _perto(fim_fonte, fins):
                ctx.problema("RECONFORM_VERIFY_FADE", nome, f"fade-out on inner piece {clip.start}")
            if clip.crossfade_in != "0" and not _perto(clip.loop_start, inicios):
                ctx.problema(
                    "RECONFORM_VERIFY_FADE", nome, f"crossfade on sliced piece {clip.start}"
                )


def _perto(valor: int, conjunto: set[int]) -> bool:
    return any(abs(valor - item) <= _TOLERANCIA_LOOP for item in conjunto)


def _conferir_saiu_mudas(ctx: _Ctx, root: ET.Element) -> None:
    for node in _tracks(root):
        nome = track_name(node)
        if _base_saiu(nome) is None:
            continue
        if get_value(node, TRACK_FIELDS["mute"]) != "false":
            ctx.problema("RECONFORM_VERIFY_SAIU_UNMUTED", nome, "the [saiu] track plays")
        alvo = node.find("DeviceChain/Mixer/Speaker/AutomationTarget")
        alvo_id = alvo.get("Id") if alvo is not None else None
        for envelope in node.findall("AutomationEnvelopes/Envelopes/AutomationEnvelope"):
            if alvo_id is not None and get_value(envelope, "EnvelopeTarget/PointeeId") == alvo_id:
                ctx.problema("RECONFORM_VERIFY_SAIU_UNMUTED", nome, "Speaker automation copied")


def _conferir_documento(ctx: _Ctx, root: ET.Element) -> None:
    for locator in root.findall(".//LiveSet/Locators/Locators/Locator"):
        nome = get_value(locator, "Name") or ""
        if nome.startswith("TC ") and float(get_value(locator, "Time") or "0") != 0.0:
            ctx.problema("RECONFORM_VERIFY_ANCHOR_LOCATOR", "", f"{nome!r} left beat 0")
    try:
        validate_document(root, classify_id_tags(root)[0])
    except DocumentInvalid as exc:
        ctx.problema("RECONFORM_VERIFY_DOCUMENT", "", str(exc))


def _sha256(caminho: Path) -> str:
    return hashlib.sha256(caminho.read_bytes()).hexdigest()


def verify_reconform(
    old: CutIR,
    new: CutIR,
    source_set: str | Path,
    applied_set: str | Path,
    report: Report,
    *,
    source_sha256: str | None = None,
    source_sidecar_sha256: str | None = None,
) -> VerifyResult:
    """Confere a copia aplicada contra o oraculo pelos eventos dos dois cortes."""
    # O modulo extra importa este aqui: a importacao fica dentro da funcao para
    # nao fechar o ciclo entre os dois.
    from .reconform_verify_extra import Conference, conferir_extra

    origem_p, aplicado_p = Path(source_set), Path(applied_set)
    anchor_o, anchor_a = read_anchor(origem_p), read_anchor(aplicado_p)
    root_o, root_a = load_als(origem_p), load_als(aplicado_p)
    # As pecas do verify sao montadas uma vez: elas sao o unico predicado de
    # correspondencia de todo o oraculo.
    ctx = _Ctx(
        old=old,
        new=new,
        sample_rate=anchor_o.sample_rate,
        pecas=montar_pecas(old, new),
    )
    origem = _clips_por_track(root_o, anchor_o)
    aplicado = _clips_por_track(root_a, anchor_a)
    total = _conferir_posicoes(ctx, origem, aplicado)
    _conferir_fades(ctx, origem, aplicado)
    _conferir_saiu_mudas(ctx, root_a)
    from .reconform_verify_sobreposicao import conferir_sobreposicao

    conferir_sobreposicao(ctx, aplicado)
    _conferir_documento(ctx, root_a)
    if source_sha256 is not None and _sha256(origem_p) != source_sha256:
        ctx.problema("RECONFORM_VERIFY_SOURCE_CHANGED", "", "the source set changed")
    conferir_extra(
        Conference(ctx, root_o, root_a, origem_p, aplicado_p, anchor_o, anchor_a),
        source_sidecar_sha256,
    )
    if ctx.problems:
        report.error(
            "RECONFORM_VERIFY_FAILED",
            f"{len(ctx.problems)} reconform check(s) failed",
            {"problems": ctx.problems},
        )
    return VerifyResult(ok=not ctx.problems, checked_clips=total, problems=ctx.problems)
