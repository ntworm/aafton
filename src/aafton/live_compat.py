"""Oraculo diferencial do Live: material, conversao e metrica de perfeicao (R07.6).

A Onda 1 mede o que o Live faz com o .als do aafton. Este modulo e a parte que
roda sem ninguem: escreve as midias-portadoras que deixam a conversao seguir o
caminho completo da CLI sem o corpus real, converte uma fixture e calcula a
metrica unica de perfeicao (E5):

    PERFEICAO = exatos / entrada - perdas_d

``entrada`` sao os SourceClips de audio do AAF que o ``verify`` espera,
``exatos`` os que chegam com desvio zero no inicio e no fim, e ``perdas_d`` as
perdas abertas de classe D (de Dropped): material que sumiu sem que nada o
declare. Antes do Live, sao os clips que o .als nao tem; depois do Live, sao as
diferencas ``DROPPED_POR_LIVE`` em campo que o aafton escreve (``als_compare``).
Cada perda aberta tira um ponto inteiro: uma so ja impede dizer "perfeito".

A perda e **cumulativa**: a coluna *depois* carrega a perda aberta de *antes*
e soma as novas. Devolver um .als igual ao gerado nao apaga a perda antiga,
porque o que voltou nao prova que o material voltou. A metrica mede geometria e
material que sumiu, nao equivalencia acustica nem exportacao perfeita.
"""

from __future__ import annotations

import aifc
import contextlib
import io
import shutil
import sys
import wave
from dataclasses import dataclass
from pathlib import Path

from .aaf_read import read_aaf
from .als_compare import Diferenca, identidade
from .bundled import default_template
from .report import Report
from .verify import VerifyResult, verify_conversion

FIXTURES_AAF = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "aaf"
TEMPLATE = Path(default_template())
SUFIXOS_AIFF = (".aif", ".aiff", ".aifc")


@dataclass(frozen=True)
class Fixture:
    nome: str
    dialeto: str
    session_text: str | None = None


# Fixtures versionadas em tests/fixtures/aaf, com o programa que as exportou.
FIXTURES: tuple[Fixture, ...] = (
    Fixture("dialect_protools", "Pro Tools"),
    Fixture("dialect_premiere", "Premiere"),
    Fixture("dialect_resolve", "Resolve"),
    Fixture("SPOT_AAF_TESTE", "Pro Tools", "SPOT_AAF_TESTE.txt"),
    Fixture("TESTE_AFF_PROTOOLS", "Pro Tools", "teste_aaf_protools_txt.txt"),
    Fixture("TESTE_DF_2997", "Pro Tools", "TESTE_DF_2997.txt"),
    Fixture("TESTE_DF_2997_correto", "Pro Tools", "TESTE_DF_2997_correto.txt"),
    Fixture("embarcado", "pyaaf2"),
)


def fixture(nome: str) -> Fixture:
    for item in FIXTURES:
        if item.nome == nome:
            return item
    raise KeyError(f"fixture not registered in live_compat.FIXTURES: {nome}")


def fixtures_disponiveis(pasta: Path | None = None) -> tuple[Fixture, ...]:
    """Retorna as fixtures cujo AAF existe na pasta informada (ou FIXTURES_AAF)."""
    alvo = pasta if pasta is not None else FIXTURES_AAF
    disponiveis: list[Fixture] = []
    for item in FIXTURES:
        if (alvo / f"{item.nome}.aaf").exists():
            disponiveis.append(item)
        else:
            print(f"fixture missing: {item.nome}", file=sys.stderr)
    return tuple(disponiveis)


def _amostras_da_portadora(sessao, asset, cheio: bool) -> int:
    """Uma amostra, ou o comprimento que os clips pedem da fonte."""
    if not cheio:
        return 1
    fins = [
        (clip.source_offset_samples or 0) + clip.duration_samples
        for clip in sessao.all_clips()
        if clip.media_id == asset.id
    ]
    return max(fins + [asset.duration_samples or 1])


def _escrever_portadora(caminho: Path, canais: int, taxa: int, amostras: int) -> None:
    silencio = bytes(2 * canais * amostras)
    if caminho.suffix.lower() in SUFIXOS_AIFF:
        with aifc.open(str(caminho), "wb") as handle:
            handle.setnchannels(canais)
            handle.setsampwidth(2)
            handle.setframerate(taxa)
            handle.setcomptype(b"NONE", b"")
            handle.writeframes(silencio)
        return
    with wave.open(str(caminho), "wb") as handle:
        handle.setnchannels(canais)
        handle.setsampwidth(2)
        handle.setframerate(taxa)
        handle.writeframes(silencio)


def escrever_portadoras(aaf: Path, pasta: Path, *, cheio: bool = False) -> list[Path]:
    """Silencio no lugar de cada midia do AAF, com o nome, os canais e a taxa dela.

    ``cheio=False`` escreve uma amostra por arquivo (estrutura, como nos testes de
    dialeto); ``cheio=True`` escreve o comprimento que os clips pedem, que e o que
    o Live precisa para nao encurtar clip nenhum.
    """
    sessao = read_aaf(aaf, Report(strict=False))
    pasta.mkdir(parents=True, exist_ok=True)
    escritas = []
    for asset in sessao.media:
        if not asset.filename or asset.status == "resolved":
            continue
        caminho = pasta / Path(asset.filename).name
        if caminho.exists():
            continue
        amostras = _amostras_da_portadora(sessao, asset, cheio)
        _escrever_portadora(caminho, asset.channels or 1, asset.sample_rate or 48000, amostras)
        escritas.append(caminho)
    return escritas


@dataclass(frozen=True)
class Conversao:
    aaf: Path
    als: Path
    codigo: int
    relatorio: str


def importar(
    aaf: Path,
    destino: Path,
    midia: Path,
    *,
    session_text: Path | None = None,
    extras: tuple[str, ...] = (),
) -> Conversao:
    """``aafton import`` pelo caminho completo da CLI, com o relatorio capturado.

    O AAF e copiado sozinho para uma pasta propria: a busca automatica de Session
    Text e de video olha ao lado dele.
    """
    from .cli import main

    origem = destino.parent / "origem"
    origem.mkdir(parents=True, exist_ok=True)
    copia = origem / aaf.name
    shutil.copyfile(aaf, copia)
    argumentos = ["import", str(copia), str(destino), "--template", str(TEMPLATE)]
    argumentos += ["--audio-dir", str(midia), "--media", "link", *extras]
    if session_text is not None:
        argumentos += ["--session-text", str(session_text)]
    saida = io.StringIO()
    with contextlib.redirect_stdout(saida):
        codigo = main(argumentos)
    return Conversao(copia, destino, codigo, saida.getvalue())


def converter_fixture(
    nome: str,
    pasta: Path,
    *,
    cheio: bool = False,
    extras: tuple[str, ...] = (),
    isolada: bool = False,
) -> Conversao:
    """Converte uma fixture versionada com as portadoras ao lado.

    ``extras`` sao argumentos extras do ``import`` da CLI. O padrao e nenhum:
    a conversao de uma fixture nao muda por causa deste modulo.

    ``isolada`` converte uma **copia** da fixture, com o audio apontado para
    portadoras locais dentro da pasta da conversao: o pacote nao depende de
    arquivo que mora fora dele. O padrao e ``False``: as oito fixtures, as
    propriedades e o oraculo continuam com a conversao de sempre.
    """
    item = fixture(nome)
    aaf = FIXTURES_AAF / f"{nome}.aaf"
    midia = pasta / "midia"
    texto = FIXTURES_AAF / item.session_text if item.session_text else None
    if isolada:
        return _converter_isolada(nome, aaf, pasta, midia, cheio, texto, extras)
    escrever_portadoras(aaf, midia, cheio=cheio)
    return importar(aaf, pasta / f"{nome}.als", midia, session_text=texto, extras=extras)


def _converter_isolada(
    nome: str,
    aaf: Path,
    pasta: Path,
    midia: Path,
    cheio: bool,
    texto: Path | None,
    extras: tuple[str, ...],
) -> Conversao:
    """Converte uma copia isolada da fixture, com o audio dentro do pacote.

    A copia reescrita fica em ``preparo`` ate o import acaba; depois some, porque
    o que o import leu ja esta em ``origem``, ao lado do ``.als``. A pasta de
    preparo e criada pela chamada, que a apaga: o que estava la antes e de
    outra pessoa, e a conversao recusa antes de copiar.
    """
    from .live_compat_media import preparar_copia_isolada

    copia = preparar_copia_isolada(aaf, pasta, midia, cheio=cheio)
    try:
        return importar(copia, pasta / f"{nome}.als", midia, session_text=texto, extras=extras)
    finally:
        shutil.rmtree(copia.parent, ignore_errors=True)


def codigos(relatorio: str, severidade: str | None = None) -> list[str]:
    """Codigos do relatorio em markdown da CLI, na ordem em que aparecem."""
    achados = []
    for linha in relatorio.splitlines():
        partes = [parte.strip() for parte in linha.strip().strip("|").split("|")]
        if len(partes) < 2 or not partes[1].startswith("`"):
            continue
        if severidade is None or partes[0] == severidade:
            achados.append(partes[1].strip("`"))
    return achados


@dataclass(frozen=True)
class Perfeicao:
    dialeto: str
    build: str
    entrada: int
    exatos: int
    perdas_d: int

    @property
    def valor(self) -> float:
        if self.entrada <= 0:
            return 0.0
        return self.exatos / self.entrada - self.perdas_d


def exatos(resultado: VerifyResult) -> int:
    """Clips de audio com desvio zero no inicio e no fim."""
    return sum(
        1
        for linha in resultado.rows
        if linha.is_audio
        and linha.status == "ok"
        and linha.delta_samples == 0
        and linha.delta_end_samples == 0
    )


def perdas_d_antes(resultado: VerifyResult) -> int:
    """Clips que o .als nao tem: a perda aberta antes do Live.

    Conservador de proposito, e sem parametro de relatorio: nenhum aviso
    cancela um clip ausente. ``*_SKIPPED`` e um aviso generico, e o codigo de
    um pulo nao diz qual clip de audio sumiu; a perda so fecha com prova do
    material, que o relatorio nao traz. O relatorio continua servindo para
    relatar (``codigos``), nao para apagar perda.
    """
    return max(0, resultado.audio_missing)


def perdas_d_depois(diferencas: list[Diferenca]) -> int:
    """DROPPED_POR_LIVE em campo ou entidade que o aafton escreve."""
    return sum(1 for diferenca in diferencas if diferenca.defeito)


def perfeicao_antes(conversao: Conversao, dialeto: str) -> Perfeicao:
    resultado = verify_conversion(conversao.aaf, conversao.als)
    return Perfeicao(
        dialeto=dialeto,
        build=identidade(conversao.als)["MinorVersion"],
        entrada=resultado.audio_expected,
        exatos=exatos(resultado),
        perdas_d=perdas_d_antes(resultado),
    )


def perfeicao_depois(
    aaf: Path,
    devolvido: Path,
    diferencas: list[Diferenca],
    dialeto: str,
    *,
    perdas_abertas_antes: int,
) -> Perfeicao:
    """A mesma metrica sobre o .als que o Live devolveu no Save As.

    ``perdas_abertas_antes`` e obrigatorio: a perda e cumulativa e a coluna de
    antes entra na conta. Sem esse numero a coluna de depois seria melhor que
    a de antes, e uma devolucao intacta pareceria consertar uma perda antiga.
    A perda antiga so fecha com prova do material, que o Live devolvido nao
    traz: por isso ela continua na conta depois de uma devolucao sem mudanca.
    """
    resultado = verify_conversion(aaf, devolvido)
    build = identidade(devolvido)
    return Perfeicao(
        dialeto=dialeto,
        build=f"{build['MinorVersion']} ({build['Creator']})",
        entrada=resultado.audio_expected,
        exatos=exatos(resultado),
        perdas_d=perdas_abertas_antes + perdas_d_depois(diferencas),
    )
