"""Sonda do oraculo de refatoracao: grava a resposta de GET / do Studio.

Uso: python refactor_oracle_studio.py WORK (PYTHONPATH aponta para o src da arvore).
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def worker_studio(work: Path, env: dict[str, str] | None) -> None:
    """Consulta a pagina inicial do Studio e grava a resposta em disco."""
    porta = _porta_livre()
    raiz = Path((env or {}).get("PYTHONPATH", "src"))
    nome = "aafton" if (raiz / "aafton").is_dir() else "ableton_aaf"
    comando = [sys.executable, "-m", f"{nome}.cli", "ui", "--port", str(porta), "--no-browser"]
    devnull = subprocess.DEVNULL
    processo = subprocess.Popen(comando, env=env, cwd=str(work), stdout=devnull, stderr=devnull)
    try:
        texto = _consulta_studio(porta)
        (work / "studio-get-root.txt").write_text(texto, encoding="utf-8")
    finally:
        processo.terminate()
        try:
            processo.wait(timeout=20)
        except subprocess.TimeoutExpired:
            processo.kill()
            processo.wait(timeout=10)


def _porta_livre() -> int:
    """Reserva uma porta TCP livre no loopback."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _consulta_studio(porta: int) -> str:
    """Espera o Studio responder e devolve a resposta formatada."""
    limite = time.monotonic() + 20
    ultimo = "sem resposta"
    while time.monotonic() < limite:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{porta}/", timeout=5) as resp:
                return _texto_da_resposta(resp)
        except urllib.error.HTTPError as erro:
            return _texto_da_resposta(erro)
        except (urllib.error.URLError, OSError) as erro:
            ultimo = f"{type(erro).__name__}: {erro}"
        time.sleep(0.25)
    return f"erro ao consultar o Studio: {ultimo}\n"


def _texto_da_resposta(resp: Any) -> str:
    """Formata a resposta HTTP do Studio, sem o cabecalho Date."""
    cabecalhos = sorted((n, v) for n, v in resp.headers.items() if n.lower() != "date")
    corpo = resp.read().decode("utf-8", errors="replace")
    status = f"HTTP/1.{resp.version} {resp.status} {resp.reason}"
    return "\n".join([status] + [f"{n}: {v}" for n, v in cabecalhos] + ["", corpo]) + "\n"


if __name__ == "__main__":
    worker_studio(Path(sys.argv[1]), None)
