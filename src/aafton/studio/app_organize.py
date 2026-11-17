"""Organizacao da sessao para a pagina do Studio (AAF para Live).

Responde a rota POST /api/app/organize calculando a proposta de organizacao
em memoria a partir da fonte lida, com cache para evitar releituras do AAF.
"""

from __future__ import annotations

import copy
import shutil
import tempfile
from pathlib import Path

from ..cli_organize import com_compact
from ..formats import ADAPTADORES
from ..ir import SessionIR
from ..organize_read import read_for_proposal
from ..organize_plan import DEFAULT_PLAN_SIGNALS, Edits, Proposal, build_proposal
from ..timecode import TimecodeRate
from .app_messages import falha_do_motor, mensagem_de_arquivo
from .app_read import error, taxa_edl

# Cache em memoria da ultima sessao lida: chave -> (sessao, tracks_excluidas).
_CACHE: dict[tuple, tuple[SessionIR, set[int]]] = {}
_PASTAS: dict[tuple, Path] = {}


def clear_cache() -> None:
    """Limpa o cache em memoria da sessao lida e as pastas de trabalho."""
    _limpar_cache()


def _obter_sessao(
    caminho: Path,
    pasta_audio: str | None,
    pasta_gravador: str | None,
    taxa: TimecodeRate | None,
    taxa_str: str | None,
) -> tuple[SessionIR, set[int]]:
    """Le a sessao ou recupera do cache em memoria."""
    chave = (
        str(caminho.resolve()),
        caminho.stat().st_mtime_ns,
        pasta_audio,
        pasta_gravador,
        taxa_str,
    )
    if chave in _CACHE:
        sessao, excluidas = _CACHE[chave]
        return copy.deepcopy(sessao), set(excluidas)

    # A pasta de trabalho guarda a midia extraida enquanto a sessao estiver no
    # cache: a proposta le o iXML desses WAVs a cada pedido.
    pasta = Path(tempfile.mkdtemp(prefix="aafton-organize-"))
    sessao = read_for_proposal(caminho, pasta, audio_dir=pasta_audio, edl_rate=taxa)

    excluidas: set[int] = set()
    if pasta_gravador and Path(pasta_gravador).is_dir():
        from ..expandir import planejar

        plano = planejar(sessao, pasta_gravador)
        excluidas = {take.dona.source_index for take in plano.takes.values() if take.faltando}

    if len(_CACHE) >= 4:
        _limpar_cache()
    _CACHE[chave] = (sessao, excluidas)
    _PASTAS[chave] = pasta
    return copy.deepcopy(sessao), set(excluidas)


def _limpar_cache() -> None:
    """Esvazia o cache e apaga as pastas de trabalho das sessoes guardadas."""
    for pasta in _PASTAS.values():
        shutil.rmtree(pasta, ignore_errors=True)
    _PASTAS.clear()
    _CACHE.clear()


def _calcular_resumo(proposta: Proposal, edicoes: Edits) -> dict[str, int]:
    """Calcula a contagem de tracks, grupos, clips movidos, retidos e editados."""
    grupos = len(proposta.groups)
    tracks = sum(len(g.tracks) for g in proposta.groups)
    movidos = len(proposta.moved_origin_keys())
    retidos = sum(
        len(u.origin_keys)
        for g in proposta.groups
        for t in g.tracks
        for u in t.units
        if not u.accepted
    ) + sum(
        len(u.origin_keys) for left in proposta.left_tracks for u in left.units if not u.accepted
    )
    editados = len(edicoes.origins)
    return {
        "tracks": tracks,
        "groups": grupos,
        "moved": movidos,
        "stay": retidos,
        "edited": editados,
    }


def _validar_origem(payload: dict) -> tuple[Path | None, TimecodeRate | None, dict | None]:
    """Valida o caminho de entrada e a taxa da EDL."""
    origem = str(payload.get("input") or "").strip()
    if not origem:
        return None, None, error("bad_request", "The source path is required.")
    caminho = Path(origem)
    if not caminho.exists():
        return (
            None,
            None,
            error("not_found", mensagem_de_arquivo(FileNotFoundError(), caminho, "read")),
        )
    sufixo = caminho.suffix.lower()
    if sufixo != ".aaf" and sufixo not in ADAPTADORES:
        return (
            None,
            None,
            error("unsupported", f"The format {sufixo} is not read by this direction."),
        )
    taxa, problema = taxa_edl(payload.get("edl_rate"))
    if problema:
        return None, None, error("bad_request", problema)
    return caminho, taxa, None


def _texto(payload: dict, nome: str) -> str | None:
    """Texto do pedido sem espacos, ou None quando vazio."""
    return str(payload.get(nome) or "").strip() or None


def _pastas(payload: dict) -> tuple[str | None, str | None]:
    """Pasta extra de midia e pasta do gravador."""
    return _texto(payload, "audio_dir"), _texto(payload, "production_audio")


def _parametros(payload: dict) -> tuple[str, str, tuple, Edits]:
    """Modo estereo, nivel, sinais e edicoes do pedido."""
    sinais = payload.get("signals")
    edicoes = payload.get("edits")
    return (
        str(payload.get("stereo") or "pair"),
        str(payload.get("certainty") or "permissive"),
        _sinais(sinais),
        Edits.from_dict(edicoes if isinstance(edicoes, dict) else None),
    )


def _sinais(recebidos: object) -> tuple[str, ...]:
    """Os sinais do pedido, sempre com a compactacao ligada.

    A pagina nao tem controle da compactacao: ela entra em toda proposta.
    """
    if recebidos is None:
        return tuple(sorted(DEFAULT_PLAN_SIGNALS))
    return com_compact(str(s) for s in recebidos)


def organize(payload: dict) -> dict:
    """Calcula a proposta de organizacao e o resumo para o Studio."""
    caminho, taxa, err = _validar_origem(payload)
    if err is not None:
        return err
    assert caminho is not None

    try:
        sessao, excluidas = _obter_sessao(
            caminho, *_pastas(payload), taxa, _texto(payload, "edl_rate")
        )
    except Exception as exc:  # noqa: BLE001 - a falha do motor e a resposta
        return error("unreadable", falha_do_motor(exc, caminho, "read"))

    try:
        stereo, certainty, signals, edicoes = _parametros(payload)
        proposta = build_proposal(
            sessao,
            stereo=stereo,
            level=certainty,
            signals=signals,
            edits=edicoes,
            excluded_source_indices=excluidas,
        )
    except ValueError as exc:
        return error("bad_request", str(exc))

    return {
        "ok": True,
        "proposal": proposta.to_dict(),
        "summary": _calcular_resumo(proposta, edicoes),
    }
