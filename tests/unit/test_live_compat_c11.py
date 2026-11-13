"""C04 e C11 do pacote do oraculo diferencial: grupos multicanal e clip estereo.

O C11 pergunta se o Live abre um AAF do Pro Tools com combiner como uma track
estereo com um clip so. O C04 pergunta se as tracks multicanais chegam
agrupadas, um canal por track. Os dois casos medem a estrutura final, os
canais do arquivo e a verificacao completa. O oraculo e o proprio AAF e o
proprio WAV: nem o escritor do produto nem o leitor do produto sao usados para
dizer o que e esperado.
"""

from __future__ import annotations

import json
import os
import struct
import sys
import wave
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

import aaf2
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO))
import prepare_live_compat as pacote_live  # noqa: E402

from aafton.als_read_clips import track_name  # noqa: E402
from aafton.als_xml import get_value, load_als  # noqa: E402
from aafton.sidecar import sidecar_path_for  # noqa: E402
from aafton.verify import verify_conversion  # noqa: E402
from scripts.live_compat_c11 import AMPLITUDE, DURACAO, SR  # noqa: E402


@pytest.fixture(scope="module")
def pacote(tmp_path_factory):
    """O pacote com o C11 so: o caso que promete pista e clip estereo."""
    destino = tmp_path_factory.mktemp("pacote-c11")
    pacote_live.preparar(destino, ["C11"])
    return destino


@pytest.fixture(scope="module")
def pacote_c04(tmp_path_factory):
    """O pacote com o C04 so: o caso que promete um grupo por track multicanal."""
    destino = tmp_path_factory.mktemp("pacote-c04")
    pacote_live.preparar(destino, ["C04"])
    return destino


def _caso(pacote: Path) -> dict:
    manifesto = json.loads((pacote / "manifesto.json").read_text(encoding="utf-8"))
    return manifesto["casos"][0]


def _quadros(caminho: Path) -> bytes:
    """Os samples de um WAV, lidos do proprio arquivo."""
    with wave.open(str(caminho), "rb") as handle:
        return handle.readframes(handle.getnframes())


def _slot_de_audio(composition) -> object:
    """O slot de audio do AAF: o unico que carrega os clips."""
    return next(slot for slot in composition.slots if slot.segment.media_kind == "Sound")


def _caminho_de_file_url(partes) -> str:
    """Caminho de uma URL file://, com a autoridade preservada e o caminho lido.

    A autoridade de uma URL file:// e o host do arquivo. Um host local (vazio
    ou localhost) nao muda o caminho; um servidor faz parte dele, e descarta-lo
    apontaria para um arquivo que nao existe. O caminho percent-decodificado
    porque %20 e um espaco do nome do arquivo, e nao quatro caracteres: o
    url2pathname do host local ja faz essa leitura.
    """
    autoridade = (partes.netloc or "").strip()
    if not autoridade or autoridade.lower() == "localhost":
        # url2pathname devolve a raiz local do host (POSIX) e a drive do Windows.
        return url2pathname(partes.path)
    caminho = unquote(partes.path)
    if os.name == "nt":
        # Caminho UNC: o servidor e a raiz, com as duas barras iniciais.
        return "\\\\" + autoridade + caminho.replace("/", "\\")
    return "//" + autoridade + caminho


def _caminho_de_url(locator: str) -> Path:
    """Caminho de um locator: a URL file:// vira caminho do sistema de arquivos."""
    partes = urlparse(locator)
    if partes.scheme == "file":
        return Path(_caminho_de_file_url(partes))
    return Path(locator)


def _caminho_da_fonte(fonte) -> Path:
    """O WAV que a fonte do combiner aponta, pelo Locator do SourceMob."""
    master = fonte.mob.slot_at(fonte.slot_id)
    locator = master.segment.mob.descriptor["Locator"].value[0]["URLString"].value
    return _caminho_de_url(locator)


def test_pergunta_do_c11_promete_um_clip_estereo(pacote):
    """A pergunta e o contrato do caso: ela nao pode virar promessa de mono."""
    caso = _caso(pacote)
    assert caso["id"] == "C11"
    assert "estereo" in caso["pergunta"]
    assert "Resultado esperado:" in caso["pergunta"]


def test_c11_gera_uma_track_e_um_clip_estereo(pacote):
    """Uma track, um clip, e o WAV final com dois canais de sons diferentes."""
    caso = _caso(pacote)
    raiz = load_als(pacote / caso["als_de_entrada"])
    assert len(raiz.findall(".//Tracks/AudioTrack")) == 1
    clips = raiz.findall(".//AudioClip")
    assert len(clips) == 1
    caminho = Path(clips[0].find("SampleRef/FileRef/Path").get("Value"))
    assert caminho.is_file()
    with wave.open(str(caminho), "rb") as handle:
        assert handle.getnchannels() == 2
        assert handle.getframerate() == SR
        assert handle.getnframes() == DURACAO
        quadros = handle.readframes(DURACAO)
    # O canal da direita e o oposto do da esquerda: estao no mesmo arquivo e
    # nao a mesma fonte repetida.
    assert quadros == struct.pack("<2h", AMPLITUDE, -AMPLITUDE) * DURACAO


def _combiner_do_c11(aaf: Path) -> list[Path]:
    """As fontes do combiner do AAF do C11: duas, de arquivos distintos."""
    with aaf2.open(str(aaf), "r") as f:
        composition = next(f.content.compositionmobs())
        slots = [slot for slot in composition.slots if slot.segment.media_kind == "Sound"]
        assert len(slots) == 1
        componentes = list(_slot_de_audio(composition).segment.components)
        assert [type(componente).__name__ for componente in componentes] == ["OperationGroup"]
        combiner = componentes[0]
        assert len(combiner.segments) == 2
        assert len({str(fonte.mob.mob_id) for fonte in combiner.segments}) == 2
        return [_caminho_da_fonte(fonte) for fonte in combiner.segments]


def test_aaf_do_c11_tem_combiner_com_duas_fontes_distintas(pacote):
    """Um combiner, duas fontes, e cada uma com o som do seu canal."""
    caso = _caso(pacote)
    caminhos = _combiner_do_c11(pacote / caso["aaf"])
    assert len(set(caminhos)) == 2
    for caminho in caminhos:
        assert caminho.is_file()
    esquerda = struct.pack("<h", AMPLITUDE) * DURACAO
    direita = struct.pack("<h", -AMPLITUDE) * DURACAO
    assert sorted(_quadros(caminho) for caminho in caminhos) == sorted([esquerda, direita])


def test_verify_do_c11_compara_os_dois_eventos_sem_desvio(pacote):
    """Dois SourceClips esperados, dois comparados, nenhum desvio."""
    caso = _caso(pacote)
    gerado = pacote / caso["als_gerado"]
    assert sidecar_path_for(gerado).is_file()
    resultado = verify_conversion(pacote / caso["aaf"], gerado)
    assert resultado.audio_expected == 2
    assert resultado.audio_compared == 2
    assert resultado.audio_missing == 0
    assert resultado.audio_extra == 0
    assert [linha.status for linha in resultado.rows] == ["ok", "ok"]
    assert resultado.max_delta_samples == 0
    assert resultado.ok


def test_autoridade_de_file_url_nao_e_descartada():
    """file://servidor aponta para o servidor, e o host local mantem a raiz.

    O teste so converte texto: nenhum acesso a rede acontece aqui.
    """
    caminho = _caminho_de_url("file://servidor/compartilhamento/fonte.wav")
    assert "servidor" in str(caminho)
    assert caminho != _caminho_de_url("file:///compartilhamento/fonte.wav")
    local = _caminho_de_url("file:///C:/midia/fonte.wav")
    assert str(_caminho_de_url("file://localhost/C:/midia/fonte.wav")) == str(local)
    assert local.name == "fonte.wav"
    if os.name == "nt":
        assert local.drive.upper() == "C:"


def test_caminho_de_file_url_e_percent_decodificado():
    """O %20 do caminho e um espaco do arquivo, e nao quatro caracteres.

    O servidor continua no caminho e o nome do arquivo vem lido: so isso
    converte texto, sem tocar na rede.
    """
    caminho = _caminho_de_url("file://servidor/share/Audio%20Files/canal%23.wav")
    assert "servidor" in str(caminho)
    assert "%20" not in str(caminho)
    assert "Audio Files" in str(caminho)
    assert str(caminho).endswith("canal#.wav")


def _grupos(raiz) -> dict:
    """Os GroupTrack do .als, pelo nome."""
    return {track_name(node): node for node in raiz.findall(".//LiveSet/Tracks/GroupTrack")}


def _filhos(raiz, grupo) -> list:
    """As AudioTracks que apontam para o grupo dado."""
    gid = grupo.get("Id")
    trilhas = raiz.findall(".//LiveSet/Tracks/AudioTrack")
    return [n for n in trilhas if get_value(n, "TrackGroupId") == gid]


def test_c04_agrupa_a_5_1_e_a_ambisonica(pacote_c04):
    """Um grupo por track multicanal: seis canais na 5.1, quatro na ambisonica.

    Cada filho aponta para o Id do seu grupo, e nenhum nome se repete.
    """
    caso = _caso(pacote_c04)
    raiz = load_als(pacote_c04 / caso["als_de_entrada"])
    grupos = _grupos(raiz)
    assert set(grupos) == {"5.1", "AMB 1ST"}
    assert len(_filhos(raiz, grupos["5.1"])) == 6
    assert len(_filhos(raiz, grupos["AMB 1ST"])) == 4
    for grupo in grupos.values():
        filhos = _filhos(raiz, grupo)
        assert filhos
        for filho in filhos:
            assert get_value(filho, "TrackGroupId") == grupo.get("Id")
    ids = {grupo.get("Id") for grupo in grupos.values()}
    apontados = {get_value(n, "TrackGroupId") for n in raiz.findall(".//LiveSet/Tracks/AudioTrack")}
    assert apontados <= ids | {"-1"}
    nomes = [track_name(node) for node in raiz.findall(".//LiveSet/Tracks/AudioTrack")]
    assert len(nomes) == len(set(nomes))


def test_c04_verifica_os_90_eventos_sem_desvio(pacote_c04):
    """Os 90 eventos de audio do AAF chegam todos, com desvio zero."""
    caso = _caso(pacote_c04)
    resultado = verify_conversion(pacote_c04 / caso["aaf"], pacote_c04 / caso["als_gerado"])
    assert resultado.audio_expected == 90
    assert resultado.audio_compared == 90
    assert resultado.audio_missing == 0
    assert resultado.audio_extra == 0
    assert resultado.max_delta_samples == 0
