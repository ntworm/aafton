"""A midia do pacote do Live mora dentro do pacote.

O `.als` que o operador abre no Live aponta para as portadoras que o proprio
pacote carrega, e nao para o arquivo que estava no Desktop de quem montou a
fixture. Os testes aqui provam isso no AAF real do Pro Tools
(`TESTE_AFF_PROTOOLS`): a copia isolada aponta para dentro, a fixture original
e a midia externa ficam como estavam, os grupos e os 90 eventos continuam no
`.als`, e o inventario de proveniencia amarra as midias por hash.
"""

from __future__ import annotations

import gzip
import json
import shutil
import sys
import wave
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import prepare_live_compat as pacote_live  # noqa: E402

from aafton.aaf_read import read_aaf  # noqa: E402
from aafton.aaf_read_components import _url_para_caminho  # noqa: E402
from aafton.als_xml import load_als  # noqa: E402
from aafton.ir import MediaAsset, SessionIR  # noqa: E402
from aafton.live_compat import (  # noqa: E402
    Conversao,
    _amostras_da_portadora,
    converter_fixture,
    perfeicao_antes,
)
from aafton.live_compat_media import (  # noqa: E402
    MidiaLocalInvalida,
    agrupar_portadoras,
    chave_do_nome,
    inventario_de_midias,
    midias_efetivas,
    preparar_copia_isolada,
)
from aafton.live_compat_proveniencia import sha256_arquivo  # noqa: E402
from aafton.report import Report  # noqa: E402
from aafton.timecode import TimecodeRate  # noqa: E402
from aafton.collect_aaf import iter_descriptors  # noqa: E402
from tests import aaf_factory  # noqa: E402

FIXTURE = "TESTE_AFF_PROTOOLS"
FIXTURE_AAF = REPO / "tests" / "fixtures" / "aaf" / f"{FIXTURE}.aaf"


# --- Ajudantes --------------------------------------------------------------


def _urlstrings(arquivo) -> list:
    """Os URLString de locator do AAF aberto, com o objeto que os guarda."""
    urls = []
    for mob in arquivo.content.sourcemobs():
        for descritor in iter_descriptors(getattr(mob, "descriptor", None)):
            locators = descritor.get("Locator") if hasattr(descritor, "get") else None
            if not locators:
                continue
            for locator in locators.value:
                url = locator.get("URLString") if hasattr(locator, "get") else None
                if url and url.value:
                    urls.append(url)
    return urls


def _urls_do_aaf(aaf: Path) -> list[str]:
    """Todas as URLStrings de locator do AAF, na ordem em que o pyaaf2 le."""
    import aaf2

    with aaf2.open(str(aaf), "r") as arquivo:
        return [url.value for url in _urlstrings(arquivo)]


def _estrutura_do_aaf(aaf: Path) -> list[tuple]:
    """A estrutura do AAF sem os caminhos: mobs, slots e essencia."""
    import aaf2

    with aaf2.open(str(aaf), "r") as arquivo:
        return sorted(
            (
                type(mob).__name__,
                str(getattr(mob, "name", "")),
                tuple(
                    (str(getattr(slot, "slot_id", "")), str(getattr(slot, "name", "")))
                    for slot in getattr(mob, "slots", []) or []
                ),
                type(getattr(mob, "essence", None)).__name__,
            )
            for mob in arquivo.content.mobs
        )


def _apontar_para_o_externo(destino: Path, externo: Path) -> None:
    """Aponta os locators de audio do AAF copiado para os arquivos de fora."""
    import aaf2

    with aaf2.open(str(destino), "rw") as arquivo:
        for url in _urlstrings(arquivo):
            alvo = externo / Path(_url_para_caminho(url.value)).name
            if alvo.exists():
                url.value = alvo.as_uri()


def _copia_com_locator_externo(destino: Path, externo: Path) -> list[Path]:
    """Copia da fixture com os locators de audio apontando para fora do pacote.

    O leitor de midia prefere o locator absoluto que existe: sem a reescrita da
    copia, a conversao usaria o arquivo de fora e o pacote nao seria
    autocontido.
    """
    shutil.copyfile(FIXTURE_AAF, destino)
    sessao = read_aaf(destino, Report(strict=False))
    externos = []
    for nome in sorted({Path(asset.filename).name for asset in sessao.media if asset.filename}):
        caminho = externo / nome
        caminho.write_bytes(aaf_factory.wav_bytes(480, channels=1, sample_rate=48000))
        externos.append(caminho)
    _apontar_para_o_externo(destino, externo)
    return externos


def _quadros(caminho: Path) -> int:
    with wave.open(str(caminho), "rb") as handle:
        return handle.getnframes()


def _pcm(caminho: Path) -> bytes:
    with wave.open(str(caminho), "rb") as handle:
        return handle.readframes(handle.getnframes())


def _sessao_simples(media: list[MediaAsset]) -> SessionIR:
    return SessionIR(
        name="x",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=0,
        media=media,
    )


def _als_com_midia(destino: Path, midia: Path) -> Path:
    """Um .als minimo com um AudioClip que aponta para a midia dada."""
    raiz = ET.Element("LiveSet")
    tracks = ET.SubElement(raiz, "Tracks")
    track = ET.SubElement(tracks, "AudioTrack")
    eventos = _sub(track, "DeviceChain/MainSequencer/Sample/ArrangerAutomation/Events")
    clip = ET.SubElement(eventos, "AudioClip")
    ref = ET.SubElement(clip, "SampleRef")
    file_ref = ET.SubElement(ref, "FileRef")
    ET.SubElement(file_ref, "Path").set("Value", midia.as_posix())
    destino.write_bytes(gzip.compress(ET.tostring(raiz, encoding="utf-8")))
    return destino


def _sub(parent: ET.Element, caminho: str) -> ET.Element:
    """Cria a cadeia de elementos do caminho, um a um."""
    atual = parent
    for parte in caminho.split("/"):
        atual = ET.SubElement(atual, parte)
    return atual


# --- A copia isolada --------------------------------------------------------


def test_copia_isolada_aponta_o_audio_para_dentro_do_pacote(tmp_path):
    """Com o locator externo existindo, a copia reescreve para a portadora local."""
    externo = tmp_path / "externo"
    externo.mkdir()
    origem = tmp_path / "origem.aaf"
    _copia_com_locator_externo(origem, externo)

    pasta = tmp_path / "caso"
    copia = preparar_copia_isolada(origem, pasta, pasta / "midia", cheio=False)

    urls = _urls_do_aaf(copia)
    assert urls
    dentro_do_pacote = 0
    for url in urls:
        caminho = Path(_url_para_caminho(url))
        assert externo.resolve() not in caminho.resolve().parents, url
        if pasta.resolve() in caminho.resolve().parents:
            dentro_do_pacote += 1
    assert dentro_do_pacote >= 1


def test_copia_isolada_nao_altera_a_fixture_nem_a_midia_externa(tmp_path):
    """A fixture original e os arquivos de fora ficam byte a byte como estavam."""
    externo = tmp_path / "externo"
    externo.mkdir()
    origem = tmp_path / "origem.aaf"
    externos = _copia_com_locator_externo(origem, externo)

    antes_fixture = sha256_arquivo(FIXTURE_AAF)
    antes_externos = {caminho: sha256_arquivo(caminho) for caminho in externos}

    preparar_copia_isolada(origem, tmp_path / "caso", tmp_path / "caso" / "midia", cheio=False)

    assert sha256_arquivo(FIXTURE_AAF) == antes_fixture
    assert {caminho: sha256_arquivo(caminho) for caminho in externos} == antes_externos


def test_copia_isolada_guarda_a_estrutura_do_aaf(tmp_path):
    """MasterMob, slots e essencia ficam como estavam: so o caminho muda."""
    externo = tmp_path / "externo"
    externo.mkdir()
    origem = tmp_path / "origem.aaf"
    _copia_com_locator_externo(origem, externo)

    antes = _estrutura_do_aaf(origem)
    copia = preparar_copia_isolada(origem, tmp_path / "caso", tmp_path / "caso" / "midia")

    assert _estrutura_do_aaf(copia) == antes


def test_midia_ja_resolvida_tambem_recebe_portadora_local():
    """Asset com status resolvido tambem ganha portadora: nenhum fica de fora."""
    asset = MediaAsset(
        id="m0", filename="fonte.wav", channels=1, sample_rate=48000, status="resolved"
    )
    portadoras = agrupar_portadoras(_sessao_simples([asset]))
    chave = chave_do_nome("fonte.wav")
    assert list(portadoras) == [chave]
    assert portadoras[chave].nome == "fonte.wav"


def test_colisao_de_taxa_ou_canais_e_recusada():
    """Dois assets com o mesmo nome e formatos diferentes nao dividem portadora."""
    sessao = _sessao_simples(
        [
            MediaAsset(id="m0", filename="fonte.wav", channels=1, sample_rate=48000),
            MediaAsset(id="m1", filename="FONTE.WAV", channels=2, sample_rate=48000),
        ]
    )
    with pytest.raises(MidiaLocalInvalida) as erro:
        agrupar_portadoras(sessao)
    assert "channels" in str(erro.value)


def test_preparo_que_ja_existe_e_recusado_sem_perder_nada(tmp_path):
    """A pasta de preparo que ja existe nao e reaproveitada nem apagada.

    O preparo e o rascunho da conversao, e a conversao apaga a pasta quando
    acaba: uma pasta que ja existia e de outra pessoa. A recusa vem antes de
    copiar ou escrever, e o que estava la fica como estava.
    """
    preparacao = tmp_path / "preparo"
    preparacao.mkdir()
    keep = preparacao / "keep.txt"
    keep.write_bytes(b"conteudo que a conversao nao pode perder")
    nota = tmp_path / "nota.txt"
    nota.write_bytes(b"anotacao de quem montou o caso")
    fixture = REPO / "tests" / "fixtures" / "aaf" / "TESTE_DF_2997.aaf"
    antes = sha256_arquivo(fixture)

    with pytest.raises(MidiaLocalInvalida) as erro:
        converter_fixture("TESTE_DF_2997", tmp_path, isolada=True, cheio=False)

    assert "preparo" in str(erro.value)
    assert keep.read_bytes() == b"conteudo que a conversao nao pode perder"
    assert nota.read_bytes() == b"anotacao de quem montou o caso"
    assert sorted(pasta.name for pasta in preparacao.iterdir()) == ["keep.txt"]
    assert sha256_arquivo(fixture) == antes


# --- O caso C04 do pacote ---------------------------------------------------


@pytest.fixture(scope="module")
def pacote_c04(tmp_path_factory):
    """O pacote do caso C04, preparado como o operador o receberia."""
    destino = tmp_path_factory.mktemp("pacote-c04")
    antes = sha256_arquivo(FIXTURE_AAF)
    pacote_live.preparar(destino, ["C04"])
    return destino, antes


def _caso(pacote: Path) -> dict:
    manifesto = json.loads((pacote / "manifesto.json").read_text(encoding="utf-8"))
    return manifesto["casos"][0]


def test_c04_nao_altera_a_fixture_original(pacote_c04):
    """A fixture versionada fica byte a byte como estava depois do preparo."""
    pacote, antes = pacote_c04
    assert sha256_arquivo(FIXTURE_AAF) == antes
    assert _caso(pacote)["id"] == "C04"


def test_c04_aponta_todas_as_referencias_para_dentro_do_pacote(pacote_c04):
    """Nenhuma FileRef de audio do .als gerado aponta para fora do pacote."""
    pacote, _ = pacote_c04
    caso = _caso(pacote)
    midias = midias_efetivas(pacote / caso["als_gerado"])
    assert midias
    for caminho in midias:
        assert pacote.resolve() in Path(caminho).resolve().parents


def test_c04_preserva_os_grupos_e_os_90_eventos(pacote_c04):
    """Os grupos do Live e os 90 eventos de audio continuam no .als gerado."""
    pacote, _ = pacote_c04
    caso = _caso(pacote)
    raiz = load_als(pacote / caso["als_gerado"])
    assert list(raiz.iter("GroupTrack"))

    conversao = Conversao(
        pacote / caso["aaf"],
        pacote / caso["als_gerado"],
        0,
        (pacote / caso["caminhos_proveniente"]["relatorio"]).read_text(encoding="utf-8"),
    )
    medida = perfeicao_antes(conversao, caso["dialeto"])
    assert medida.entrada == 90
    assert medida.exatos == 90
    assert medida.perdas_d == 0
    assert medida.valor == 1.0


def test_c04_portadoras_tem_o_comprimento_que_os_clips_pedem(pacote_c04):
    """A portadora tem o comprimento do maior trecho pedido e e silencio."""
    pacote, _ = pacote_c04
    caso = _caso(pacote)
    aaf = pacote / caso["aaf"]
    midia = aaf.parent.parent / "midia"
    sessao = read_aaf(aaf, Report(strict=False))
    for asset in sessao.media:
        if not asset.filename:
            continue
        portadora = midia / Path(asset.filename).name
        assert portadora.is_file(), asset.filename
        assert _quadros(portadora) >= _amostras_da_portadora(sessao, asset, True)
        assert set(_pcm(portadora)) == {0}


def test_inventario_de_midia_tem_caminho_relativo_e_hash(pacote_c04):
    """O manifesto amarra, por hash, as midias que o .als gerado referencia."""
    pacote, _ = pacote_c04
    caso = _caso(pacote)
    inventario = caso["midias_proveniente"]
    assert inventario
    for entrada in inventario:
        caminho = pacote / entrada["caminho"]
        assert caminho.is_file()
        assert not Path(entrada["caminho"]).is_absolute()
        assert sha256_arquivo(caminho) == entrada["sha256"]


def test_inventario_cobre_as_referencias_do_als_gerado(pacote_c04):
    """A lista do inventario e a lista das referencias do .als, sem sobra."""
    pacote, _ = pacote_c04
    caso = _caso(pacote)
    do_manifesto = {(pacote / e["caminho"]).resolve() for e in caso["midias_proveniente"]}
    do_als = {Path(c).resolve() for c in midias_efetivas(pacote / caso["als_gerado"])}
    assert do_manifesto == do_als


def test_inventario_recusa_midia_que_sai_do_pacote(tmp_path):
    """A midia do inventario tem de morar dentro do pacote."""
    pacote = tmp_path / "pacote"
    pacote.mkdir()
    fora = tmp_path / "fora.wav"
    fora.write_bytes(aaf_factory.wav_bytes(480, channels=1, sample_rate=48000))
    als = _als_com_midia(tmp_path / "x.als", fora)
    with pytest.raises(MidiaLocalInvalida):
        inventario_de_midias(pacote, als)
