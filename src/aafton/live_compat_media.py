"""Midia local do pacote do Live: copia isolada do AAF e inventario de proveniencia.

O pacote do operador tem de ser autocontido: o ``.als`` que ele entrega nao
pode apontar para um arquivo que mora fora do pacote -- o Desktop de quem
montou a fixture, o ``D:`` da maquina do Pro Tools. Este modulo faz a **copia
isolada**: uma copia do AAF com as URLStrings de audio apontando para
portadoras locais, e as portadoras ao lado, com o comprimento que os clips
pedem.

Nada aqui mexe na fixture original, na midia externa, no resolvedor global de
midia nem no escritor de AAF. A mudanca acontece na copia, aberta com pyaaf2
em modo de escrita, e so nas URLStrings de audio cujo nome bate com uma
portadora local: o MasterMob, seus slots, os canais e a geometria da essencia
ficam como estavam.

O inventario (``midias_proveniente``) amarra, por hash, as midias que o ``.als``
gerado referencia de verdade. A lista sai do leitor de caminho do proprio
produto (``als_read._media_path``): nao ha semantica nova de XML aqui.
"""

from __future__ import annotations

import shutil
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from .aaf_read import read_aaf
from .aaf_read_components import _url_para_caminho
from .als_read import _media_path
from .als_xml import load_als
from .ir import MediaAsset, SessionIR
from .report import Report
from .collect_aaf import iter_descriptors

# Orcamento de compatibilidade com o MAX_PATH legado do Windows: 259
# caracteres, contados em unidades UTF-16. E um orcamento conservador, para
# o app que nao pediu caminhos longos: nao e o limite do sistema de arquivos
# nem prova da causa do offline do C03.
LIMITE_WINDOWS = 259


class MidiaLocalInvalida(RuntimeError):
    """A midia local nao pode ser preparada: colisao, escape ou midia que falta."""


@dataclass(frozen=True)
class Portadora:
    """A portadora local que substitui um arquivo de audio do AAF."""

    nome: str
    canais: int
    taxa: int
    amostras: int


def chave_do_nome(nome: str) -> str:
    """A chave de agrupamento: o nome do arquivo, sem pasta e sem caixa."""
    return Path(nome).name.casefold()


def agrupar_portadoras(sessao: SessionIR, *, cheio: bool = True) -> dict[str, Portadora]:
    """Uma portadora por nome de arquivo, com o comprimento que os clips pedem.

    Os assets que temem o mesmo nome de arquivo dividem uma portadora: o mesmo
    arquivo nao pode ter duas taxas nem duas contagens de canais. Quando
    dividem, a colisao e recusada em vez de escolher uma em silencio.
    """
    grupos: dict[str, list[MediaAsset]] = {}
    for asset in sessao.media:
        if not asset.filename:
            continue
        grupos.setdefault(chave_do_nome(asset.filename), []).append(asset)
    return {chave: _portadora(assets, sessao, cheio) for chave, assets in grupos.items()}


def _portadora(assets: list[MediaAsset], sessao: SessionIR, cheio: bool) -> Portadora:
    """A portadora de um grupo de assets: nome, canais, taxa e comprimento."""
    from .live_compat import _amostras_da_portadora

    canais = {asset.channels for asset in assets if asset.channels is not None}
    taxas = {asset.sample_rate for asset in assets if asset.sample_rate is not None}
    if len(canais) > 1 or len(taxas) > 1:
        raise MidiaLocalInvalida(
            "media with the same name and different rates or channels: "
            + ", ".join(sorted(asset.filename for asset in assets))
        )
    amostras = max(_amostras_da_portadora(sessao, asset, cheio) for asset in assets)
    return Portadora(
        nome=Path(assets[0].filename).name,
        canais=next(iter(canais), 1),
        taxa=next(iter(taxas), sessao.sample_rate or 48000),
        amostras=amostras,
    )


def preparar_copia_isolada(aaf: Path, pasta: Path, midia: Path, *, cheio: bool = True) -> Path:
    """Copia o AAF, aponta o audio para as portadoras locais e devolve a copia.

    A copia mora na pasta da conversao, em ``preparo``: e ela que o import le.
    A fixture original nao e tocada, e o AAF copiado so muda nas URLStrings de
    audio que apontam para uma portadora local.

    A pasta de preparo e o rascunho da conversao: e criada aqui, e quem apaga
    no fim do import apaga so o que esta chamada criou.
    """
    preparacao = Path(pasta) / "preparo"
    _criar_preparo(preparacao)
    copia = preparacao / Path(aaf).name
    shutil.copyfile(aaf, copia)
    sessao = read_aaf(copia, Report(strict=False))
    portadoras = agrupar_portadoras(sessao, cheio=cheio)
    _reescrever_locators(copia, portadoras, midia)
    _escrever_portadoras(portadoras, midia)
    return copia


def _criar_preparo(preparacao: Path) -> None:
    """Cria a pasta de preparo; a que ja existe e recusada, com o motivo.

    O preparo e o rascunho da conversao: quem apagou essa pasta no fim do
    import confia que ela e da chamada. Uma pasta que ja existia e de outra
    pessoa, e a conversao recusa antes de copiar ou escrever qualquer coisa.
    """
    try:
        preparacao.mkdir(parents=True, exist_ok=False)
    except FileExistsError as erro:
        raise MidiaLocalInvalida(
            f"the preparation folder already exists and cannot be reused: {preparacao}"
        ) from erro


def _reescrever_locators(copia: Path, portadoras: dict[str, Portadora], midia: Path) -> None:
    """Aponta as URLStrings de audio para a portadora local, no AAF copiado."""
    import aaf2

    with aaf2.open(str(copia), "rw") as arquivo:
        for mob in arquivo.content.sourcemobs():
            for descritor in iter_descriptors(getattr(mob, "descriptor", None)):
                _reescrever_descritor(descritor, portadoras, midia)


def _reescrever_descritor(descritor, portadoras: dict[str, Portadora], midia: Path) -> None:
    """Reescreve as URLStrings de um descritor que apontam para uma portadora."""
    locators = descritor.get("Locator") if hasattr(descritor, "get") else None
    if not locators:
        return
    for locator in locators.value:
        url = locator.get("URLString") if hasattr(locator, "get") else None
        if not url or not url.value:
            continue
        nome = Path(_url_para_caminho(url.value)).name
        portadora = portadoras.get(chave_do_nome(nome))
        if portadora is None:
            continue
        url.value = (Path(midia) / portadora.nome).resolve().as_uri()


def _escrever_portadoras(portadoras: dict[str, Portadora], midia: Path) -> list[Path]:
    """Escreve as portadoras locais: silencio com o nome, os canais e a taxa."""
    from .live_compat import _escrever_portadora

    pasta = Path(midia)
    pasta.mkdir(parents=True, exist_ok=True)
    escritas = []
    for portadora in portadoras.values():
        caminho = pasta / portadora.nome
        _escrever_portadora(caminho, portadora.canais, portadora.taxa, portadora.amostras)
        escritas.append(caminho)
    return escritas


def midias_efetivas(als: Path) -> list[Path]:
    """Os arquivos de audio que o .als referencia de verdade, sem repetir.

    A lista sai do leitor de caminho do proprio produto (``als_read._media_path``)
    e do XML do Live: nenhum campo novo e nenhuma semantica nova de XML.

    Um clip que tem FileRef mas nao tem Path nem RelativePath e recusado: a
    referencia ilegivel nao vira um inventario vazio que parece completo.
    """
    raiz = load_als(als)
    caminhos: list[Path] = []
    vistos: set[str] = set()
    for clip in raiz.iter("AudioClip"):
        if clip.find("SampleRef/FileRef") is None:
            continue
        caminho = _caminho_do_clip(clip, als)
        chave = str(caminho).casefold()
        if chave in vistos:
            continue
        vistos.add(chave)
        caminhos.append(caminho)
    return caminhos


def _caminho_do_clip(clip: ET.Element, als: Path) -> Path:
    """O caminho que o clip referencia; a referencia ilegivel e recusada."""
    try:
        return Path(_media_path(clip, als))
    except ValueError as erro:
        raise MidiaLocalInvalida(
            f"the .als {als} has an AudioClip with FileRef and no Path and no RelativePath: {erro}"
        ) from erro


def inventario_de_midias(pacote: Path, als: Path) -> list[dict]:
    """O inventario de proveniencia das midias que o .als gerado referencia.

    Cada entrada tem o caminho relativo ao pacote e o sha256 do conteudo. A
    midia tem de existir fisicamente dentro do pacote, e o caminho tem de caber
    no orcamento de compatibilidade do MAX_PATH legado: um pacote que o app
    legado do Windows nao abre nao e um pacote.
    """
    from .live_compat_proveniencia import sha256_arquivo

    raiz = Path(pacote).resolve()
    entradas = []
    for caminho in midias_efetivas(als):
        alvo = Path(caminho).resolve()
        relativo = _relativo_no_pacote(raiz, alvo)
        if not alvo.is_file():
            raise MidiaLocalInvalida(f"the media {caminho} of the generated .als does not exist")
        _conferir_limite_windows(alvo)
        entradas.append({"caminho": relativo, "sha256": sha256_arquivo(alvo)})
    return entradas


def _relativo_no_pacote(raiz: Path, alvo: Path) -> str:
    """O caminho do alvo relativo a raiz do pacote, sem diferenciar caixa."""
    partes_raiz = [parte.casefold() for parte in raiz.parts]
    partes_alvo = [parte.casefold() for parte in alvo.parts]
    if partes_alvo[: len(partes_raiz)] != partes_raiz:
        raise MidiaLocalInvalida(f"the media {alvo} is outside the package")
    return Path(*alvo.parts[len(partes_raiz) :]).as_posix()


def _conferir_limite_windows(caminho: Path) -> None:
    """O caminho tem de caber no orcamento de compatibilidade do MAX_PATH.

    O orcamento e o do MAX_PATH legado do Windows, 259 unidades UTF-16: um
    orcamento conservador para o app que nao pediu caminhos longos, nao o
    limite do sistema de arquivos.
    """
    unidades = len(str(caminho).encode("utf-16-le")) // 2
    if unidades > LIMITE_WINDOWS:
        raise MidiaLocalInvalida(
            f"the media path has {unidades} units and goes over the compatibility budget "
            f"with the legacy Windows MAX_PATH ({LIMITE_WINDOWS}): {caminho}"
        )
