"""Aba de som direto do app: rotas de leitura e conversao, pagina e desenho."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from importlib import resources
from pathlib import Path

import pytest

from aafton.studio import app_convert, app_pa, page
from aafton.studio.page import load_page
from aafton.timecode import TimecodeRate, samples_to_label
from aafton.web_app import run_studio_server
from tests.unit.test_som_direto_leitor import HORA, RATE, duas_diarias

SEP = "·"


@pytest.fixture(scope="module")
def studio_server():
    """O servidor do studio numa porta livre, sem abrir o navegador."""
    server = run_studio_server(port=0, open_browser=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join()
    server.server_close()


@pytest.fixture(autouse=True)
def _sem_resultado_de_outra_conversao():
    app_convert.LAST.clear()
    yield
    app_convert.LAST.clear()


@pytest.fixture
def pasta(tmp_path):
    return duas_diarias(tmp_path / "som")


def _post(url, payload):
    """Um POST com corpo JSON e o status e o JSON da resposta."""
    pedido = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(pedido) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _ler(servidor, pasta):
    return _post(f"{servidor}/api/app/read", {"direction": "production", "input": str(pasta)})


def _converter(servidor, pasta, saida, **opcoes):
    corpo = {"direction": "production", "input": str(pasta), "output": str(saida)}
    corpo["options"] = {"bpm": "60", "template": "", **opcoes}
    return _post(f"{servidor}/api/app/convert", corpo)


def _static(nome: str) -> str:
    return (resources.files("aafton.studio") / "static" / nome).read_text(encoding="utf-8")


def _painel(html: str) -> str:
    inicio = html.index('<section class="panel" id="panel-pa"')
    return html[inicio : html.index("</section>", inicio)]


def test_a_leitura_da_os_fatos_da_pasta(studio_server, pasta):
    status, corpo = _ler(studio_server, pasta)
    assert status == 200
    assert corpo["ok"] is True
    assert corpo["direction"] == "production"
    fatos = {fato["k"]: (fato["v"], fato["sub"]) for fato in corpo["facts"]}
    assert [fato["k"] for fato in corpo["facts"]] == [
        "Recorder",
        "Days",
        "Takes",
        "Inputs",
        "Audio",
        "Timecode",
    ]
    assert fatos["Recorder"] == ("SoundDevices", "BWF + iXML")
    assert fatos["Days"] == ("2", f"2025-05-05 {SEP} 2025-06-05")
    assert fatos["Takes"] == ("4", "2 circled")
    assert fatos["Inputs"] == ("2", "In 3 to In 4")
    assert fatos["Audio"] == ("48 kHz", "16-bit PCM")
    assert fatos["Timecode"] == ("24 fps", "non-drop")
    sugerido = Path(corpo["suggest"]["output"])
    assert sugerido == pasta.resolve().parent / "som Project" / "som.als"


def test_a_leitura_da_as_tracks_os_blocos_e_as_notas(studio_server, pasta):
    _, corpo = _ler(studio_server, pasta)
    inicio2 = -(-(10 * RATE + 5 * RATE + 4800 + 10 * RATE) // RATE) * RATE
    assert corpo["blocks"] == [
        {"start_samples": 0, "label": f"05-05 {SEP} 10:00:00"},
        {"start_samples": inicio2, "label": f"06-05 {SEP} 09:00:00"},
    ]
    assert corpo["timeline"] == {"start_samples": 0, "end_samples": inicio2 + 13 * RATE + 4800}
    fixa, varia = corpo["tracks"]
    assert (fixa["name"], fixa["takes"], fixa["clips"]) == (f"In 3 {SEP} NEUMANN", 4, 4)
    assert fixa["state"] == {"st": "ok", "label": "Ready"}
    assert (varia["name"], varia["takes"], varia["clips"]) == ("In 4", 3, 3)
    assert varia["state"] == {"st": "ok", "label": "Mic varies by take"}
    assert fixa["circled_spans"] == [
        [10 * RATE, 10 * RATE + 4800],
        [inicio2 + 13 * RATE, inicio2 + 13 * RATE + 4800],
    ]
    assert len(fixa["spans"]) == 2
    taxa = TimecodeRate(24, 1)
    assert varia["clip_rows"][0] == {
        "name": "12A/005",
        "mic": f"{SEP} CCM8L",
        "day": "05-05-25",
        "tc_in": "10:00:00:00",
        "tc_out": samples_to_label(10 * HORA + 4800, taxa, RATE),
        "circled": "circled",
    }
    assert varia["clip_rows"][1]["circled"] == ""
    assert fixa["clip_rows"][0]["mic"] == ""
    codigos = [nota["code"] for nota in corpo["notes"]["info"]]
    assert "PRODUCTION_TAKES" in codigos


def test_a_leitura_recusa_o_que_nao_e_som_direto(studio_server, tmp_path):
    aaf = tmp_path / "cena.aaf"
    aaf.write_bytes(b"x")
    _, corpo = _ler(studio_server, aaf)
    assert corpo["error"]["code"] == "unsupported"
    _, corpo = _ler(studio_server, tmp_path / "nao_existe")
    assert corpo["error"]["code"] == "not_found"


def test_a_conversao_escreve_o_set_e_da_o_veredito_verificado(studio_server, pasta, tmp_path):
    saida = tmp_path / "out" / "som.als"
    status, corpo = _converter(studio_server, pasta, saida)
    assert status == 200
    assert corpo["ok"] is True
    assert corpo["written"] is True
    assert saida.exists()
    assert corpo["verdict"] == {
        "state": "ok",
        "word": "Verified",
        "detail": [
            "7 of 7 audio clips compared",
            "largest deviation 0 samples",
            "every channel equal to the recorder file",
        ],
    }
    relatorio = tmp_path / "relatorio.md"
    _, salvo = _post(
        f"{studio_server}/api/app/report", {"direction": "production", "path": str(relatorio)}
    )
    assert salvo["ok"] is True
    assert relatorio.exists()
    _, de_novo = _converter(studio_server, pasta, saida)
    assert de_novo["error"]["code"] == "exists"


def test_o_filtro_de_circled_chega_ao_motor(studio_server, pasta, tmp_path):
    saida = tmp_path / "out" / "circled.als"
    _, corpo = _converter(studio_server, pasta, saida, circled_only=True)
    assert corpo["verdict"]["word"] == "Verified"
    assert corpo["verdict"]["detail"][0] == "3 of 3 audio clips compared"
    codigos = [nota["code"] for nota in corpo["notes"]["info"]]
    assert "PRODUCTION_CIRCLED_ONLY" in codigos


def test_o_set_dentro_da_pasta_do_gravador_e_recusado(studio_server, pasta):
    _, corpo = _converter(studio_server, pasta, pasta / "dentro.als")
    assert corpo["error"]["code"] == "bad_request"
    assert corpo["error"]["message"] == "The Live set cannot go inside the recorder folder."
    assert not (pasta / "dentro.als").exists()


def test_a_regua_arredonda_o_timecode_ao_segundo_mais_proximo():
    taxa = TimecodeRate(24, 1)
    assert app_pa.segundo_mais_proximo("10:20:23:12", taxa) == "10:20:24"
    assert app_pa.segundo_mais_proximo("10:20:23:11", taxa) == "10:20:23"
    assert app_pa.segundo_mais_proximo("23:59:59:20", taxa) == "00:00:00"
    assert app_pa.segundo_mais_proximo("01:00:00;15", TimecodeRate(30000, 1001, True)) == (
        "01:00:01"
    )


def test_a_pagina_tem_a_terceira_aba_e_o_painel():
    html = load_page()
    assert 'id="tab-pa" aria-controls="panel-pa"' in html
    assert _static("index.html").count('role="tab"') == 5
    painel = _painel(html)
    assert 'data-direction="production"' in painel
    for texto in (
        "The recorder files: a folder with one subfolder per shooting day, or a single WAV.",
        "Choose folder&hellip;",
        'data-f="circled_only"',
        "Circled takes only",
        'data-f="template"',
        'data-f="bpm"',
        'data-act="open-output"',
        'data-act="reveal-output"',
        'data-act="save-report"',
        'data-f="exists"',
    ):
        assert texto in painel, texto
    for recusada in ("live_origin", "ltc", "video", "session_text", "a-mode", "a-stereo"):
        assert f'"{recusada}"' not in painel and f'name="{recusada}"' not in painel, recusada
    for modelo in ("tpl-track-production", "tpl-detail-production", "tpl-clip-production"):
        assert f'id="{modelo}"' in html, modelo


def test_o_script_da_aba_entra_antes_do_fluxo():
    assert page.SCRIPTS.index("app-pa.js") < page.SCRIPTS.index("app-flow.js")
    texto = _static("app-pa.js")
    assert len(texto.splitlines()) < 400
    assert not [linha for linha in texto.splitlines() if linha.startswith("const ")]
    assert texto.isascii()


def test_o_desenho_da_aba_usa_os_tokens():
    """O circulado e verde de estado; nada de raio, sombra ou gradiente novo."""
    css = _static("studio.css")
    assert ".circ{color:var(--ok)}" in css
    assert "--lane-circled:var(--ok)" in css
    inicio = css.index("/* ---------- production audio sheet")
    bloco = css[inicio : css.index(".sheet-foot{", inicio)]
    assert "#" not in bloco.split("*/", 1)[1]
    assert "radius" not in bloco and "shadow" not in bloco and "gradient" not in bloco
    assert "amber" not in bloco


def test_a_direcao_de_som_direto_tem_o_seu_ultimo_resultado(pasta, tmp_path):
    saida = tmp_path / "out" / "direto.als"
    corpo = app_pa.convert(
        {"direction": "production", "input": str(pasta), "output": str(saida), "options": {}}
    )
    assert corpo["written"] is True
    assert app_convert.LAST["production"]["output"] == saida.resolve()
    assert "to_live" not in app_convert.LAST


def test_nome_do_gravador_por_extenso_e_sem_serial():
    from aafton.studio.app_pa import nome_do_gravador

    assert nome_do_gravador("SoundDev: MixPre-6 II TH13221523") == "Sound Devices MixPre-6 II"
    assert nome_do_gravador("Zoom F8n") == "Zoom F8n"
    assert nome_do_gravador("  ") == "Unknown"
