"""E5 do R07.6: a metrica unica de perfeicao e o material que ela usa.

PERFEICAO = exatos / entrada - perdas_d. A conta tem de ser 1.0 numa conversao
sample-exata sem perda, cair quando um clip sai do lugar e cair um ponto inteiro
por perda aberta de classe D. A perda e cumulativa: a coluna de depois carrega
a de antes, porque devolver um .als igual ao gerado nao prova que o material
voltou.
"""

from __future__ import annotations

import gzip
import wave
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from aafton.als_compare import DROPPED_POR_LIVE, RESCRITO_POR_LIVE, Diferenca
from aafton.als_xml import load_als
from aafton.live_compat import (
    FIXTURES,
    FIXTURES_AAF,
    Conversao,
    Perfeicao,
    codigos,
    converter_fixture,
    escrever_portadoras,
    fixture,
    fixtures_disponiveis,
    importar,
    perdas_d_antes,
    perdas_d_depois,
    perfeicao_antes,
    perfeicao_depois,
)
from aafton.verify import VerifyResult, verify_conversion
from tests import aaf_factory
from tests.support.paths import is_private_fixture


def test_toda_fixture_registrada_existe_e_tem_dialeto():
    ausentes_privadas = []
    for item in FIXTURES:
        caminho = FIXTURES_AAF / f"{item.nome}.aaf"
        if not caminho.is_file() and is_private_fixture(item.nome):
            ausentes_privadas.append(item.nome)
            continue
        assert caminho.is_file(), item.nome
        assert item.dialeto
        if item.session_text:
            assert (FIXTURES_AAF / item.session_text).is_file(), item.session_text
    with pytest.raises(KeyError):
        fixture("nao_existe")
    if ausentes_privadas:
        pytest.skip(f"not distributed: {', '.join(ausentes_privadas)} (private fixture)")


def test_fixtures_disponiveis_com_pasta_temporaria(tmp_path, capsys):
    (tmp_path / "TESTE_DF_2997.aaf").write_bytes(b"dummy")
    disponiveis = fixtures_disponiveis(tmp_path)
    assert len(disponiveis) == 1
    assert disponiveis[0].nome == "TESTE_DF_2997"
    captura = capsys.readouterr()
    assert "fixture missing:" in captura.err


def test_a_conta_da_perfeicao():
    assert Perfeicao(dialeto="x", build="b", entrada=4, exatos=4, perdas_d=0).valor == 1.0
    assert Perfeicao(dialeto="x", build="b", entrada=4, exatos=3, perdas_d=0).valor == 0.75
    assert Perfeicao(dialeto="x", build="b", entrada=4, exatos=4, perdas_d=1).valor == 0.0
    assert Perfeicao(dialeto="x", build="b", entrada=0, exatos=0, perdas_d=0).valor == 0.0


def test_perda_d_antes_ignora_aviso_de_video_e_varios_avisos():
    """Nenhum aviso cancela clip ausente: a perda de antes e o que o .als nao tem."""
    resultado = VerifyResult(audio_expected=5, audio_missing=2)
    relatorio = (
        "| severidade | codigo | mensagem |\n"
        "| warning | `VIDEO_SKIPPED` | pulado |\n"
        "| warning | `WRITE_CLIP_SKIPPED` | pulado |\n"
        "| warning | `SESSION_TEXT_SKIPPED` | pulado |\n"
    )
    assert perdas_d_antes(resultado) == 2
    assert codigos(relatorio) == ["VIDEO_SKIPPED", "WRITE_CLIP_SKIPPED", "SESSION_TEXT_SKIPPED"]
    assert codigos(relatorio, "error") == []


def test_perda_d_depois_e_o_defeito_do_comparador():
    diferencas = [
        Diferenca("AudioTrack[A#0]/AudioClip[c#0]", "existe", None, DROPPED_POR_LIVE, True),
        Diferenca("AudioTrack[A#0]/Color", "1", None, DROPPED_POR_LIVE, False),
        Diferenca("AudioTrack[A#0]/AudioClip[c#1]/Color", "1", "2", RESCRITO_POR_LIVE, False),
    ]
    assert perdas_d_depois(diferencas) == 1


def test_portadora_cheia_tem_o_comprimento_que_os_clips_pedem(tmp_path):
    aaf = FIXTURES_AAF / "TESTE_DF_2997.aaf"
    cheias = escrever_portadoras(aaf, tmp_path / "cheia", cheio=True)
    curtas = escrever_portadoras(aaf, tmp_path / "curta")
    assert cheias and len(cheias) == len(curtas)
    with wave.open(str(curtas[0])) as handle:
        assert handle.getnframes() == 1
    assert all(_quadros(c) > 1 for c in cheias)


def _quadros(caminho: Path) -> int:
    with wave.open(str(caminho)) as handle:
        return handle.getnframes()


def test_perfeicao_antes_de_uma_fixture_real_e_um(tmp_path):
    conversao = converter_fixture("TESTE_DF_2997", tmp_path)
    assert conversao.codigo == 0, conversao.relatorio[-1500:]
    medida = perfeicao_antes(conversao, "Pro Tools")
    assert medida.entrada == 22
    assert medida.exatos == medida.entrada
    assert medida.perdas_d == 0
    assert medida.valor == 1.0
    assert medida.build == "12.0_12402"


def test_perfeicao_depois_usa_o_als_devolvido_e_o_build_dele(tmp_path):
    """Sem Live aqui: o .als gerado faz o papel do devolvido, com o sidecar copiado."""
    conversao = converter_fixture("TESTE_DF_2997", tmp_path)
    devolvido = tmp_path / "saida_do_live.als"
    devolvido.write_bytes(conversao.als.read_bytes())
    sidecar = conversao.als.with_name(conversao.als.stem + ".aafton.json")
    devolvido.with_name("saida_do_live.aafton.json").write_bytes(sidecar.read_bytes())
    medida = perfeicao_depois(conversao.aaf, devolvido, [], "Pro Tools", perdas_abertas_antes=0)
    assert medida.valor == 1.0
    assert medida.build.startswith("12.0_12402 (Ableton Live")


def test_perfeicao_depois_exige_a_perda_de_antes():
    """Sem o numero de antes nao ha metrica depois: a perda e cumulativa."""
    with pytest.raises(TypeError):
        perfeicao_depois(Path("a.aaf"), Path("b.als"), [], "Pro Tools")


def _conversao_com_dois_clips(pasta: Path) -> Conversao:
    """Um AAF com dois SourceClips e o .als que sai deles."""
    midia = pasta / "midia"
    midia.mkdir(parents=True, exist_ok=True)
    wav = midia / "fonte.wav"
    wav.write_bytes(aaf_factory.wav_bytes(48000 * 70, channels=1, sample_rate=48000))
    aaf = pasta / "fonte.aaf"
    aaf_factory.build_aaf_with_transition(aaf, wav)
    return importar(aaf, pasta / "caso.als", midia)


def _conversao_com_dois_clips_simples(pasta: Path) -> Conversao:
    """Um AAF com dois clips linkados, sem crossfade, e o .als que sai deles."""
    midia = pasta / "midia"
    midia.mkdir(parents=True, exist_ok=True)
    wav = midia / "fonte.wav"
    wav.write_bytes(aaf_factory.wav_bytes(48000 * 70, channels=1, sample_rate=48000))
    aaf = pasta / "fonte.aaf"
    aaf_factory.build_linked_aaf(aaf, [(0, 96000, 0), (96000, 96000, 96000)], wav)
    return importar(aaf, pasta / "caso.als", midia)


def _sem_um_clip(origem: Path, destino: Path) -> Path:
    """Copia o .als sem um clip de audio: uma perda aberta antes do Live."""
    raiz = load_als(origem)
    for eventos in raiz.findall(
        ".//AudioTrack/DeviceChain/MainSequencer/Sample/ArrangerAutomation/Events"
    ):
        clips = eventos.findall("AudioClip")
        if clips:
            eventos.remove(clips[-1])
            break
    else:
        raise AssertionError("o .als gerado nao tem clip de audio")
    destino.write_bytes(gzip.compress(ET.tostring(raiz, encoding="utf-8")))
    sidecar = origem.with_name(origem.stem + ".aafton.json")
    destino.with_name(destino.stem + ".aafton.json").write_bytes(sidecar.read_bytes())
    return destino


def test_perda_antes_continua_na_coluna_depois_quando_a_devolucao_nao_muda(tmp_path):
    """Sem Live aqui: o .als de entrada faz o papel do devolvido.

    Devolver o mesmo .als nao conserta a perda antiga: a perda e cumulativa.
    """
    conversao = _conversao_com_dois_clips_simples(tmp_path)
    entrada = _sem_um_clip(conversao.als, tmp_path / "entrada.als")
    antes = perfeicao_antes(Conversao(conversao.aaf, entrada, 0, conversao.relatorio), "Pro Tools")
    assert (antes.entrada, antes.exatos, antes.perdas_d) == (2, 1, 1)
    assert antes.valor == pytest.approx(-0.5)
    depois = perfeicao_depois(
        conversao.aaf, entrada, [], "Pro Tools", perdas_abertas_antes=antes.perdas_d
    )
    assert depois.perdas_d == 1
    assert depois.valor == pytest.approx(antes.valor)


def test_apagar_o_clip_que_entra_no_crossfade_conta_so_ele(tmp_path):
    """Na forma nativa medida o clip que sai fica com o fim do AAF.

    O crossfade toca antes da juncao, que e o fim do clip que sai: apagar o
    clip que entra perde so ele, e o que sai continua exato.
    """
    conversao = _conversao_com_dois_clips(tmp_path)
    entrada = _sem_um_clip(conversao.als, tmp_path / "entrada.als")
    antes = perfeicao_antes(Conversao(conversao.aaf, entrada, 0, conversao.relatorio), "Pro Tools")
    assert (antes.entrada, antes.exatos, antes.perdas_d) == (2, 1, 1)
    assert antes.valor == pytest.approx(-0.5)
    depois = perfeicao_depois(
        conversao.aaf, entrada, [], "Pro Tools", perdas_abertas_antes=antes.perdas_d
    )
    assert depois.perdas_d == 1
    assert depois.valor == pytest.approx(antes.valor)
    resultado = verify_conversion(conversao.aaf, entrada)
    linhas = [linha for linha in resultado.rows if linha.is_audio]
    assert not [linha for linha in linhas if linha.delta_end_samples == -24000]


def test_perda_nova_do_live_entra_uma_vez_na_coluna_depois(tmp_path):
    """A perda de antes continua e a nova (DROPPED_POR_LIVE) entra por cima."""
    conversao = converter_fixture("TESTE_DF_2997", tmp_path)
    devolvido = tmp_path / "saida_do_live.als"
    devolvido.write_bytes(conversao.als.read_bytes())
    sidecar = conversao.als.with_name(conversao.als.stem + ".aafton.json")
    devolvido.with_name("saida_do_live.aafton.json").write_bytes(sidecar.read_bytes())
    diferencas = [
        Diferenca("AudioTrack[A#0]/AudioClip[c#0]", "existe", None, DROPPED_POR_LIVE, True)
    ]
    medida = perfeicao_depois(
        conversao.aaf, devolvido, diferencas, "Pro Tools", perdas_abertas_antes=1
    )
    assert medida.perdas_d == 2
    assert medida.valor == pytest.approx(1.0 - 2)
