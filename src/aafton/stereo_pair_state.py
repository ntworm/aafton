"""Estado do emparelhamento estereo: o que um par recusado nao pode deixar.

O emparelhamento grava o WAV estereo e cria o asset da midia antes de saber
se o par inteiro deu certo: quando o segundo evento falha, a sessao fica com
o asset do primeiro evento, o clip apontando para ele e o cache de arquivos
gerados cheio. Este modulo fotografa o estado antes da tentativa e o restaura
depois, sem desfazer o que ja estava pronto.

O rollback e no lugar: a lista de midias, o dicionario do cache e os proprios
clips voltam a ser os mesmos objetos, com o mesmo conteudo. Os arquivos
gerados no disco ficam, porque eles podem ser reaproveitados pelo digest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .ir import ClipIR, MediaAsset, SessionIR, TrackIR


@dataclass
class _Fotografia:
    """O estado do par antes da tentativa."""

    nome: str
    canais: int
    clips: list[tuple[ClipIR, str | None, str]] = field(default_factory=list)
    media: list[MediaAsset] = field(default_factory=list)
    gerados: dict = field(default_factory=dict)


def _fotografar(session: SessionIR, t_l: TrackIR, generated_files: dict) -> _Fotografia:
    """O que precisa voltar ao lugar se o par for recusado."""
    return _Fotografia(
        nome=t_l.name,
        canais=t_l.channel_count,
        clips=[(clip, clip.media_id, clip.name) for clip in t_l.clips],
        media=list(session.media),
        gerados=dict(generated_files),
    )


def _restaurar(session: SessionIR, t_l: TrackIR, generated_files: dict, foto: _Fotografia) -> None:
    """Devolve a sessao ao estado fotografado, no lugar, sem trocar os objetos."""
    t_l.name = foto.nome
    t_l.channel_count = foto.canais
    for clip, media_id, nome in foto.clips:
        clip.media_id = media_id
        clip.name = nome
    session.media[:] = foto.media
    generated_files.clear()
    generated_files.update(foto.gerados)


def par_com_transacao(
    tentar: Callable[..., tuple[bool, int]],
    argumentos: tuple,
    session: SessionIR,
    t_l: TrackIR,
    generated_files: dict,
) -> tuple[bool, int]:
    """Chama ``tentar`` e desfaz o que ela deixou pela metade.

    ``tentar`` e o emparelhamento de um par: recebe os argumentos no formato
    dele e devolve (sucesso, arquivos escritos). Quando ela recusa o par ou
    levanta, a sessao, os clips e o cache voltam ao estado de antes. Os
    avisos do relatorio ficam: quem leu o relatorio continua vendo o motivo
    da recusa.
    """
    foto = _fotografar(session, t_l, generated_files)
    try:
        sucesso, escritos = tentar(*argumentos)
    except Exception:
        _restaurar(session, t_l, generated_files, foto)
        raise
    if not sucesso:
        _restaurar(session, t_l, generated_files, foto)
    return sucesso, escritos
