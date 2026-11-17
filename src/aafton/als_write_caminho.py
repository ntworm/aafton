"""Caminho da midia relativo a pasta do set, como o Live grava depois do Save As.

O Live relativiza a midia que esta dentro da pasta do projeto: o ``FileRef``
passa a apontar pelo ``RelativePath`` e o ``RelativePathType`` vira 1. Uma pasta
do set copiada para outro lugar continua achando os arquivos que ela leva
junto. A midia de fora, o caso do ``--media link``, continua com o caminho
absoluto, porque o Live precisa acha-la no lugar de sempre.
"""

import os
import xml.etree.ElementTree as ET
from pathlib import Path

from .als_xml import set_value

# O tipo 1 do Live: o RelativePath e lido relativo a pasta do .als.
TIPO_RELATIVO_AO_PROJETO = 1


def aplicar_caminho_relativo(
    node: ET.Element,
    resolved_path: str,
    als_dir: Path | None,
) -> None:
    """Escreve o caminho relativo da midia quando ela esta dentro da pasta do set.

    Fora da pasta do set (ou sem pasta do set) o ``FileRef`` fica como esta.
    O ``Path`` absoluto tambem fica: e ele que o Live consulta quando o set
    esta no lugar, e o leitor que precisa do endereco real do arquivo.
    """
    if als_dir is None or not resolved_path:
        return
    relativo = _caminho_relativo(resolved_path, als_dir)
    if relativo is None:
        return
    file_ref = node.find("SampleRef/FileRef")
    if file_ref is None:
        return
    if file_ref.find("RelativePathType") is None or file_ref.find("RelativePath") is None:
        return
    set_value(node, "SampleRef/FileRef/RelativePathType", TIPO_RELATIVO_AO_PROJETO)
    set_value(node, "SampleRef/FileRef/RelativePath", relativo)


def _caminho_relativo(resolved_path: str, als_dir: Path) -> str | None:
    """Endereco da midia relativo a pasta do set, com barra normal.

    None quando a midia esta fora da pasta do set (o caso do --media link) ou
    no mesmo lugar dela.
    """
    try:
        alvo = Path(resolved_path).resolve()
        base = als_dir.resolve()
    except (OSError, ValueError):
        return None
    relativo = _dentro_de(alvo, base)
    if relativo is None:
        return None
    return relativo.as_posix()


def _dentro_de(alvo: Path, base: Path) -> Path | None:
    """Caminho do alvo dentro da base, quando ele esta dentro dela."""
    partes_alvo = alvo.parts
    partes_base = base.parts
    if len(partes_alvo) <= len(partes_base):
        return None
    if not _mesmas_partes(partes_alvo[: len(partes_base)], partes_base):
        return None
    return Path(*partes_alvo[len(partes_base) :])


def _mesmas_partes(esquerda: tuple[str, ...], direita: tuple[str, ...]) -> bool:
    """Compara as partes do caminho com a regra de maiuscula do sistema."""
    return all(os.path.normcase(a) == os.path.normcase(b) for a, b in zip(esquerda, direita))
