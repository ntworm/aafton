"""Recursos que o aafton carrega dentro do proprio pacote.

O template do Live e um dado do produto: ele viaja com o pacote instalado e
nao com a pasta do codigo. Todo caminho que procura um template usa o do
pacote quando o pedido nao nomeia nenhum.
"""

from __future__ import annotations

from importlib.resources import files


def default_template() -> str:
    """The path of the Live template that comes inside the package.

    The path is absolute and points to the file of the installed package, so
    the conversion does not depend on the folder the command runs from.
    """
    return str(files("aafton").joinpath("templates").joinpath("live12.als"))
