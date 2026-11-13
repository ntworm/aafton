"""Escrita de EDL CMX 3600 a partir do SessionIR, sem o OpenTimelineIO.

O escritor cmx_3600 do OpenTimelineIO recusa uma timeline sem track de video
e, com a track vazia, escreve so o TITLE e perde todo evento de audio. Por
isso o produto escreve o EDL com o proprio codigo, e o que a EDL nao carrega
vira erro no relatorio.

As conferencias que a EDL e o XML do FCP compartilham estao em
escrita_comum.py.
"""

from __future__ import annotations

import string
from dataclasses import dataclass
from pathlib import Path

from ..ir import ClipIR, SessionIR
from ..report import Report
from ..timecode import frames_to_label
from .escrita_comum import (
    Contexto,
    alocar_canais,
    avisar_arredondamento,
    conferir_multicanal,
    conferir_recursos,
    frame,
    gravar,
    origem,
)

LIMITE_EVENTOS = 999
# O reel aceita A-Z, 0-9 e o sublinhado; o resto vira sublinhado.
CARACTERES_REEL = frozenset(string.ascii_uppercase + string.digits + "_")
# Quantos caracteres do nome ficam no reel quando dois arquivos colidem.
CARACTERES_REEL_NUMERADO = 6


@dataclass
class _Evento:
    """Um evento de audio da EDL, com os tempos em frames."""

    canal: int
    record_in: int
    record_out: int
    source_in: int
    source_out: int
    reel: str
    nome_clip: str
    arquivo: str


def write_edl(session: SessionIR, destination: str | Path, report: Report) -> None:
    """Grava a sessao em um EDL CMX 3600."""
    conferir_recursos(
        session,
        report,
        "FORMAT_EDL_FEATURE_UNSUPPORTED",
        "the EDL",
        "FORMAT_EDL_MARKERS_DROPPED",
    )
    conferir_multicanal(session, report, "the EDL")
    contexto = Contexto(session=session, report=report)
    eventos = _eventos(contexto, _reels(session))
    if len(eventos) > LIMITE_EVENTOS:
        report.error(
            "FORMAT_EDL_TOO_MANY_EVENTS",
            f"the EDL accepts at most {LIMITE_EVENTOS} events and the session has {len(eventos)}",
        )
        return
    gravar(Path(destination), _texto(contexto, eventos))
    avisar_arredondamento(contexto)


def _eventos(contexto: Contexto, reels: dict[str, str]) -> list[_Evento]:
    """Eventos de audio da sessao, por inicio de registro e por canal."""
    eventos: list[_Evento] = []
    for canal in alocar_canais(contexto.session.tracks):
        for clip in canal.clips:
            evento = _evento(contexto, clip, canal.numero, reels)
            if evento is not None:
                eventos.append(evento)
    eventos.sort(key=lambda evento: (evento.record_in, evento.canal))
    return eventos


def _evento(contexto: Contexto, clip: ClipIR, canal: int, reels: dict[str, str]) -> _Evento | None:
    """Evento do clip no canal dado; None quando o arquivo do clip falta."""
    asset = contexto.session.media_by_id(clip.media_id) if clip.media_id else None
    if asset is None:
        contexto.report.error(
            "FORMAT_EDL_MEDIA_MISSING",
            f"the clip {clip.name!r} has no media file in the session",
            {"clip": clip.name},
        )
        return None
    rate = contexto.session.timecode_rate
    inicio = origem(contexto, asset)
    # Offset desconhecido vale zero: o comeco do arquivo e a origem da fonte.
    offset = clip.source_offset_samples or 0
    record_in = frame(contexto, clip.timeline_start_samples, rate)
    record_out = frame(contexto, clip.timeline_end_samples, rate)
    source_in = frame(contexto, inicio + offset, rate)
    return _Evento(
        canal=canal,
        record_in=record_in,
        record_out=record_out,
        source_in=source_in,
        # A duracao da fonte e a do registro: a EDL nao aceita duracoes diferentes.
        source_out=source_in + (record_out - record_in),
        reel=reels.get(asset.id) or _base_do_reel(asset.filename),
        nome_clip=clip.name,
        arquivo=asset.filename,
    )


def _reels(sessao: SessionIR) -> dict[str, str]:
    """Reel de cada midia da sessao: o nome curto, com numero quando colide.

    Dois arquivos com o mesmo comeco de nome nao podem usar o mesmo reel: o
    primeiro fica com o nome e os seguintes ganham o nome curto com um
    contador de dois digitos.
    """
    reels: dict[str, str] = {}
    donos: dict[str, str] = {}
    contadores: dict[str, int] = {}
    for asset in sessao.media:
        base = _base_do_reel(asset.filename)
        reel = base
        if base in donos:
            numero = contadores.get(base, 0) + 1
            while f"{base[:CARACTERES_REEL_NUMERADO]}{numero:02d}" in donos:
                numero += 1
            contadores[base] = numero
            reel = f"{base[:CARACTERES_REEL_NUMERADO]}{numero:02d}"
        donos[reel] = asset.id
        reels[asset.id] = reel
    return reels


def _base_do_reel(nome: str) -> str:
    """Base do reel: a do arquivo, em maiusculas e ate 8 caracteres."""
    base = Path(nome).stem.upper()
    limpo = "".join(caractere if caractere in CARACTERES_REEL else "_" for caractere in base)
    return limpo[:8] or "AX"


def _texto(contexto: Contexto, eventos: list[_Evento]) -> str:
    """Arquivo EDL completo: cabecalho, eventos e comentarios."""
    sessao = contexto.session
    fcm = "FCM: DROP FRAME" if sessao.timecode_rate.drop_frame else "FCM: NON-DROP FRAME"
    linhas = [f"TITLE: {sessao.name}", fcm, ""]
    for numero, evento in enumerate(eventos, start=1):
        linhas.append(_linha(contexto, numero, evento))
        linhas.append(f"* FROM CLIP NAME: {evento.nome_clip}")
        linhas.append(f"* SOURCE FILE: {evento.arquivo}")
        linhas.append("")
    return "\r\n".join(linhas) + "\r\n"


def _linha(contexto: Contexto, numero: int, evento: _Evento) -> str:
    """Linha do evento, com o timecode de origem e o de registro."""
    rate = contexto.session.timecode_rate
    src_in = frames_to_label(evento.source_in, rate)
    src_out = frames_to_label(evento.source_out, rate)
    rec_in = frames_to_label(evento.record_in, rate)
    rec_out = frames_to_label(evento.record_out, rate)
    canal = "A" if evento.canal == 1 else f"A{evento.canal}"
    cabecalho = f"{numero:03d}  {evento.reel:<8} {canal:<5} C        "
    tempos = f"{src_in} {src_out} {rec_in} {rec_out}"
    return cabecalho + tempos
