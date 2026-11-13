"""A volta do operador: o Save As do Live e o inventario de proveniencia.

O Live salva por padrao em uma pasta nova (`saida_do_live Project`) com o
`saida_do_live.als` dentro; a volta plana tambem vale. As duas juntas nao
valem: com as duas presentes nao ha prova de qual e a volta.

O inventario `midias_proveniente` amarra, por hash, as midias que o `.als`
gerado referencia. Um manifesto sem o campo continua valido (o formato
anterior, com os cinco hashes); com o campo, a midia que mudou, a que sumiu,
a que sai do pacote e a lista que nao cobre o `.als` recusam o caso -- antes de
qualquer escrita de medicao.
"""

from __future__ import annotations

import gzip
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import prepare_live_compat as pacote_live  # noqa: E402

from aafton.live_compat_proveniencia import (  # noqa: E402
    PASTA_DO_LIVE,
    SAIDA_ESPERADA,
    ProvenienciaInvalida,
    hashes_do_caso,
    sha256_arquivo,
    validar_pacote,
)
from tests import aaf_factory  # noqa: E402
from tests.unit.test_live_compat_proveniencia import _pacote  # noqa: E402


# --- Ajudantes --------------------------------------------------------------


def _junction(link: Path, alvo: Path) -> bool:
    """Cria uma junction do Windows; False quando o host nao deixa."""
    comando = f'mklink /J "{link}" "{alvo}"'
    resultado = subprocess.run(comando, shell=True, capture_output=True, text=True)
    return resultado.returncode == 0


def _als_com_refs(destino: Path, midias: list[Path | None]) -> Path:
    """Um .als minimo: um AudioClip por midia, sem Path quando a midia e None.

    A midia ``None`` e um FileRef sem Path e sem RelativePath: o Live nao
    sabe de onde veio o audio daquele clip.
    """
    raiz = ET.Element("LiveSet")
    tracks = ET.SubElement(raiz, "Tracks")
    track = ET.SubElement(tracks, "AudioTrack")
    eventos = _sub(track, "DeviceChain/MainSequencer/Sample/ArrangerAutomation/Events")
    for midia in midias:
        clip = ET.SubElement(eventos, "AudioClip")
        ref = ET.SubElement(clip, "SampleRef")
        file_ref = ET.SubElement(ref, "FileRef")
        if midia is not None:
            ET.SubElement(file_ref, "Path").set("Value", str(midia))
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(gzip.compress(ET.tostring(raiz, encoding="utf-8")))
    return destino


def _sub(parent: ET.Element, caminho: str) -> ET.Element:
    """Cria a cadeia de elementos do caminho, um a um."""
    atual = parent
    for parte in caminho.split("/"):
        atual = ET.SubElement(atual, parte)
    return atual


def _pacote_com_inventario(
    tmp_path: Path, *, ilegiveis: int = 0, com_valida: bool = True
) -> tuple[Path, dict]:
    """Um pacote com um .als de verdade, uma midia e o inventario que a amarra.

    ``ilegiveis`` e quantos AudioClip do .als gerado tem FileRef sem Path: a
    referencia que o .als nao sabe ler nao pode virar inventario vazio.
    """
    midia = tmp_path / "_maquina" / "midia" / "fonte.wav"
    midia.parent.mkdir(parents=True, exist_ok=True)
    midia.write_bytes(aaf_factory.wav_bytes(480, channels=1, sample_rate=48000))

    refs: list[Path | None] = [None] * ilegiveis
    if com_valida:
        refs.append(midia)
    als = _als_com_refs(tmp_path / "_maquina" / "caso.als", refs)
    (tmp_path / "caso").mkdir(parents=True, exist_ok=True)
    (tmp_path / "caso" / "entrada.als").write_bytes(als.read_bytes())
    (tmp_path / "_maquina" / "caso.aafton.json").write_text('{"formato": 1}\n', encoding="utf-8")
    (tmp_path / "_maquina" / "origem").mkdir(parents=True, exist_ok=True)
    (tmp_path / "_maquina" / "origem" / "fonte.aaf").write_bytes(b"aaf")
    (tmp_path / "_maquina" / "relatorio.md").write_text("relatorio\n", encoding="utf-8")

    caminhos = {
        "entrada": "caso/entrada.als",
        "als_gerado": "_maquina/caso.als",
        "sidecar": "_maquina/caso.aafton.json",
        "aaf": "_maquina/origem/fonte.aaf",
        "relatorio": "_maquina/relatorio.md",
    }
    caso = {
        "id": "C05",
        "dialeto": "pyaaf2",
        "pasta": "caso",
        "als_de_entrada": caminhos["entrada"],
        "als_gerado": caminhos["als_gerado"],
        "aaf": caminhos["aaf"],
        "saida_esperada": "caso/saida_do_live.als",
        "caminhos_proveniente": caminhos,
        "hashes": hashes_do_caso(tmp_path, caminhos),
        "midias_proveniente": [
            {"caminho": "_maquina/midia/fonte.wav", "sha256": sha256_arquivo(midia)}
        ],
    }
    return tmp_path, {"casos": [caso]}


def _link_para_o_alias(monkeypatch, alias: Path, alvo: Path) -> None:
    """Simula o link de arquivo: o alias resolve para o alvo de verdade.

    O host do teste nao cria link simbolico; o que a validacao confere e o
    caminho canonico, e e isso que o link mudaria.
    """
    original = Path.resolve

    def resolve_do_link(self, *args, **kwargs):
        if self == alias:
            return original(alvo, *args, **kwargs)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve_do_link)


def _gravar(pacote: Path, manifesto: dict) -> None:
    (pacote / "manifesto.json").write_text(
        json.dumps(manifesto, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# --- A volta do operador ----------------------------------------------------


def test_saida_plana_e_aceita(tmp_path):
    """A volta plana, na pasta do caso, e a saida conferida."""
    pacote, manifesto = _pacote(tmp_path)
    saida = pacote / "caso" / "saida_do_live.als"
    saida.write_bytes(b"volta do Live")

    validados = validar_pacote(pacote, manifesto)

    assert validados[0]["saida"] == saida.resolve()


def test_saida_aninhada_do_live_e_aceita(tmp_path):
    """A volta na pasta que o Live cria por padrao tambem e a saida conferida."""
    pacote, manifesto = _pacote(tmp_path)
    aninhada = pacote / "caso" / PASTA_DO_LIVE / "saida_do_live.als"
    aninhada.parent.mkdir(parents=True)
    aninhada.write_bytes(b"volta do Live")

    validados = validar_pacote(pacote, manifesto)

    assert validados[0]["saida"] == aninhada.resolve()


def test_saida_plana_e_aninhada_juntas_sao_recusadas(tmp_path):
    """Com as duas voltas presente nao ha prova de qual e a volta."""
    pacote, manifesto = _pacote(tmp_path)
    mesma = b"volta do Live"
    (pacote / "caso" / "saida_do_live.als").write_bytes(mesma)
    aninhada = pacote / "caso" / PASTA_DO_LIVE / "saida_do_live.als"
    aninhada.parent.mkdir(parents=True)
    aninhada.write_bytes(mesma)

    with pytest.raises(ProvenienciaInvalida) as erro:
        validar_pacote(pacote, manifesto)

    texto = str(erro.value)
    assert "two places" in texto
    assert "caso/saida_do_live.als" in texto
    assert f"caso/{PASTA_DO_LIVE}/saida_do_live.als" in texto
    assert "saida_do_live-discarded.als" in texto


def test_saida_plana_e_aninhada_juntas_com_pacote_relativo(tmp_path, monkeypatch):
    """Com o pacote por caminho relativo, a recusa das duas voltas e a mesma.

    O pacote pode vir como caminho relativo; a conferenca e a recusa nao
    podem depender disso.
    """
    pacote, manifesto = _pacote(tmp_path / "pacote")
    mesma = b"volta do Live"
    (pacote / "caso" / "saida_do_live.als").write_bytes(mesma)
    aninhada = pacote / "caso" / PASTA_DO_LIVE / "saida_do_live.als"
    aninhada.parent.mkdir(parents=True)
    aninhada.write_bytes(mesma)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ProvenienciaInvalida) as erro:
        validar_pacote(Path("pacote"), manifesto)

    assert "two places" in str(erro.value)


def test_sem_volta_a_saida_plana_fica_pendente(tmp_path):
    """Sem volta do operador, a saida conferida e a plana, que nao existe."""
    pacote, manifesto = _pacote(tmp_path)

    validados = validar_pacote(pacote, manifesto)

    assert validados[0]["saida"] == (pacote / "caso" / "saida_do_live.als").resolve()
    assert not validados[0]["saida"].exists()


def test_junction_que_foge_da_pasta_do_caso_e_recusada(tmp_path):
    """Uma junction da pasta da saida para fora do caso e recusada."""
    pacote, manifesto = _pacote(tmp_path)
    fora = tmp_path / "fora"
    fora.mkdir()
    (fora / "saida_do_live.als").write_bytes(b"volta do Live")
    if not _junction(pacote / "caso" / PASTA_DO_LIVE, fora):
        pytest.skip("o host nao cria junction")

    with pytest.raises(ProvenienciaInvalida) as erro:
        validar_pacote(pacote, manifesto)

    assert "case folder" in str(erro.value)


def test_volta_aninhada_que_aponta_para_a_entrada_e_recusada(tmp_path, monkeypatch):
    """A volta que e a propria entrada do operador nao e devolucao.

    O caminho plano nao existe, e o aninhado e um link para a entrada: o
    caminho canonico da volta e o proprio arquivo de entrada. Conferir o
    caminho plano antes da escolha nao pegaria este caso.
    """
    pacote, manifesto = _pacote(tmp_path)
    raiz = pacote.resolve()
    alias = (raiz / "caso").resolve() / PASTA_DO_LIVE / SAIDA_ESPERADA
    alvo = raiz / "caso" / "entrada.als"
    alias.parent.mkdir(parents=True)
    alias.write_bytes(b"volta do Live")
    _link_para_o_alias(monkeypatch, alias, alvo)

    with pytest.raises(ProvenienciaInvalida) as erro:
        validar_pacote(pacote, manifesto)

    assert "operator input" in str(erro.value)
    caso = manifesto["casos"][0]
    assert hashes_do_caso(pacote, caso["caminhos_proveniente"]) == caso["hashes"]
    assert not (pacote / "medicao.json").exists()
    assert not (pacote / "caso" / "saida_do_live.aafton.json").exists()


def test_manifesto_sem_inventario_continua_valido(tmp_path):
    """O formato anterior, com os cinco hashes, continua valendo."""
    pacote, manifesto = _pacote(tmp_path)
    assert "midias_proveniente" not in manifesto["casos"][0]

    validar_pacote(pacote, manifesto)


# --- O inventario de midias -------------------------------------------------


def test_inventario_modificado_e_recusado(tmp_path):
    """A midia que mudou depois do preparo nao serve."""
    pacote, manifesto = _pacote_com_inventario(tmp_path)
    (pacote / "_maquina" / "midia" / "fonte.wav").write_bytes(b"conteudo trocado")

    with pytest.raises(ProvenienciaInvalida) as erro:
        validar_pacote(pacote, manifesto)

    assert "changed" in str(erro.value)


def test_inventario_ausente_e_recusado(tmp_path):
    """A midia que sumiu do pacote nao serve."""
    pacote, manifesto = _pacote_com_inventario(tmp_path)
    (pacote / "_maquina" / "midia" / "fonte.wav").unlink()

    with pytest.raises(ProvenienciaInvalida) as erro:
        validar_pacote(pacote, manifesto)

    assert "missing" in str(erro.value)


def test_inventario_que_foge_do_pacote_e_recusado(tmp_path):
    """A midia listada tem de morar dentro do pacote."""
    pacote, manifesto = _pacote_com_inventario(tmp_path)
    manifesto["casos"][0]["midias_proveniente"][0]["caminho"] = "../fora.wav"

    with pytest.raises(ProvenienciaInvalida) as erro:
        validar_pacote(pacote, manifesto)

    assert "package" in str(erro.value)


def test_inventario_sem_cobertura_e_recusado(tmp_path):
    """A lista tem de cobrir as referencias de audio do .als gerado."""
    pacote, manifesto = _pacote_com_inventario(tmp_path)
    manifesto["casos"][0]["midias_proveniente"] = []

    with pytest.raises(ProvenienciaInvalida) as erro:
        validar_pacote(pacote, manifesto)

    assert "does not cover" in str(erro.value)


def test_inventario_alterado_recusa_antes_de_medir(tmp_path):
    """A recusa vem antes de qualquer escrita: nada de medicao nem de sidecar."""
    pacote, manifesto = _pacote_com_inventario(tmp_path)
    _gravar(pacote, manifesto)
    (pacote / "_maquina" / "midia" / "fonte.wav").write_bytes(b"conteudo trocado")

    with pytest.raises(ProvenienciaInvalida):
        pacote_live.medir(pacote, fixtures=False)

    assert not (pacote / "medicao.json").exists()
    assert not (pacote / "caso" / "saida_do_live.aafton.json").exists()


def test_inventario_null_e_recusado(tmp_path):
    """Um inventario nulo nao desliga os hashes: o caso e recusado.

    O campo que existe com valor nulo nao e o campo que nao existe: o
    formato anterior e o que nao traz a chave.
    """
    pacote, manifesto = _pacote_com_inventario(tmp_path)
    manifesto["casos"][0]["midias_proveniente"] = None
    (pacote / "_maquina" / "midia" / "fonte.wav").write_bytes(b"conteudo trocado")
    _gravar(pacote, manifesto)

    with pytest.raises(ProvenienciaInvalida) as erro:
        pacote_live.medir(pacote, fixtures=False)

    assert "inventory" in str(erro.value)
    assert not (pacote / "medicao.json").exists()
    assert not (pacote / "caso" / "saida_do_live.aafton.json").exists()


@pytest.mark.parametrize("com_valida", [False, True], ids=["so_ilegivel", "ao_lado_de_uma_valida"])
def test_referencia_de_midia_ilegivel_e_recusada(tmp_path, com_valida):
    """Um FileRef sem Path nem RelativePath nao vira inventario vazio.

    O clip que nao sabe de onde veio o audio nao pode sumir da lista: o caso
    e recusado, com ou sem outra referencia valida ao lado.
    """
    pacote, manifesto = _pacote_com_inventario(tmp_path, ilegiveis=1, com_valida=com_valida)

    with pytest.raises(ProvenienciaInvalida) as erro:
        validar_pacote(pacote, manifesto)

    assert "media reference" in str(erro.value)


def test_referencia_de_midia_ilegivel_recusa_antes_de_medir(tmp_path):
    """A recusa vem antes de qualquer escrita: nada de medicao nem de sidecar."""
    pacote, manifesto = _pacote_com_inventario(tmp_path, ilegiveis=1)
    _gravar(pacote, manifesto)

    with pytest.raises(ProvenienciaInvalida):
        pacote_live.medir(pacote, fixtures=False)

    assert not (pacote / "medicao.json").exists()
    assert not (pacote / "caso" / "saida_do_live.aafton.json").exists()
