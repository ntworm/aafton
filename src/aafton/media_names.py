"""Nomes de midia que cabem no orcamento legado de caminho do Windows.

O Windows conta o caminho absoluto em unidades UTF-16, e o orcamento legado
do MAX_PATH e de 259. Acima desse numero o caminho sai do orcamento: nao e
um limite universal de sistema de arquivos (com o caminho longo habilitado o
Windows aceita mais), e sim um orcamento conservador que este modulo mantem
por compatibilidade. Aqui so o prefixo legivel e encurtado; o digest nunca e
cortado.

A politica e conservadora: o que este modulo garante e esse orcamento legado,
que e medido aqui, e ele existe por compatibilidade com o Windows e com o
Live. A causa proposta para o C03 (o nome longo do caminho) segue como
hipotese, sem reteste, e nada aqui certifica o comportamento do Live.
"""

import re
from pathlib import Path

MAX_PATH_UNITS = 259
MAX_COMPONENT_UNITS = 255

_ILEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class MediaNameTooLongError(ValueError):
    """O caminho final nao cabe nem sem prefixo legivel."""


def utf16_len(texto: str) -> int:
    """Unidades UTF-16 do texto (a unidade que o Windows conta no caminho)."""
    return sum(2 if ord(ch) > 0xFFFF else 1 for ch in texto)


def truncate_utf16(texto: str, maximo: int) -> str:
    """Corta em maximo unidades UTF-16, sem partir par substituto."""
    partes = []
    usado = 0
    for ch in texto:
        unidades = 2 if ord(ch) > 0xFFFF else 1
        if usado + unidades > maximo:
            break
        partes.append(ch)
        usado += unidades
    return "".join(partes)


def stereo_output_name(extract_dir: Path, prefix: str, digest: str) -> str:
    """Nome do WAV estereo: digest inteiro e prefixo curto quando preciso.

    Preserva byte a byte o padrao atual enquanto o caminho couber no MAX_PATH.
    """
    if len(digest) != 64 or not set(digest) <= set("0123456789abcdef"):
        raise ValueError("digest expected in hexadecimal with 64 characters")

    sufixo = f"_{digest}.wav"
    pasta = Path(extract_dir).resolve()
    fixo = utf16_len(str(pasta)) + 1 + utf16_len(sufixo)
    orcamento = min(MAX_PATH_UNITS - fixo, MAX_COMPONENT_UNITS - utf16_len(sufixo))
    if orcamento < 0:
        raise MediaNameTooLongError(
            f"the final path in {pasta} does not fit in {MAX_PATH_UNITS} UTF-16 units "
            "even without a prefix: use a shorter extraction folder"
        )

    # Nome de clip é dado externo; identidade vem dos bytes resultantes.
    limpo = _ILEGAL.sub("_", prefix).strip(". ")[:64] or "stereo"
    if utf16_len(limpo) > orcamento:
        limpo = truncate_utf16(limpo, orcamento).rstrip(". ")
    return f"{limpo}{sufixo}"


_EXTENSOES_DE_AUDIO = (".wav", ".wave", ".bwf", ".aif", ".aiff")


def sem_extensao_de_audio(nome: str | None) -> str | None:
    """Tira a extensao de audio do nome do MasterMob.

    O export do aafton e outros programas chamam o MasterMob de ``X.wav``; a
    essencia extraida ja ganha ``.wav``, e sem isto o arquivo sairia ``X.wav.wav``.
    """
    if not nome:
        return nome
    for extensao in _EXTENSOES_DE_AUDIO:
        if nome.lower().endswith(extensao) and len(nome) > len(extensao):
            return nome[: -len(extensao)]
    return nome
