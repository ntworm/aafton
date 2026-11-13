"""E1 do R07.6: o comparador semantico de dois .als.

O par que nao difere e o .als gerado contra ele mesmo depois de tudo que o
Live muda sem mudar o significado (gzip, ordem dos filhos, LomId, Id do clip,
Revision, relocacao da midia). O par que difere tem uma mutacao por classe.
Cada teste diz qual seria o defeito se ele falhar.
"""

from __future__ import annotations

import copy
import gzip
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from aafton import als_paths
from aafton.als_compare import (
    AUSENTE_NO_LIVE,
    DROPPED_POR_LIVE,
    RESCRITO_POR_LIVE,
    VALOR_DIVERGENTE,
    carregar,
    comparar,
    contagem,
    identidade,
    tabela_markdown,
)
from aafton.als_compare_campos import (
    CAMINHO_CLIPS,
    CAMPOS_CLIP,
    CAMPOS_LIVESET,
    CAMPOS_LOCATOR,
    CAMPOS_TRACK,
    RUIDO_PADRAO,
)
from aafton.als_write import WriteOptions, write_session
from aafton.ir import ClipIR, FadeIR, MarkerIR, MediaAsset, SessionIR, TrackIR
from aafton.report import Report
from aafton.timecode import TimecodeRate

TEMPLATE = Path("templates/live12.als")
SR = 48000
ORIGEM = 3600 * SR


def _sessao(tmp_path: Path) -> SessionIR:
    wav = tmp_path / "cena.wav"
    wav.write_bytes(b"RIFF")
    asset = MediaAsset(
        id="m1",
        filename="cena.wav",
        channels=1,
        sample_rate=SR,
        duration_samples=60 * SR,
        status="resolved",
        resolved_path=str(wav),
    )
    tracks = []
    for indice, pan in enumerate((0.25, 0.75)):
        track = TrackIR(
            id=f"t{indice}",
            source_index=indice,
            name=f"DX {indice + 1}",
            kind="audio",
            channel_count=1,
            pan=pan,
            gain_linear=0.5,
        )
        track.clips.append(
            ClipIR(
                id=f"c{indice}",
                track_id=track.id,
                channel_index=0,
                event_index=0,
                name="cena.wav",
                media_id="m1",
                timeline_start_samples=ORIGEM + 12345 + indice * SR,
                timeline_end_samples=ORIGEM + 12345 + (indice + 2) * SR,
                source_offset_samples=480,
                gain_linear=0.7071067811865476,
                fade_in=FadeIR(kind="in", duration_samples=4800),
                fade_out=FadeIR(kind="out", duration_samples=9600),
            )
        )
        tracks.append(track)
    return SessionIR(
        name="comparador",
        sample_rate=SR,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=ORIGEM,
        tracks=tracks,
        media=[asset],
        markers=[MarkerIR(id="k1", name="CENA 1", position_samples=ORIGEM + SR, comment="x")],
    )


@pytest.fixture
def gerado(tmp_path: Path) -> Path:
    destino = tmp_path / "gerado.als"
    write_session(
        _sessao(tmp_path),
        TEMPLATE,
        destino,
        WriteOptions(sample_rate=SR, live_origin_samples=ORIGEM, permissive=True, groups="prefix"),
        Report(strict=False),
    )
    return destino


def _raiz(caminho: Path) -> ET.Element:
    return carregar(caminho)


def _clips(raiz: ET.Element) -> list[ET.Element]:
    return raiz.findall(f"LiveSet/Tracks/AudioTrack/{CAMINHO_CLIPS}")


def _como_o_live_salva(raiz: ET.Element) -> ET.Element:
    """O que o Save As muda sem mudar o significado."""
    salvo = copy.deepcopy(raiz)
    salvo.set("Revision", "outra-revisao")
    for indice, no in enumerate(salvo.iter()):
        if no.tag in ("LomId", "LomIdView"):
            no.set("Value", str(indice))
        if no.tag == "IsContentSelectedInDocument":
            no.set("Value", "true")
    for clip in _clips(salvo):
        clip.set("Id", str(int(clip.get("Id", "0")) + 77))
        clip[:] = list(reversed(list(clip)))
        caminho = clip.find("SampleRef/FileRef/Path").get("Value")
        clip.find("SampleRef/FileRef/RelativePathType").set("Value", "1")
        clip.find("SampleRef/FileRef/RelativePath").set("Value", Path(caminho).name)
    _renumerar_tracks(salvo, deslocamento=1000)
    for eventos in salvo.iter("Events"):
        eventos[:] = list(reversed(list(eventos)))
    return salvo


def _renumerar_tracks(raiz: ET.Element, deslocamento: int) -> None:
    """Troca o Id das tracks e os ponteiros TrackGroupId junto, como um Live renumerando."""
    tracks = raiz.find("LiveSet/Tracks")
    novos = {t.get("Id"): str(int(t.get("Id")) + deslocamento) for t in tracks}
    for track in tracks:
        track.set("Id", novos[track.get("Id")])
        grupo = track.find("TrackGroupId")
        if grupo is not None and grupo.get("Value") in novos:
            grupo.set("Value", novos[grupo.get("Value")])


def _salvar_xml_puro(raiz: ET.Element, destino: Path) -> Path:
    destino.write_bytes(ET.tostring(raiz, encoding="utf-8"))
    return destino


def test_o_gerado_contra_ele_mesmo_nao_difere(gerado):
    assert comparar(gerado, gerado) == []


def test_o_ruido_do_save_as_nao_vira_diferenca(gerado, tmp_path):
    """gzip, ordem, LomId, Id do clip, Revision, ids de track e relocacao sao ruido."""
    salvo = _salvar_xml_puro(_como_o_live_salva(_raiz(gerado)), tmp_path / "salvo.xml")
    diferencas = comparar(gerado, salvo)
    assert diferencas == [], (
        "o comparador acusou ruido do Save As como diferenca; acrescente o item a "
        f"RUIDO_PADRAO so se for ruido de verdade: {diferencas[:5]}"
    )


def test_o_gerado_tem_grupo_e_o_ponteiro_e_resolvido_pelo_nome(gerado):
    raiz = _raiz(gerado)
    assert raiz.find("LiveSet/Tracks/GroupTrack") is not None, "o caso precisa de um grupo"
    _renumerar_tracks(raiz, deslocamento=5)
    assert comparar(gerado, raiz) == []
    grupo = raiz.find("LiveSet/Tracks/AudioTrack/TrackGroupId")
    grupo.set("Value", "-1")
    [diferenca] = comparar(gerado, raiz)
    assert diferenca.caminho.endswith("/TrackGroupId")
    assert diferenca.devolvido == "-"
    assert diferenca.classe == VALOR_DIVERGENTE


def test_posicao_reescrita_num_campo_escrito_e_valor_divergente(gerado):
    raiz = _raiz(gerado)
    _clips(raiz)[0].find("CurrentStart").set("Value", "1.5")
    [diferenca] = comparar(gerado, raiz)
    assert diferenca.caminho.endswith("/CurrentStart")
    assert diferenca.classe == VALOR_DIVERGENTE
    assert diferenca.escrito and not diferenca.defeito


def test_o_atributo_time_do_clip_e_comparado(gerado):
    """A posicao no arranjo e o atributo Time; o filho <Time> nao existe no clip."""
    raiz = _raiz(gerado)
    _clips(raiz)[0].set("Time", "99")
    [diferenca] = comparar(gerado, raiz)
    assert diferenca.caminho.endswith("/@Time")
    assert diferenca.classe == VALOR_DIVERGENTE


def test_campo_escrito_que_some_e_dropped_e_defeito(gerado):
    raiz = _raiz(gerado)
    fades = _clips(raiz)[0].find("Fades")
    fades.remove(fades.find("FadeInLength"))
    [diferenca] = comparar(gerado, raiz)
    assert diferenca.classe == DROPPED_POR_LIVE
    assert diferenca.defeito, "DROPPED num campo que o aafton escreve tem de ser defeito"


def test_clip_que_some_e_uma_linha_so_dropped(gerado):
    raiz = _raiz(gerado)
    clip = _clips(raiz)[0]
    for eventos in raiz.iter("Events"):
        if clip in list(eventos):
            eventos.remove(clip)
    diferencas = comparar(gerado, raiz)
    assert [d.classe for d in diferencas] == [DROPPED_POR_LIVE]
    assert "/AudioClip[" in diferencas[0].caminho


def test_campo_herdado_mudado_pelo_live_e_rescrito(gerado):
    raiz = _raiz(gerado)
    _clips(raiz)[0].find("Color").set("Value", "13")
    [diferenca] = comparar(gerado, raiz)
    assert diferenca.classe == RESCRITO_POR_LIVE
    assert not diferenca.escrito


def test_campo_herdado_que_some_e_ausente_no_live(gerado):
    raiz = _raiz(gerado)
    clip = _clips(raiz)[0]
    clip.remove(clip.find("WarpMode"))
    [diferenca] = comparar(gerado, raiz)
    assert diferenca.classe == AUSENTE_NO_LIVE
    assert not diferenca.defeito


def test_campo_que_o_live_acrescenta_e_rescrito(gerado):
    raiz = _raiz(gerado)
    raiz.find("LiveSet/Grid").append(ET.Element("CampoDeOutroBuild", Value="1"))
    [diferenca] = comparar(gerado, raiz)
    assert diferenca.classe == RESCRITO_POR_LIVE
    assert diferenca.gerado is None


def test_filho_herdado_do_grid_que_some_e_ausente_no_live(gerado):
    """A grade vem do template: o Live tira-la nao e defeito do aafton."""
    raiz = _raiz(gerado)
    grade = raiz.find("LiveSet/Grid")
    assert grade is not None and len(grade), "o caso precisa de uma grade no template"
    removido = grade[0]
    grade.remove(removido)
    [diferenca] = comparar(gerado, raiz)
    assert diferenca.caminho.endswith(f"/Grid/{removido.tag}")
    assert diferenca.classe == AUSENTE_NO_LIVE
    assert not diferenca.defeito


def test_float32_e_regra_declarada_nao_divergencia(gerado):
    raiz = _raiz(gerado)
    volume = _clips(raiz)[0].find("SampleVolume")
    volume.set("Value", "0.70710676908493042")
    [diferenca] = comparar(gerado, raiz)
    assert diferenca.classe == RESCRITO_POR_LIVE
    assert diferenca.regra and diferenca.regra.startswith("float32")


def test_relocacao_so_e_ruido_quando_o_live_relativiza(gerado):
    raiz = _raiz(gerado)
    _clips(raiz)[0].find("SampleRef/FileRef/RelativePathType").set("Value", "3")
    [diferenca] = comparar(gerado, raiz)
    assert diferenca.caminho.endswith("RelativePathType")
    assert diferenca.classe == RESCRITO_POR_LIVE


def test_automacao_e_comparada_pelo_parametro_nao_pelo_id(tmp_path):
    sessao = _sessao(tmp_path)
    sessao.tracks[0].gain_points = [(ORIGEM, 1.0), (ORIGEM + SR, 0.5)]
    destino = tmp_path / "automacao.als"
    write_session(
        sessao,
        TEMPLATE,
        destino,
        WriteOptions(sample_rate=SR, live_origin_samples=ORIGEM, permissive=True, groups="prefix"),
        Report(strict=False),
    )
    raiz = _raiz(destino)
    envelope = raiz.find(".//AutomationEnvelopes/Envelopes/AutomationEnvelope")
    assert envelope is not None, "o caso precisa de um envelope de ganho"
    eventos = envelope.find("Automation/Events")
    eventos[-1].set("Value", "0.25")
    [diferenca] = comparar(destino, raiz)
    assert "/Automacao[Volume#0]" in diferenca.caminho
    assert diferenca.classe == VALOR_DIVERGENTE


def test_gzip_e_xml_puro_sao_o_mesmo_documento(gerado, tmp_path):
    puro = _salvar_xml_puro(_raiz(gerado), tmp_path / "puro.xml")
    regzipado = tmp_path / "regzipado.als"
    regzipado.write_bytes(gzip.compress(puro.read_bytes()))
    assert comparar(puro, regzipado) == []
    assert identidade(regzipado)["MinorVersion"] == identidade(gerado)["MinorVersion"]


def test_contagem_e_tabela_trazem_as_quatro_classes(gerado):
    raiz = _raiz(gerado)
    _clips(raiz)[0].find("CurrentStart").set("Value", "1.5")
    diferencas = comparar(gerado, raiz)
    assert contagem(diferencas) == {
        DROPPED_POR_LIVE: 0,
        VALOR_DIVERGENTE: 1,
        RESCRITO_POR_LIVE: 0,
        AUSENTE_NO_LIVE: 0,
    }
    tabela = tabela_markdown(diferencas, caso="C01")
    assert "| C01 |" in tabela and VALOR_DIVERGENTE in tabela


def test_o_ruido_declarado_e_a_lista_do_contrato():
    """A lista de ruido e declarada, nao espalhada: mudar e decisao, nao ajuste."""
    assert {"LomId", "LomIdView", "IsContentSelectedInDocument", "ViewData"} <= RUIDO_PADRAO.tags
    assert {"Id", "Revision"} <= RUIDO_PADRAO.atributos
    assert any(c.endswith("RelativePathType") for c in RUIDO_PADRAO.relocacao)


@pytest.mark.parametrize(
    ("mapa", "campos"),
    [
        (als_paths.AUDIO_CLIP_FIELDS, CAMPOS_CLIP),
        (als_paths.LOCATOR_FIELDS, CAMPOS_LOCATOR),
        (als_paths.LIVESET_FIELDS, CAMPOS_LIVESET),
    ],
)
def test_todo_campo_declarado_pelo_escritor_e_comparado_como_escrito(mapa, campos):
    escritos = {c.caminho for c in campos if c.escrito}
    faltam = sorted(set(mapa.values()) - escritos)
    assert not faltam, (
        f"o escritor declara {faltam} em als_paths e o comparador nao compara como "
        "escrito; um DROPPED ali passaria por AUSENTE_NO_LIVE"
    )


def test_todo_campo_de_track_do_escritor_e_comparado():
    escritos = {c.caminho for c in CAMPOS_TRACK if c.escrito}
    # o alvo da automacao e os eventos do arranjo sao comparados pela automacao e pelos clips
    estruturais = {
        "DeviceChain/Mixer/Volume/AutomationTarget",
        als_paths.TRACK_FIELDS["arrangement_events"],
    }
    faltam = sorted(set(als_paths.TRACK_FIELDS.values()) - escritos - estruturais)
    assert not faltam, f"campos de track escritos e nao comparados: {faltam}"
