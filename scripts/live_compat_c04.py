"""Caso C04 do oraculo diferencial do Live: multicanal e estereo agrupados.

A pergunta do C04 e se as tracks 5.1 e ambisonicas aparecem agrupadas por
canal, um canal por track. O AAF nao carrega as pastas do Live: o agrupamento
so e reconstruido quando a conversao o pede, e o unico jeito de pedir e o
``--groups prefix``. Sem esse pedido o caso mediria o padrao (nenhum grupo) e
nao a pergunta.

Por isso o C04 nao usa o construtor generico das fixtures: ele passa o
``--groups prefix`` no import.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from aafton.live_compat import Conversao, converter_fixture  # noqa: E402

FIXTURE = "TESTE_AFF_PROTOOLS"
GRUPOS = "prefix"


def caso_c04(pasta: Path) -> Conversao:
    """Converte a fixture multicanal do Pro Tools pedindo o agrupamento.

    A conversao e isolada: uma copia da fixture, com as portadoras dentro da
    pasta do caso. O pacote nao depende do arquivo que mora fora dele.
    """
    return converter_fixture(FIXTURE, pasta, cheio=True, extras=("--groups", GRUPOS), isolada=True)
