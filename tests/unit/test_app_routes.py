"""Tests of the HTTP routes of the app page."""

from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from aafton import __version__
from aafton.pipeline import ImportOptions, import_session
from aafton.report import Report
from aafton.studio import app_convert, app_read, app_routes, dialogs
from aafton.web_app import run_studio_server
from tests.aaf_factory import build_calibration_aaf, build_linked_aaf, wav_bytes

TEMPLATE = Path("templates/live12.als")

# The space the sheet puts between the thousands of the sample rate.
NBSP = chr(0x00A0)


@pytest.fixture(scope="module")
def studio_server():
    """The studio server on a free port, without opening a browser."""
    server = run_studio_server(port=0, open_browser=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join()
    server.server_close()


@pytest.fixture(autouse=True)
def _sem_resultado_de_outra_conversao():
    """Each test starts without the file written by another one."""
    app_convert.LAST.clear()
    yield
    app_convert.LAST.clear()


@pytest.fixture
def wav(tmp_path):
    caminho = tmp_path / "cena.wav"
    caminho.write_bytes(wav_bytes(48000 * 10))
    return caminho


@pytest.fixture
def aaf(tmp_path, wav):
    return build_linked_aaf(tmp_path / "cena.aaf", [(0, 96000, 0), (192000, 96000, 0)], wav)


@pytest.fixture
def set_live(tmp_path, wav):
    """The Live set that the Live to AAF direction reads and writes back."""
    if not TEMPLATE.exists():
        pytest.skip("o template do Live nao esta no repositorio")
    aaf = build_calibration_aaf(
        tmp_path / "calibracao.aaf",
        [
            {
                "name": "DIALOGO 1",
                "clips": [{"path": str(wav), "start": 0, "offset": 0, "duration": 96000}],
            }
        ],
        frames=96000,
    )
    destino = tmp_path / "cena.als"
    import_session(
        ImportOptions(input=str(aaf), output=str(destino), template=str(TEMPLATE)),
        Report(strict=False),
    )
    return destino


def _get(url, **headers):
    """A GET and its status, headers and body."""
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request) as resp:
        return resp.status, resp.headers, resp.read()


def _get_json(url, **headers):
    """A GET and the JSON it answers."""
    status, _, body = _get(url, **headers)
    return status, json.loads(body.decode("utf-8"))


def _post(url, payload, **headers):
    """A POST with a JSON body and the JSON it answers."""
    cabecalhos = {"Content-Type": "application/json", **headers}
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=cabecalhos
    )
    try:
        with urllib.request.urlopen(request) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _converte(studio_server, aaf, saida):
    """The convert request of the AAF to Live direction."""
    return _post(
        f"{studio_server}/api/app/convert",
        {
            "direction": "to_live",
            "input": str(aaf),
            "output": str(saida),
            "options": {"mode": "strict"},
        },
    )


def test_a_informacao_do_app_diz_a_versao_e_o_sistema(studio_server):
    status, body = _get_json(f"{studio_server}/api/app/info")

    assert status == 200
    assert body["ok"] is True
    assert body["version"] == __version__
    assert body["platform"] == sys.platform
    assert body["dialogs"] is dialogs.has_backend()


def test_a_fonte_embutida_e_servida_com_o_cabecalho_de_cache(studio_server, tmp_path, monkeypatch):
    pasta = tmp_path / "fonts"
    pasta.mkdir()
    (pasta / "IBMPlex-Regular.woff2").write_bytes(b"wOF2fonte")
    monkeypatch.setattr(app_routes, "FONTS_DIR", pasta)

    status, headers, corpo = _get(f"{studio_server}/static/fonts/IBMPlex-Regular.woff2")

    assert status == 200
    assert headers["Content-Type"] == "font/woff2"
    assert headers["Content-Length"] == str(len(b"wOF2fonte"))
    assert headers["Cache-Control"] == "public, max-age=31536000, immutable"
    assert corpo == b"wOF2fonte"


@pytest.mark.parametrize(
    "nome",
    [
        "../segredo.txt",
        "%2e%2e%2fsegredo.txt",
        "IBMPlex-Regular.woff2.txt",
        "IBMPlex%20Regular.woff2",
        "IBMPlex-Regular.woff",
    ],
)
def test_um_nome_de_fonte_fora_do_padrao_da_404(studio_server, nome):
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(f"{studio_server}/static/fonts/{nome}")
    assert exc.value.code == 404


def test_uma_fonte_que_o_pacote_nao_tem_da_404(studio_server, tmp_path, monkeypatch):
    monkeypatch.setattr(app_routes, "FONTS_DIR", tmp_path / "vazio")

    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(f"{studio_server}/static/fonts/IBMPlex-Regular.woff2")
    assert exc.value.code == 404


def test_o_browse_devolve_o_caminho_escolhido_no_dialogo(studio_server, monkeypatch):
    escolhido = "C:\\cenas\\EP02.aaf"
    chamadas = []

    def falso(**kwargs):
        chamadas.append(kwargs)
        return {"path": escolhido}

    monkeypatch.setattr(dialogs, "pick", falso)

    status, body = _post(
        f"{studio_server}/api/app/browse",
        {"kind": "open_file", "filter": "source", "title": "Source"},
    )

    assert status == 200
    assert body == {"ok": True, "path": escolhido}
    assert chamadas[0]["kind"] == "open_file"
    assert chamadas[0]["filter_type"] == "source"


def test_o_browse_devolve_o_caminho_vazio_quando_o_dialogo_e_cancelado(studio_server, monkeypatch):
    monkeypatch.setattr(dialogs, "pick", lambda **kwargs: {"path": None})

    status, body = _post(f"{studio_server}/api/app/browse", {"kind": "open_file"})

    assert status == 200
    assert body == {"ok": True, "path": None}


def test_o_browse_responde_o_erro_quando_o_sistema_nao_tem_dialogo(studio_server, monkeypatch):
    def falso(**kwargs):
        return {"error": "no file dialog on this system"}

    monkeypatch.setattr(dialogs, "pick", falso)

    status, body = _post(f"{studio_server}/api/app/browse", {"kind": "open_file"})

    assert status == 200
    assert body == {
        "ok": False,
        "error": {"code": "no_dialog", "message": "no file dialog on this system"},
    }


def test_o_browse_responde_o_codigo_do_erro_do_dialogo(studio_server, monkeypatch):
    """A rota do browse leva o codigo do erro do dialogo para a pagina."""

    def falso(**kwargs):
        """O dialogo que falha com o arquivo de outra extensao."""
        return {"error": "expected a .aaf file", "code": "wrong_extension"}

    monkeypatch.setattr(dialogs, "pick", falso)

    status, body = _post(f"{studio_server}/api/app/browse", {"kind": "open_file"})

    assert status == 200
    assert body == {
        "ok": False,
        "error": {"code": "wrong_extension", "message": "expected a .aaf file"},
    }


def test_a_informacao_do_app_diz_se_o_sistema_tem_dialogo(studio_server, monkeypatch):
    """A informacao do app diz quando o sistema tem dialogo e quando nao tem."""
    dialogs.set_window_backend(None)
    monkeypatch.setattr(sys, "platform", "darwin")

    status, body = _get_json(f"{studio_server}/api/app/info")

    assert status == 200
    assert body["dialogs"] is True

    monkeypatch.setattr(sys, "platform", "linux")

    status, body = _get_json(f"{studio_server}/api/app/info")

    assert status == 200
    assert body["dialogs"] is False


def test_a_leitura_descreve_a_sessao_sem_escrever_nada(studio_server, aaf, tmp_path):
    antes = sorted(item.name for item in tmp_path.iterdir())

    status, body = _post(
        f"{studio_server}/api/app/read",
        {"direction": "to_live", "input": str(aaf)},
    )

    assert status == 200
    assert body["ok"] is True
    assert body["direction"] == "to_live"
    assert body["source"]["format"] == "aaf"
    assert [fato["k"] for fato in body["facts"]] == [
        "Program",
        "Sample rate",
        "Timecode",
        "Duration",
        "Tracks",
        "Media",
    ]
    fatos = {fato["k"]: fato for fato in body["facts"]}
    assert fatos["Sample rate"]["v"] == f"48{NBSP}000 Hz"
    assert fatos["Timecode"]["v"] == "25 fps"
    assert fatos["Timecode"]["sub"] == "starts 01:00:00:00"
    assert fatos["Duration"]["v"] == "00:00:06:00"
    assert fatos["Tracks"]["v"] == "1"
    assert fatos["Tracks"]["sub"] == "2 clips"
    assert fatos["Media"]["v"] == "1 of 1"
    assert body["media"] == {
        "total": 1,
        "embedded": 0,
        "found": 1,
        "missing": 0,
        "missing_text": "",
    }
    assert sorted(item.name for item in tmp_path.iterdir()) == antes


def test_um_pedido_malformado_responde_com_400(studio_server, tmp_path):
    status, body = _post(
        f"{studio_server}/api/app/convert",
        {"direction": "to_live", "input": str(tmp_path / "cena.aaf")},
    )

    assert status == 400
    assert body == {
        "ok": False,
        "error": {
            "code": "bad_request",
            "message": "The source and the destination are required.",
        },
    }


def test_uma_direcao_desconhecida_responde_com_400(studio_server):
    status, body = _post(f"{studio_server}/api/app/convert", {"direction": "to_outro"})

    assert status == 400
    assert body["ok"] is False
    assert body["error"]["code"] == "bad_request"
    assert body["error"]["message"] == "The direction must be to_live or to_aaf."


def test_um_arquivo_que_nao_existe_responde_com_200(studio_server, tmp_path):
    status, body = _post(
        f"{studio_server}/api/app/read",
        {"direction": "to_live", "input": str(tmp_path / "nao_existe.aaf")},
    )

    assert status == 200
    assert body["ok"] is False
    assert body["error"]["code"] == "not_found"
    assert body["error"]["message"] == f"The file {tmp_path / 'nao_existe.aaf'} was not found."


@pytest.mark.skipif(not TEMPLATE.exists(), reason="o template do Live nao esta no repositorio")
def test_a_conversao_escreve_o_set_e_responde_com_o_veredito(studio_server, aaf, tmp_path):
    saida = tmp_path / "saida.als"

    status, body = _converte(studio_server, aaf, saida)

    assert status == 200
    assert body["ok"] is True
    assert body["written"] is True
    assert body["output"] == str(saida.resolve())
    assert body["verdict"]["state"] == "ok"
    assert body["verdict"]["word"] == "Positions verified"
    assert "2 of 2 audio clips compared" in body["verdict"]["detail"]
    assert "largest deviation 0 samples" in body["verdict"]["detail"]
    assert body["can_force"] is False
    assert saida.exists()


def test_o_open_e_o_reveal_respondem_nada_antes_de_converter(studio_server, tmp_path):
    status, body = _post(f"{studio_server}/api/app/open", {"direction": "to_live"})
    assert status == 200
    assert body["ok"] is False
    assert body["error"]["code"] == "nothing"
    assert body["error"]["message"] == "There is no file to open yet."

    status, body = _post(f"{studio_server}/api/app/reveal", {"direction": "to_live"})
    assert body["error"]["code"] == "nothing"
    assert body["error"]["message"] == "There is no file to show yet."

    destino = tmp_path / "relatorio.md"
    status, body = _post(
        f"{studio_server}/api/app/report",
        {"direction": "to_live", "path": str(destino)},
    )
    assert body["error"]["code"] == "nothing"
    assert body["error"]["message"] == "There is no report to write yet."
    assert not destino.exists()


@pytest.mark.skipif(not TEMPLATE.exists(), reason="o template do Live nao esta no repositorio")
def test_o_open_e_o_reveal_respondem_nada_quando_a_conversao_parou(studio_server, tmp_path, wav):
    aaf = build_linked_aaf(tmp_path / "cena.aaf", [(0, 96000, 0)], wav)
    wav.unlink()

    status, body = _post(
        f"{studio_server}/api/app/convert",
        {
            "direction": "to_live",
            "input": str(aaf),
            "output": str(tmp_path / "saida.als"),
            "options": {"mode": "strict"},
        },
    )
    assert body["written"] is False

    status, body = _post(f"{studio_server}/api/app/open", {"direction": "to_live"})
    assert body["ok"] is False
    assert body["error"]["code"] == "nothing"

    status, body = _post(f"{studio_server}/api/app/reveal", {"direction": "to_live"})
    assert body["ok"] is False
    assert body["error"]["code"] == "nothing"

    destino = tmp_path / "relatorio.md"
    status, body = _post(
        f"{studio_server}/api/app/report",
        {"direction": "to_live", "path": str(destino)},
    )
    assert body["ok"] is True
    assert destino.exists()


@pytest.mark.skipif(not TEMPLATE.exists(), reason="o template do Live nao esta no repositorio")
def test_o_open_e_o_reveal_abrem_o_arquivo_escrito(studio_server, aaf, tmp_path, monkeypatch):
    saida = tmp_path / "saida.als"
    _converte(studio_server, aaf, saida)

    abertos = []
    mostrados = []
    monkeypatch.setattr(app_routes, "launch", lambda caminho: abertos.append(caminho))
    monkeypatch.setattr(app_routes, "reveal", lambda caminho: mostrados.append(caminho))

    status, body = _post(f"{studio_server}/api/app/open", {"direction": "to_live"})
    assert status == 200
    assert body["ok"] is True

    status, body = _post(f"{studio_server}/api/app/reveal", {"direction": "to_live"})
    assert status == 200
    assert body["ok"] is True

    assert abertos == [saida.resolve()]
    assert mostrados == [saida.resolve()]


@pytest.mark.skipif(not TEMPLATE.exists(), reason="o template do Live nao esta no repositorio")
def test_o_open_e_o_reveal_ignoram_o_caminho_do_pedido(studio_server, aaf, tmp_path, monkeypatch):
    """O caminho do pedido nunca e aberto: so o arquivo da conversao e."""
    saida = tmp_path / "saida.als"
    _converte(studio_server, aaf, saida)
    outro = tmp_path / "outro.als"
    outro.write_text("o set de antes", encoding="utf-8")

    abertos = []
    mostrados = []
    monkeypatch.setattr(app_routes, "launch", lambda caminho: abertos.append(caminho))
    monkeypatch.setattr(app_routes, "reveal", lambda caminho: mostrados.append(caminho))

    status, body = _post(
        f"{studio_server}/api/app/open", {"direction": "to_live", "path": str(outro)}
    )
    assert status == 200
    assert body["ok"] is True

    status, body = _post(
        f"{studio_server}/api/app/reveal", {"direction": "to_live", "path": str(outro)}
    )
    assert status == 200
    assert body["ok"] is True

    assert abertos == [saida.resolve()]
    assert mostrados == [saida.resolve()]


@pytest.mark.skipif(not TEMPLATE.exists(), reason="o template do Live nao esta no repositorio")
def test_o_relatorio_da_conversao_e_escrito_no_arquivo_escolhido(studio_server, aaf, tmp_path):
    saida = tmp_path / "saida.als"
    _converte(studio_server, aaf, saida)

    destino = tmp_path / "relatorio.md"
    status, body = _post(
        f"{studio_server}/api/app/report",
        {"direction": "to_live", "path": str(destino)},
    )
    assert body["ok"] is True
    assert destino.read_text(encoding="utf-8").startswith("# Conversion report")

    destino_json = tmp_path / "relatorio.json"
    status, body = _post(
        f"{studio_server}/api/app/report",
        {"direction": "to_live", "path": str(destino_json)},
    )
    assert body["ok"] is True
    assert isinstance(json.loads(destino_json.read_text(encoding="utf-8"))["issues"], list)

    status, body = _post(
        f"{studio_server}/api/app/report",
        {"direction": "to_live", "path": str(tmp_path / "relatorio.pdf")},
    )
    assert status == 400
    assert body["ok"] is False
    assert body["error"]["code"] == "bad_request"
    assert body["error"]["message"] == "The format .pdf is not a report."


def test_a_origem_de_fora_do_servidor_da_403(studio_server):
    request = urllib.request.Request(
        f"{studio_server}/api/app/info",
        headers={"Origin": "https://exemplo.invalido"},
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(request)
    assert exc.value.code == 403


def test_uma_excecao_na_rota_vira_um_erro_do_app(studio_server, monkeypatch):
    def falha(payload):
        raise RuntimeError("o motor falhou")

    monkeypatch.setattr(app_read, "read", falha)

    status, body = _post(
        f"{studio_server}/api/app/read",
        {"direction": "to_live", "input": "qualquer.aaf"},
    )

    assert status == 200
    assert body == {"ok": False, "error": {"code": "failed", "message": "o motor falhou"}}


def test_o_open_responde_nada_quando_o_arquivo_sumiu(studio_server, tmp_path, monkeypatch):
    arquivo = tmp_path / "saida.als"
    arquivo.write_text("o set", encoding="utf-8")
    app_convert.LAST["to_live"] = {"output": arquivo, "report": Report()}

    def falha(caminho):
        raise OSError("o arquivo nao existe mais")

    monkeypatch.setattr(app_routes, "launch", falha)

    status, body = _post(f"{studio_server}/api/app/open", {"direction": "to_live"})

    assert status == 200
    assert body["ok"] is False
    assert body["error"]["code"] == "nothing"


def test_o_reveal_responde_nada_quando_o_arquivo_sumiu(studio_server, tmp_path, monkeypatch):
    arquivo = tmp_path / "saida.als"
    arquivo.write_text("o set", encoding="utf-8")
    app_convert.LAST["to_live"] = {"output": arquivo, "report": Report()}

    def falha(caminho):
        raise OSError("o arquivo nao existe mais")

    monkeypatch.setattr(app_routes, "reveal", falha)

    status, body = _post(f"{studio_server}/api/app/reveal", {"direction": "to_live"})

    assert status == 200
    assert body["ok"] is False
    assert body["error"]["code"] == "nothing"


def test_o_caminho_do_relatorio_e_absoluto(studio_server, aaf, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _converte(studio_server, aaf, Path("saida.als"))

    status, body = _post(
        f"{studio_server}/api/app/report",
        {"direction": "to_live", "path": "relatorio.md"},
    )

    assert status == 200
    assert body["ok"] is True
    assert body["path"] == str((tmp_path / "relatorio.md").resolve())
    assert Path(body["path"]).is_absolute()


def test_o_relatorio_da_conversao_para_aaf(studio_server, set_live, tmp_path):
    status, body = _post(
        f"{studio_server}/api/app/convert",
        {
            "direction": "to_aaf",
            "input": str(set_live),
            "output": str(tmp_path / "saida.aaf"),
            "tracks": [0],
            "options": {"mode": "strict"},
        },
    )
    assert body["ok"] is True
    assert body["written"] is True

    destino = tmp_path / "relatorio.md"
    status, body = _post(
        f"{studio_server}/api/app/report",
        {"direction": "to_aaf", "path": str(destino)},
    )

    assert status == 200
    assert body["ok"] is True
    assert destino.exists()
    assert destino.read_text(encoding="utf-8").startswith("# Conversion report")
    assert body["path"] == str(destino.resolve())
