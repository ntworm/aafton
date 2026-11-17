"""Opcoes de organizacao da sessao na linha de comando (import e inspect).

``--organize type`` liga a organizacao por tipo e familia de nome; ``prefix`` e
o agrupamento por prefixo de hoje, igual a ``--groups prefix``. O ``inspect
--organize type`` imprime a proposta e as edicoes em JSON; o ``import
--organize-plan`` le esse JSON com as edicoes feitas a mao e aplica.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from collections.abc import Iterable
from pathlib import Path

SINAIS = ("file", "meta", "name", "family", "color", "compact")
NIVEIS = ("permissive", "balanced", "strict")


def com_compact(sinais: Iterable[str]) -> tuple[str, ...]:
    """Os sinais recebidos com a compactacao ligada.

    A compactacao nao tem controle na pagina do Studio: ela entra sempre. O
    CLI desliga a compactacao quando o sinal falta em --organize-signals.
    """
    lista = [str(s) for s in sinais]
    if "compact" not in lista:
        lista.append("compact")
    return tuple(lista)


class OrganizeArgsError(ValueError):
    """Combinacao de opcoes de organizacao que nao faz sentido."""


def _sinais(texto: str) -> tuple[str, ...]:
    """Lista separada por virgula dos sinais ligados."""
    sinais = tuple(s.strip() for s in texto.split(",") if s.strip())
    desconhecidos = [s for s in sinais if s not in SINAIS]
    if desconhecidos:
        raise argparse.ArgumentTypeError(
            f"unknown organize signal(s): {', '.join(desconhecidos)}; use {', '.join(SINAIS)}"
        )
    return sinais


def _add_comuns(parser: argparse.ArgumentParser) -> None:
    """Certeza e sinais, iguais no import e no inspect."""
    parser.add_argument(
        "--certainty",
        choices=list(NIVEIS),
        default="permissive",
        help="how sure aafton must be to move a clip to a track of its type (default: permissive)",
    )
    parser.add_argument(
        "--organize-signals",
        type=_sinais,
        default=SINAIS,
        metavar="LIST",
        help=f"comma list of the signals to use: {','.join(SINAIS)} (default: all)",
    )


def add_organize_import_args(p_import: argparse.ArgumentParser) -> None:
    """Opcoes de organizacao do import."""
    p_import.add_argument(
        "--organize",
        choices=["off", "prefix", "type"],
        default="off",
        help="type: groups, renames and colours tracks by what they hold; "
        "prefix: groups by name prefix, like --groups prefix (default: off)",
    )
    _add_comuns(p_import)
    p_import.add_argument(
        "--organize-plan",
        default=None,
        metavar="FILE",
        help="JSON printed by inspect --organize type, with its edits changed by hand",
    )
    p_import.set_defaults(organize_edits=None)


def add_organize_inspect_args(p_inspect: argparse.ArgumentParser) -> None:
    """Opcoes de organizacao do inspect."""
    p_inspect.add_argument(
        "--organize",
        choices=["type"],
        default=None,
        help="prints the organize proposal and the editable edits as JSON",
    )
    _add_comuns(p_inspect)
    p_inspect.add_argument(
        "--stereo",
        choices=["pair", "split"],
        default="pair",
        help="stereo mode the proposal assumes (default: pair)",
    )


def _ler_plano(caminho: str) -> dict:
    """Le o JSON do inspect --organize; erro claro quando nao serve."""
    try:
        dados = json.loads(Path(caminho).read_text(encoding="utf-8"))
        proposta = dados["proposal"]
        return {
            "edits": dados.get("edits") or {},
            "level": proposta["level"],
            "signals": tuple(proposta["signals"]),
            "stereo": proposta["stereo"],
        }
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise OrganizeArgsError(f"cannot read the organize plan {caminho}: {exc}") from exc


def resolve_organize(args: argparse.Namespace) -> dict:
    """Campos de ImportOptions vindos das opcoes de organizacao e de --groups.

    ``--groups prefix`` vale ``--organize prefix``; as duas opcoes em conflito
    sao recusadas.
    """
    organize = args.organize
    if args.groups == "prefix":
        if organize == "type":
            raise OrganizeArgsError("--groups prefix and --organize type cannot be used together")
        organize = "prefix"
    campos = {
        "organize": organize,
        "groups": "prefix" if organize == "prefix" else "none",
        "certainty": args.certainty,
        "organize_signals": tuple(args.organize_signals),
        "organize_edits": None,
        "stereo": args.stereo,
    }
    if args.organize_plan:
        if organize == "prefix":
            raise OrganizeArgsError("--organize-plan cannot be used with --organize prefix")
        plano = _ler_plano(args.organize_plan)
        campos.update(
            organize="type",
            groups="none",
            certainty=plano["level"],
            organize_signals=plano["signals"],
            organize_edits=plano["edits"],
            stereo=plano["stereo"],
        )
    return campos


def inspect_organize(args: argparse.Namespace) -> int:
    """Imprime a proposta de organizacao de um AAF e as edicoes vazias."""
    from .organize_plan import Edits, build_proposal
    from .organize_read import read_for_proposal

    with tempfile.TemporaryDirectory(prefix="aafton-inspect-") as tmp:
        # A midia se resolve e a embarcada se extrai como no import, para o sinal
        # de metadados ver os WAVs; a pasta vive ate a proposta ficar pronta.
        session = read_for_proposal(
            args.input,
            Path(tmp),
            audio_dir=args.audio_dir,
            edl_rate=getattr(args, "edl_rate", None),
        )
        proposta = build_proposal(
            session,
            stereo=args.stereo,
            level=args.certainty,
            signals=tuple(args.organize_signals),
        )
    # O mesmo escritor UTF-8 do inspect: o JSON redirecionado num console do
    # Windows nao pode cair na pagina de codigo do sistema.
    from .cli import _print_json

    _print_json(
        {"proposal": json.loads(proposta.to_json()), "edits": json.loads(Edits().to_json())}
    )
    return 0
