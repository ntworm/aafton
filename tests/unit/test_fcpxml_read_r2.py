"""Testes da segunda revisao do leitor de FCPXML do produto.

Os documentos vem de FCPXML real: um compound clip com ref-clip na spine, um
marcador dentro de clip conectado, um gap com offset diferente do start e dois
projetos no mesmo arquivo.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aafton.formats.fcpxml_read import read_fcpxml
from aafton.report import Report
from aafton.report import StrictModeError

TAXA = 48000

RECURSOS = (
    "<resources>"
    '<format id="r1" name="FFVideoFormat1080p25" frameDuration="100/2500s"'
    ' width="1920" height="1080"/>'
    '<asset id="r2" name="A001" start="0s" duration="60s" hasVideo="1" hasAudio="1"'
    ' audioSources="1" audioChannels="2" audioRate="48000" format="r1">'
    '<media-rep kind="original-media" src="file:///C:/midia/A001.mov"/>'
    "</asset>"
    '<asset id="r3" name="dial02" start="36000s" duration="60s" hasAudio="1"'
    ' audioSources="1" audioChannels="1" audioRate="48000">'
    '<media-rep kind="original-media" src="file:///C:/midia/dial02.wav"/>'
    "</asset>"
    '<asset id="r4" name="musica" start="0s" duration="120s" hasAudio="1"'
    ' audioSources="1" audioChannels="2" audioRate="48000">'
    '<media-rep kind="original-media" src="file:///C:/midia/musica.wav"/>'
    "</asset>"
    "{extra}"
    "</resources>"
)

# O compound clip: a sequencia dele fica nos recursos, antes do projeto.
COMPOUND = (
    '<media id="r5" name="Compound">'
    '<sequence format="r1" duration="5s" tcStart="0s" tcFormat="NDF"'
    ' audioLayout="stereo" audioRate="48k"><spine>'
    '<asset-clip name="musica dentro" ref="r4" offset="0s" start="10s" duration="5s"/>'
    "</spine></sequence></media>"
)

# O clip principal com o compound clip na spine.
SPINE_COM_COMPOUND = (
    '<asset-clip name="A001" ref="r2" offset="3600s" start="0s" duration="10s"'
    ' tcFormat="NDF"/>'
    '<ref-clip name="Compound" ref="r5" offset="3610s" duration="5s"/>'
)

# O clip principal com um clip conectado que tem um marcador dentro.
SPINE_COM_MARCADOR = (
    '<asset-clip name="A001" ref="r2" offset="3600s" start="0s" duration="20s"'
    ' tcFormat="NDF">'
    '<asset-clip name="dial02" ref="r3" lane="-1" offset="5s" start="36001s"'
    ' duration="3s">'
    '<marker start="36002s" duration="100/2500s" value="M no conectado"/>'
    "</asset-clip>"
    "</asset-clip>"
)

# O gap com offset diferente do start e um clip conectado com marcador.
SPINE_COM_GAP = (
    '<asset-clip name="A001" ref="r2" offset="3600s" start="0s" duration="10s"'
    ' tcFormat="NDF"/>'
    '<gap name="Gap" offset="3610s" start="3600s" duration="10s">'
    '<asset-clip name="dial02" ref="r3" lane="-1" offset="3601s" start="36001s"'
    ' duration="3s">'
    '<marker start="36002s" duration="100/2500s" value="M2"/>'
    "</asset-clip>"
    "</gap>"
)

# O clip conectado a um clip que ja esta conectado.
SPINE_COM_TRES_NIVEIS = (
    '<asset-clip name="A001" ref="r2" offset="3600s" start="0s" duration="20s"'
    ' tcFormat="NDF">'
    '<asset-clip name="dial02" ref="r3" lane="-1" offset="5s" start="36001s"'
    ' duration="3s">'
    '<asset-clip name="musica" ref="r4" lane="-2" offset="36003s" start="1s"'
    ' duration="2s"/>'
    "</asset-clip>"
    "</asset-clip>"
)


def _documento(spine: str, extra: str = "") -> str:
    """FCPXML de teste: os recursos, o projeto e a spine dados."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<!DOCTYPE fcpxml>\n"
        '<fcpxml version="1.9">'
        f"{RECURSOS.format(extra=extra)}"
        '<library><event name="E"><project name="P">'
        '<sequence format="r1" duration="20s" tcStart="3600s" tcFormat="NDF"'
        ' audioLayout="stereo" audioRate="48k">'
        f"<spine>{spine}</spine>"
        "</sequence>"
        "</project></event></library>"
        "</fcpxml>"
    )


def _dois_projetos() -> str:
    """FCPXML com dois projetos no mesmo arquivo."""
    texto = _documento(
        '<asset-clip name="dial02" ref="r3" offset="3600s" start="36001s" duration="3s"/>'
    )
    segundo = (
        '<project name="P2"><sequence format="r1" duration="5s" tcStart="3600s"'
        ' tcFormat="NDF" audioRate="48k"><spine>'
        '<asset-clip name="musica" ref="r4" offset="3600s" start="0s" duration="5s"/>'
        "</spine></sequence></project>"
    )
    return texto.replace("</project></event>", f"{segundo}</project></event>")


def _sem_projeto() -> str:
    """FCPXML com recursos, mas sem projeto nenhum."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<!DOCTYPE fcpxml>\n"
        '<fcpxml version="1.9">'
        f"{RECURSOS.format(extra='')}"
        "</fcpxml>"
    )


def _gravar(tmp_path: Path, texto: str) -> Path:
    """Grava um FCPXML de teste e devolve o caminho."""
    caminho = tmp_path / "caso.fcpxml"
    caminho.write_text(texto, encoding="utf-8")
    return caminho


def _codigos(report: Report) -> list[str]:
    """Codigos de todos os problemas registrados no relatorio."""
    return [issue.code for issue in report.issues]


def _mensagem(report: Report, codigo: str) -> str:
    """Mensagem do primeiro problema com o codigo dado."""
    for issue in report.issues:
        if issue.code == codigo:
            return issue.message
    return ""


def _inicios(sessao) -> dict[str, int]:
    """Tempo de inicio de cada clip da cena, pelo nome."""
    return {clip.name: clip.timeline_start_samples for clip in sessao.all_clips()}


def test_compound_clip_na_spine_nao_troca_a_cena(tmp_path):
    """O compound clip da spine nao vira a cena: o projeto continua o projeto."""
    caminho = _gravar(tmp_path, _documento(SPINE_COM_COMPOUND, COMPOUND))
    report = Report(strict=False)

    sessao = read_fcpxml(caminho, report)

    assert sessao.name == "P"
    assert [clip.name for clip in sessao.all_clips()] == ["A001"]
    assert _inicios(sessao) == {"A001": 3600 * TAXA}
    assert "FORMAT_NESTED_UNSUPPORTED" in _codigos(report)
    with pytest.raises(StrictModeError) as erro:
        read_fcpxml(caminho, Report(strict=True))
    assert "FORMAT_NESTED_UNSUPPORTED" in str(erro.value)


def test_dois_projetos_no_arquivo_sao_erro(tmp_path):
    """Dois projetos no mesmo arquivo: o primeiro e lido e o erro fica."""
    caminho = _gravar(tmp_path, _dois_projetos())
    report = Report(strict=False)

    sessao = read_fcpxml(caminho, report)

    assert "FORMAT_FCPXML_MULTIPLE_PROJECTS" in _codigos(report)
    mensagem = _mensagem(report, "FORMAT_FCPXML_MULTIPLE_PROJECTS")
    assert "P" in mensagem
    assert "P2" in mensagem
    assert sessao.name == "P"
    assert [clip.name for clip in sessao.all_clips()] == ["dial02"]
    with pytest.raises(StrictModeError) as erro:
        read_fcpxml(caminho, Report(strict=True))
    assert "FORMAT_FCPXML_MULTIPLE_PROJECTS" in str(erro.value)


def test_documento_sem_projeto_e_erro(tmp_path):
    """Um FCPXML sem projeto nao vira cena."""
    caminho = _gravar(tmp_path, _sem_projeto())
    report = Report(strict=False)

    sessao = read_fcpxml(caminho, report)

    assert "FORMAT_FCPXML_INVALID" in _codigos(report)
    assert list(sessao.all_clips()) == []
    with pytest.raises(StrictModeError) as erro:
        read_fcpxml(caminho, Report(strict=True))
    assert "FORMAT_FCPXML_INVALID" in str(erro.value)


def test_marcador_em_clip_conectado_fica_no_tempo_de_sequencia(tmp_path):
    """O marcador do clip conectado fica no tempo de sequencia da cena."""
    caminho = _gravar(tmp_path, _documento(SPINE_COM_MARCADOR))

    sessao = read_fcpxml(caminho, Report(strict=False))

    assert [(m.name, m.position_samples) for m in sessao.markers] == [
        ("M no conectado", 3606 * TAXA)
    ]
    assert _inicios(sessao) == {"A001": 3600 * TAXA, "dial02": 3605 * TAXA}


def test_marcador_em_gap_com_offset_diferente_do_start(tmp_path):
    """O marcador do clip do gap com offset diferente do start fica no lugar."""
    caminho = _gravar(tmp_path, _documento(SPINE_COM_GAP))

    sessao = read_fcpxml(caminho, Report(strict=False))

    assert [(m.name, m.position_samples) for m in sessao.markers] == [("M2", 3612 * TAXA)]
    assert _inicios(sessao) == {"A001": 3600 * TAXA, "dial02": 3611 * TAXA}


def test_clip_conectado_a_clip_conectado_fica_no_lugar(tmp_path):
    """O clip dentro de um clip conectado fica no tempo de sequencia da cena."""
    caminho = _gravar(tmp_path, _documento(SPINE_COM_TRES_NIVEIS))

    sessao = read_fcpxml(caminho, Report(strict=False))

    assert _inicios(sessao) == {
        "A001": 3600 * TAXA,
        "dial02": 3605 * TAXA,
        "musica": 3607 * TAXA,
    }


def test_o_ajuste_do_fcpxml_so_guarda_o_tempo_exato():
    """O modulo de ajuste do FCPXML nao normaliza documento nenhum."""
    from aafton.formats import fcpxml_fix

    assert not hasattr(fcpxml_fix, "normalizar_fcpxml")
    assert not hasattr(fcpxml_fix, "NormalizacaoFcpxml")
