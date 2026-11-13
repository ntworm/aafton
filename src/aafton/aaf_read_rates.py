"""Classificacao da diferenca entre a taxa do arquivo e a taxa da sessao.

O import aceita midia em taxa padrao diferente da sessao, com aviso, porque
o Live reamostra o arquivo ao tocar e as posicoes seguem a taxa da sessao.
Pull-down (razao 1001/1000) e taxa fora do padrao continuam erro: corrigi-los
em silencio produz uma sessao que soa certa e esta errada.
"""

from __future__ import annotations


from fractions import Fraction


from typing import Literal


from .report import Report


ClassificacaoTaxa = Literal["igual", "padrao", "pulldown"]

TAXAS_PADRAO: frozenset[int] = frozenset(
    {
        8000,
        11025,
        16000,
        22050,
        24000,
        32000,
        44100,
        48000,
        88200,
        96000,
        176400,
        192000,
        352800,
        384000,
    }
)

RAZAO_PULLDOWN = Fraction(1001, 1000)
RAZAO_PULLDOWN_INVERSA = Fraction(1000, 1001)
TOLERANCIA_RAZAO = 1e-4


def _e_pulldown(razao: Fraction) -> bool:
    """Diz se a razao entre as taxas e a de pull-down.

    A comparacao exata cobre 48048/48000 e 47952/48000; a tolerancia de 1e-4
    cobre a taxa de pull-down gravada com arredondamento.
    """
    for referencia in (RAZAO_PULLDOWN, RAZAO_PULLDOWN_INVERSA):
        if razao == referencia:
            return True
        if abs(float(razao) - float(referencia)) <= TOLERANCIA_RAZAO:
            return True
    return False


def classificar_diferenca_de_taxa(taxa_arquivo: int, taxa_sessao: int) -> ClassificacaoTaxa:
    """Classifica a diferenca entre a taxa do arquivo e a taxa da sessao.

    Devolve 'igual' quando as duas taxas coincidem, 'padrao' quando as duas
    sao taxas padrao diferentes entre si, e 'pulldown' quando a razao entre
    elas e 1001/1000 ou 1000/1001, ou quando alguma das duas nao e taxa
    padrao.
    """
    if taxa_arquivo == taxa_sessao:
        return "igual"
    if taxa_arquivo not in TAXAS_PADRAO or taxa_sessao not in TAXAS_PADRAO:
        return "pulldown"
    if _e_pulldown(Fraction(taxa_arquivo, taxa_sessao)):
        return "pulldown"
    return "padrao"


def mesma_duracao(
    dur_a: int | None, taxa_a: int | None, dur_b: int | None, taxa_b: int | None
) -> bool:
    """Diz se duas duracoes em samples representam o mesmo tempo.

    Com as taxas iguais, ou com alguma taxa ausente, a comparacao e exata:
    sem a taxa nao da para converter uma contagem de samples na outra. Com
    taxas diferentes, as duracoes sao comparadas em tempo, aceitando
    diferenca de ate 1 sample na taxa maior, porque a conversao entre taxas
    arredonda.
    """
    if dur_a is None or dur_b is None:
        return dur_a == dur_b
    if not taxa_a or not taxa_b or taxa_a == taxa_b:
        return dur_a == dur_b
    if taxa_a > taxa_b:
        em_taxa_maior = Fraction(dur_a)
        outra = Fraction(dur_b * taxa_a, taxa_b)
    else:
        em_taxa_maior = Fraction(dur_a * taxa_b, taxa_a)
        outra = Fraction(dur_b)
    return abs(em_taxa_maior - outra) <= 1


def relatar_taxa_diferente(
    classificacao: ClassificacaoTaxa,
    nome_arquivo: str,
    taxa_arquivo: int,
    taxa_sessao: int,
    report: Report,
) -> None:
    """Registra no relatorio a diferenca entre a taxa do arquivo e a da sessao.

    Taxa padrao diferente da sessao vira aviso MEDIA_SAMPLE_RATE_DIFFERS,
    porque o Live converte a taxa ao tocar e as posicoes seguem a taxa da
    sessao. Pull-down e taxa fora do padrao continuam erro PULLDOWN_MISMATCH:
    a ferramenta nao faz resample. Taxa igual nao produz nada.
    """
    if classificacao == "igual":
        return
    if classificacao == "padrao":
        report.warning(
            "MEDIA_SAMPLE_RATE_DIFFERS",
            f"{nome_arquivo} is at {taxa_arquivo} Hz and the session at "
            f"{taxa_sessao} Hz; Live converts the rate when playing and the positions "
            "follow the session rate",
            {
                "filename": nome_arquivo,
                "file_sample_rate": taxa_arquivo,
                "session_sample_rate": taxa_sessao,
            },
        )
        return
    report.error(
        "PULLDOWN_MISMATCH",
        f"{nome_arquivo} is at {taxa_arquivo} Hz and the session at "
        f"{taxa_sessao} Hz; the tool does not resample. Convert the "
        "file, or export the AAF at the session rate.",
        {
            "filename": nome_arquivo,
            "file_sample_rate": taxa_arquivo,
            "session_sample_rate": taxa_sessao,
        },
    )
