"""Testes da aba Collect media do Studio e da coleta no export das duas abas."""

from __future__ import annotations

import gzip
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from aafton.als_xml import load_als
from aafton.collect import CollectError
from aafton.collect_aaf import read_locator_urls
from aafton.studio import app_collect, app_convert, app_routes, app_set
from tests.aaf_factory import build_calibration_aaf, build_linked_aaf, wav_bytes
from tests.unit.test_als_sem_sidecar import clip, escrever_wav, montar_set

TEMPLATE = Path("templates/live12.als")


@pytest.fixture(autouse=True)
def _limpo():
    """Cada teste comeca e termina com LAST vazio."""
    app_convert.LAST.clear()
    yield
    app_convert.LAST.clear()


def _sub(pai: ET.Element, caminho: str, **atributos) -> ET.Element:
    """Cria os elementos do caminho; o ultimo recebe os atributos."""
    node = pai
    partes = caminho.split("/")
    for parte in partes[:-1]:
        filho = node.find(parte)
        node = filho if filho is not None else ET.SubElement(node, parte)
    return ET.SubElement(node, partes[-1], **atributos)


def _valor(pai: ET.Element, caminho: str, valor) -> None:
    _sub(pai, caminho, Value=str(valor))


def _adicionar_clip_als(
    eventos: ET.Element,
    indice: int,
    *,
    nome: str,
    rel_type: str,
    rel_path: str,
    abs_path: str,
    is_video: bool = False,
) -> None:
    """Adiciona um AudioClip ao eventos XML com os campos do FileRef."""
    node = ET.SubElement(eventos, "AudioClip", Id=str(indice), Time=str(indice * 4))
    _valor(node, "CurrentStart", indice * 4)
    _valor(node, "CurrentEnd", indice * 4 + 4)
    _valor(node, "Name", nome)
    _valor(node, "SampleRef/FileRef/RelativePathType", rel_type)
    _valor(node, "SampleRef/FileRef/RelativePath", rel_path)
    _valor(node, "SampleRef/FileRef/Path", abs_path)
    if is_video:
        _valor(node, "SampleRef/FileRef/Type", "1")
    _valor(node, "Loop/LoopStart", 0.0)
    _valor(node, "Loop/LoopEnd", 4.0)
    _valor(node, "Loop/LoopOn", "false")
    _valor(node, "IsWarped", "false")


def _montar_set_collect(tmp_path: Path) -> tuple[Path, dict[str, Path]]:
    """Monta um .als sintetico com video, 2 WAVs de mesmo nome, missing e library."""
    proj = tmp_path / "Sessao Project"
    (proj / "Ableton Project Info").mkdir(parents=True)
    pasta_a = proj / "Samples" / "FolderA"
    pasta_b = proj / "Samples" / "FolderB"
    pasta_v = proj / "Video"
    pasta_a.mkdir(parents=True)
    pasta_b.mkdir(parents=True)
    pasta_v.mkdir(parents=True)

    wav1 = pasta_a / "amb.wav"
    wav2 = pasta_b / "amb.wav"
    vid = pasta_v / "corte.mp4"
    wav1.write_bytes(wav_bytes(48000, 1, 48000))
    wav2.write_bytes(wav_bytes(48000 * 2, 2, 48000))
    vid.write_bytes(b"video-bytes-" * 200)
    missing = proj / "Foley" / "porta.wav"

    root = ET.Element("Ableton", Creator="Ableton Live 12.4.6")
    live = ET.SubElement(root, "LiveSet")
    tracks = ET.SubElement(live, "Tracks")
    track = ET.SubElement(tracks, "AudioTrack", Id="10")
    _valor(track, "Name/EffectiveName", "DX")
    eventos = _sub(track, "DeviceChain/MainSequencer/Sample/ArrangerAutomation/Events")

    _adicionar_clip_als(
        eventos,
        0,
        nome="corte",
        rel_type="3",
        rel_path="Video/corte.mp4",
        abs_path=str(vid),
        is_video=True,
    )
    _adicionar_clip_als(
        eventos,
        1,
        nome="amb_a",
        rel_type="3",
        rel_path="Samples/FolderA/amb.wav",
        abs_path=str(wav1),
    )
    _adicionar_clip_als(
        eventos,
        2,
        nome="amb_b",
        rel_type="3",
        rel_path="Samples/FolderB/amb.wav",
        abs_path=str(wav2),
    )
    _adicionar_clip_als(
        eventos,
        3,
        nome="porta",
        rel_type="3",
        rel_path="Foley/porta.wav",
        abs_path=str(missing),
    )
    _adicionar_clip_als(
        eventos,
        4,
        nome="kick",
        rel_type="5",
        rel_path="Samples/Drums/Kick.aif",
        abs_path="/Library/Samples/Drums/Kick.aif",
    )

    als_path = proj / "sessao.als"
    with gzip.open(als_path, "wb") as handle:
        handle.write(ET.tostring(root, encoding="utf-8"))
    return als_path, {"wav1": wav1, "wav2": wav2, "video": vid, "missing": missing}


class _FakeHandler:
    """Handler falso para exercitar app_routes.handle_post."""

    def __init__(self) -> None:
        self.status = 0
        self.data: dict = {}

    def _send_json(self, data: dict, status: int = 200) -> None:
        self.status = status
        self.data = data


# --- /api/collect/read e /api/collect/run ------------------------------------


def test_collect_read_e_run_em_set_sintetico(tmp_path, monkeypatch):
    als, arquivos = _montar_set_collect(tmp_path)

    leitura = app_collect.read({"input": str(als)})

    assert leitura["ok"] is True
    assert leitura["direction"] == "collect"
    assert leitura["source"]["format"] == "als"
    assert leitura["source"]["session_sub"] == "Ableton Live 12.4.6"

    fatos = {f["k"]: f for f in leitura["facts"]}
    assert fatos["Session"]["v"] == "Live set"
    assert fatos["Session"]["sub"] == "Ableton Live 12.4.6"
    assert fatos["Files"]["v"] == "5"
    assert fatos["Files"]["sub"] == "1 missing, 1 in Live's library"
    assert fatos["Files"]["st"] == "err"
    assert fatos["To copy"]["sub"].startswith("picture ")

    estados = [it["state"] for it in leitura["items"]]
    assert estados == [
        {"st": "ok", "label": "Copy"},
        {"st": "ok", "label": "Copy"},
        {"st": "warn", "label": "Copy, renamed"},
        {"st": "err", "label": "Missing"},
        {"st": "off", "label": "Stays in Live's library"},
    ]
    kinds = [it["kind"] for it in leitura["items"]]
    assert kinds == ["Picture", "Mono", "Stereo", "Audio", "Audio"]
    pastas = [it["folder"] for it in leitura["items"]]
    assert pastas == [
        "Video",
        "Samples/FolderA",
        "Samples/FolderB",
        "Foley",
        "Core Library",
    ]
    assert len(leitura["renamed"]) == 1
    assert leitura["renamed"][0]["original"] == "amb.wav"
    assert leitura["renamed"][0]["renamed"].startswith("amb__")
    assert leitura["notes"]["main"][0]["code"] == "COLLECT_MEDIA_MISSING"

    destino = tmp_path / "coletado"
    execucao = app_collect.run(
        {"input": str(als), "output": str(destino), "options": {"include_video": True}}
    )

    assert execucao["ok"] is True
    assert execucao["written"] is True
    sessao_saida = destino / "sessao.als"
    pasta_midia = destino / "sessao Media"
    assert Path(execucao["output"]) == sessao_saida.resolve()
    assert sessao_saida.is_file()
    assert (pasta_midia / "corte.mp4").is_file()
    assert (pasta_midia / "amb.wav").is_file()
    assert (pasta_midia / leitura["renamed"][0]["renamed"]).is_file()
    assert execucao["verdict"]["state"] == "warn"
    assert execucao["verdict"]["word"] == "Collected, 1 missing"
    assert any("porta.wav was missing" in d for d in execucao["verdict"]["detail"])

    assert app_convert.LAST["collect"]["output"] == sessao_saida.resolve()
    assert app_convert.LAST["collect"]["report"].missing_count == 1

    abertos: list[Path] = []
    mostrados: list[Path] = []
    monkeypatch.setattr(app_routes, "launch", abertos.append)
    monkeypatch.setattr(app_routes, "reveal", mostrados.append)

    assert app_routes._open({"direction": "collect"}) == {
        "ok": True,
        "path": str(sessao_saida.resolve()),
    }
    assert app_routes._reveal({"direction": "collect"}) == {
        "ok": True,
        "path": str(sessao_saida.resolve()),
    }
    assert abertos == [sessao_saida.resolve()]
    assert mostrados == [sessao_saida.resolve()]

    rel_md = tmp_path / "relatorio_collect.md"
    rel_json = tmp_path / "relatorio_collect.json"
    assert app_routes._report({"direction": "collect", "path": str(rel_md)})["ok"] is True
    assert rel_md.read_text(encoding="utf-8").startswith("# Collect media report")
    assert app_routes._report({"direction": "collect", "path": str(rel_json)})["ok"] is True
    dados_json = json.loads(rel_json.read_text(encoding="utf-8"))
    assert dados_json["counts"]["copied"] == 3
    assert dados_json["counts"]["missing"] == 1
    assert dados_json["counts"]["library"] == 1
    _ = arquivos


def test_rotas_http_collect_registradas_uma_vez(tmp_path):
    wav = escrever_wav(tmp_path, "cena.wav")
    als = montar_set(tmp_path, [("DX", [clip(wav, 0.0, 4.0)])])

    h_read = _FakeHandler()
    assert app_routes.handle_post(h_read, "/api/collect/read", {"input": str(als)}) is True
    assert h_read.status == 200
    assert h_read.data["ok"] is True

    h_run = _FakeHandler()
    dest = tmp_path / "out_http"
    assert (
        app_routes.handle_post(h_run, "/api/collect/run", {"input": str(als), "output": str(dest)})
        is True
    )
    assert h_run.status == 200
    assert h_run.data["ok"] is True
    assert (dest / "set.als").is_file()

    assert app_routes.handle_post(_FakeHandler(), "/api/app/collect/read", {}) is False
    assert app_routes.handle_post(_FakeHandler(), "/api/app/collect/run", {}) is False


def test_collect_validacoes_de_entrada(tmp_path):
    assert app_collect.read({"input": ""})["error"]["code"] == "bad_request"
    assert app_collect.read({"input": str(tmp_path / "sumiu.als")})["error"]["code"] == "not_found"
    txt = tmp_path / "nota.txt"
    txt.write_text("oi", encoding="utf-8")
    assert app_collect.read({"input": str(txt)})["error"]["code"] == "unsupported"
    assert app_collect.run({"input": str(txt), "output": ""})["error"]["code"] == "bad_request"


# --- Export com collect_media nas duas direcoes ------------------------------


@pytest.mark.skipif(not TEMPLATE.exists(), reason="o template do Live nao esta no repositorio")
def test_export_aaf_to_live_com_collect_media_copia_e_relinka(tmp_path):
    pasta_origem = tmp_path / "origem"
    pasta_origem.mkdir()
    wav = pasta_origem / "dialogo.wav"
    wav.write_bytes(wav_bytes(48000 * 6))
    aaf = build_linked_aaf(pasta_origem / "cena.aaf", [(0, 96000, 0)], wav)

    saida_als = tmp_path / "entrega" / "cena.als"
    res = app_convert.convert(
        {
            "direction": "to_live",
            "input": str(aaf),
            "output": str(saida_als),
            "options": {"mode": "strict", "collect_media": True},
        }
    )

    assert res["ok"] is True
    assert res["written"] is True
    assert res["verdict"]["state"] == "ok"
    assert res["verdict"]["word"] == "Positions verified"

    copia_wav = tmp_path / "entrega" / "cena Media" / "dialogo.wav"
    assert copia_wav.is_file()
    assert copia_wav.read_bytes() == wav.read_bytes()

    root = load_als(saida_als)
    caminhos_rel = [
        fref.find("RelativePath").get("Value")
        for sref in root.iter("SampleRef")
        if (fref := sref.find("FileRef")) is not None and fref.find("RelativePath") is not None
    ]
    assert "cena Media/dialogo.wav" in caminhos_rel


def test_export_live_to_aaf_com_collect_media_copia_e_relinka(tmp_path):
    pasta_set = tmp_path / "set_dir"
    pasta_set.mkdir()
    wav = escrever_wav(pasta_set, "voz.wav")
    als = montar_set(pasta_set, [("DX", [clip(wav, 0.0, 4.0)])])

    saida_aaf = tmp_path / "aaf_out" / "retorno.aaf"
    saida_aaf.parent.mkdir()
    res = app_set.convert_to_aaf(
        {
            "direction": "to_aaf",
            "input": str(als),
            "output": str(saida_aaf),
            "tracks": [0],
            "options": {
                "mode": "strict",
                "format": "aaf",
                "media": "link",
                "collect_media": True,
            },
        }
    )

    assert res["ok"] is True
    assert res["written"] is True
    assert res["verdict"]["state"] == "ok"
    assert res["verdict"]["word"] == "Positions verified"

    copia_wav = tmp_path / "aaf_out" / "retorno Media" / "voz.wav"
    assert copia_wav.is_file()
    assert copia_wav.read_bytes() == wav.read_bytes()
    urls = read_locator_urls(saida_aaf)
    assert urls and all(u == copia_wav.resolve().as_uri() for lista in urls for u in lista)


@pytest.mark.parametrize(
    "nome_saida,formato,media",
    [
        ("saida.edl", "edl", "link"),
        ("saida_embed.aaf", "aaf", "embed"),
    ],
)
def test_export_live_to_aaf_ignora_collect_em_edl_ou_embed(tmp_path, nome_saida, formato, media):
    pasta_set = tmp_path / "set_dir"
    pasta_set.mkdir()
    wav = escrever_wav(pasta_set, "voz.wav")
    als = montar_set(pasta_set, [("DX", [clip(wav, 0.0, 4.0)])])

    saida = tmp_path / "out" / nome_saida
    saida.parent.mkdir()
    res = app_set.convert_to_aaf(
        {
            "direction": "to_aaf",
            "input": str(als),
            "output": str(saida),
            "tracks": [0],
            "options": {
                "mode": "strict",
                "format": formato,
                "media": media,
                "collect_media": True,
            },
        }
    )

    assert res["ok"] is True
    assert res["written"] is True
    assert not (saida.parent / f"{saida.stem} Media").exists()


@pytest.mark.skipif(not TEMPLATE.exists(), reason="o template do Live nao esta no repositorio")
def test_export_com_collect_error_preserva_saida_e_anexa_erro(tmp_path, monkeypatch):
    wav = tmp_path / "cena.wav"
    wav.write_bytes(wav_bytes(48000 * 4))
    aaf = build_calibration_aaf(
        tmp_path / "cena.aaf",
        [{"name": "DX", "clips": [{"path": str(wav), "start": 0, "offset": 0, "duration": 48000}]}],
        frames=48000,
    )
    saida = tmp_path / "saida.als"

    def falha_collect(destino):
        raise CollectError("disco cheio na coleta")

    monkeypatch.setattr(app_collect, "collect_into_output", falha_collect)

    res = app_convert.convert(
        {
            "direction": "to_live",
            "input": str(aaf),
            "output": str(saida),
            "options": {"mode": "strict", "collect_media": True},
        }
    )

    assert res["ok"] is True
    assert res["written"] is True
    assert saida.is_file()
    assert res["collect_error"] == "disco cheio na coleta"
    codigos = [n["code"] for n in res["notes"]["main"]]
    assert "COLLECT_FAILED" in codigos


def test_collect_que_falha_nao_deixa_relatorio_vazio_para_salvar(tmp_path):
    pasta = tmp_path / "set_dir"
    pasta.mkdir()
    wav = escrever_wav(pasta, "voz.wav")
    als = montar_set(pasta, [("DX", [clip(wav, 0.0, 4.0)])])
    ocupado = tmp_path / "ocupado"
    ocupado.mkdir()
    (ocupado / "algo.txt").write_text("x", encoding="utf-8")

    res = app_collect.run({"input": str(als), "output": str(ocupado)})

    assert res["ok"] is False
    assert "collect" not in app_convert.LAST
    salvo = app_routes._report({"direction": "collect", "path": str(tmp_path / "r.md")})
    assert salvo["ok"] is False
    assert salvo["error"]["code"] == "nothing"
