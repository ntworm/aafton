"""O texto do verify em ingles: a linha de cobertura e a linha do veredito.

A linha de cobertura e escrita por `verify_coverage.formatar_cobertura` e o
Markdown do verify corta o prefixo dela. O teste monta o resultado com a linha
que o proprio modulo escreve, para o prefixo escrito e o prefixo cortado nao
se separarem.
"""

from aafton.verify import VerifyResult, VerifyRow
from aafton.verify_coverage import Mapeamento, formatar_cobertura

COBERTURA = "1 of 1 audio events compared (0 missing, 0 extra)"
MAPEAMENTO = "Mapping: 2 of 2 slots mapped, across 1 tracks of the .als"


def _linha(status="ok", desvio=0):
    return VerifyRow(
        track="DX 1",
        clip="fala",
        aaf_in="01:00:00:00",
        aaf_out="01:00:01:00",
        als_in="01:00:00:00",
        als_out="01:00:01:00",
        delta_samples=desvio,
        delta_frames=0.0,
        delta_end_samples=desvio,
        status=status,
    )


def _resultado(**campos):
    resultado = VerifyResult(
        regime_name="Sample-exact (edit_rate == sample_rate)",
        tolerance_samples=0,
        audio_expected=1,
        audio_compared=1,
        rows=[_linha()],
    )
    for nome, valor in campos.items():
        setattr(resultado, nome, valor)
    resultado.coverage_msg = formatar_cobertura(resultado, Mapeamento(2, 2, 1))
    return resultado


def test_a_linha_de_cobertura_tem_o_prefixo_que_o_markdown_corta():
    resultado = _resultado()

    assert resultado.coverage_msg == f"Coverage: {COBERTURA}; {MAPEAMENTO}"
    assert f"- **Coverage**: {COBERTURA}; {MAPEAMENTO}" in resultado.to_markdown().splitlines()
    assert f"Coverage: {COBERTURA}; {MAPEAMENTO}" in resultado.to_table().splitlines()


def test_o_veredito_aprovado_em_ingles():
    resultado = _resultado()

    assert resultado.ok
    assert "- **Result**: passed" in resultado.to_markdown().splitlines()
    assert "Result: passed" in resultado.to_table().splitlines()


def test_o_veredito_reprovado_em_ingles():
    resultado = _resultado(rows=[_linha("out of tolerance", 3)])

    assert not resultado.ok
    assert "- **Result**: failed" in resultado.to_markdown().splitlines()
    assert "Result: failed" in resultado.to_table().splitlines()
    assert "- **Largest deviation**: 3 samples" in resultado.to_markdown().splitlines()


def test_o_cabecalho_e_os_campos_do_markdown_em_ingles():
    linhas = _resultado().to_markdown().splitlines()

    assert linhas[0] == "# Verification AAF -> ALS"
    assert "- **Tolerance**: 0 samples" in linhas
    assert "- **Audio events**: 1 compared of 1 expected (0 missing, 0 extra)" in linhas
    assert (
        "| Track | Clip | TC AAF | TC ALS | Start deviation (samples) | End deviation (samples) "
        "| Status |"
    ) in linhas
