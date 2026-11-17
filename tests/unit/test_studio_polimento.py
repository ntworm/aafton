"""Testes do polimento do Studio: o Save as com o nome do original e os erros de arquivo.

O dialogo de salvar abre na pasta e com o nome do destino; um arquivo movido,
apagado ou sem permissao vira uma frase simples, sem o texto do sistema.
"""

from __future__ import annotations

import json
import sys
from importlib import resources
from pathlib import Path

import pytest

from aafton import desktop
from aafton.pipeline import ImportOptions, import_session
from aafton.report import Report
from aafton.studio import (
    app_convert,
    app_messages,
    app_read,
    app_routes,
    app_set,
    dialogs,
    dialogs_macos,
    dialogs_windows,
)
from tests.aaf_factory import build_calibration_aaf, build_linked_aaf, wav_bytes

TEMPLATE = Path("templates/live12.als")

# Os textos do sistema que nenhuma resposta da pagina pode mostrar.
TEXTOS_DO_SISTEMA = ("WinError", "Errno")


@pytest.fixture(autouse=True)
def _limpo():
    """Cada teste comeca sem janela registrada e sem conversao anterior."""
    dialogs.set_window_backend(None)
    app_convert.LAST.clear()
    yield
    dialogs.set_window_backend(None)
    app_convert.LAST.clear()


@pytest.fixture
def aaf(tmp_path):
    """Um AAF com a midia ao lado, numa pasta que o teste pode mover."""
    pasta = tmp_path / "AF_CENA"
    pasta.mkdir()
    wav = pasta / "cena.wav"
    wav.write_bytes(wav_bytes(48000 * 10))
    return build_linked_aaf(pasta / "cena.aaf", [(0, 96000, 0)], wav)


def _sem_texto_do_sistema(resposta: dict) -> None:
    """A resposta nao carrega o texto cru do sistema."""
    texto = json.dumps(resposta)
    for marca in TEXTOS_DO_SISTEMA:
        assert marca not in texto


def _static(name: str) -> str:
    """O texto de um arquivo da pasta static do studio."""
    return (resources.files("aafton.studio") / "static" / name).read_text(encoding="utf-8")


def _corpo(script: str, abertura: str) -> str:
    """O corpo de uma funcao do script, da abertura ate o fecho."""
    texto = _static(script)
    inicio = texto.index(abertura)
    return texto[inicio : texto.index("\n};", inicio)]


# --- o Save as ---


def test_o_dialogo_sobe_ate_a_primeira_pasta_que_existe(tmp_path):
    destino = tmp_path / "cena Project" / "cena.als"

    assert dialogs._inicio(str(destino)) == (str(tmp_path), "cena.als")
    assert dialogs._inicio(None) == ("", "")
    assert dialogs._inicio("") == ("", "")


def test_a_janela_recebe_a_pasta_e_o_nome_do_initial(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    chamadas = []

    def janela(kind, label, extensions, title, folder, name):
        chamadas.append((kind, folder, name))
        return None

    dialogs.set_window_backend(janela)
    destino = tmp_path / "cena Project" / "cena.als"

    assert dialogs.pick("save_file", "als", "Save", initial=str(destino)) == {"path": None}
    assert dialogs.pick("save_file", "als", "Save") == {"path": None}
    assert chamadas == [("save_file", str(tmp_path), "cena.als"), ("save_file", "", "")]


def test_o_backend_da_janela_abre_na_pasta_com_o_nome(tmp_path):
    class Janela:
        """A janela falsa: guarda o pedido do dialogo."""

        def __init__(self):
            self.pedidos = []
            self.events = type("Events", (), {"shown": self})()

        def wait(self, timeout=None):
            return True

        def create_file_dialog(self, tipo, directory, multiplo, save_filename, file_types):
            self.pedidos.append((tipo, directory, save_filename))
            return None

    webview = type("Webview", (), {})()
    webview.FileDialog = type("FileDialog", (), {"OPEN": "o", "SAVE": "s", "FOLDER": "f"})
    janela = Janela()
    backend = desktop._backend(janela, webview)

    backend("save_file", "Ableton Live Sets", ["als"], "Save", str(tmp_path), "cena.als")
    backend("save_file", "Ableton Live Sets", ["als"], "Save")
    backend("open_file", "AAF files", ["aaf"], "Pick", str(tmp_path), "cena.aaf")

    assert janela.pedidos == [
        ("s", str(tmp_path), "cena.als"),
        ("s", "", ""),
        ("o", str(tmp_path), ""),
    ]
    assert "untitled" not in Path(desktop.__file__).read_text(encoding="utf-8")


def test_o_script_do_windows_abre_na_pasta_com_o_nome_escapado():
    script = dialogs_windows.build_dialog_script(
        "save_file", "als", "Save", "C:\\Ana's cenas", "Ana's cena.als"
    )

    assert "$f.InitialDirectory = 'C:\\Ana''s cenas'" in script
    assert "$f.FileName = 'Ana''s cena.als'" in script
    sem_inicio = dialogs_windows.build_dialog_script("save_file", "als", "Save")
    assert "InitialDirectory" not in sem_inicio
    assert "FileName =" not in sem_inicio


def test_o_script_do_macos_abre_na_pasta_com_o_nome_escapado():
    script = dialogs_macos.build_script("save_file", "Save", ["als"], '/cenas/"A"', 'cena "1".als')

    assert 'default name "cena \\"1\\".als"' in script
    assert 'default location (POSIX file "/cenas/\\"A\\"")' in script
    assert "default name" not in dialogs_macos.build_script("save_file", "Save", ["als"])
    assert "untitled" not in Path(dialogs_macos.__file__).read_text(encoding="utf-8")


def test_o_browse_leva_o_initial_ate_o_pick(monkeypatch):
    chamadas = []

    def falso(**kwargs):
        chamadas.append(kwargs)
        return {"path": None}

    class Handler:
        """O handler falso: guarda a resposta."""

        def _send_json(self, data, status=200):
            self.data = data

    monkeypatch.setattr(dialogs, "pick", falso)
    payload = {"kind": "save_file", "filter": "als", "title": "Save", "initial": "C:\\a\\b.als"}
    app_routes._browse(Handler(), payload)
    app_routes._browse(Handler(), {"kind": "open_file", "initial": ""})

    assert chamadas[0]["initial"] == "C:\\a\\b.als"
    assert chamadas[1]["initial"] is None


def test_a_pagina_manda_o_destino_e_o_relatorio_como_initial():
    browse = _corpo("app-core.js", "App.browse = async (p, kind, filter, title) => {")
    inicial = _corpo("app-core.js", "App.saveInitial = (p, filter) => {")

    assert 'initial: kind === "save_file" ? App.saveInitial(p, filter) : "",' in browse
    assert 'const typed = App.text(p.el, "output");' in inicial
    assert 'let dest = typed || suggest.output || "";' in inicial
    assert 'if (filter === "report") return dest.replace(/\\.[^.\\\\/]*$/, "") + ".md";' in inicial


# --- as mensagens de arquivo ---


def test_a_mensagem_de_cada_falha_de_arquivo(tmp_path):
    alvo = tmp_path / "cena.als"

    sumiu = app_messages.mensagem_de_arquivo(FileNotFoundError(2, "x"), alvo, "open")
    assert sumiu == (
        f"cena.als is no longer at {tmp_path}. It may have been moved, renamed or deleted. "
        "Convert again to write it."
    )
    negado = app_messages.mensagem_de_arquivo(PermissionError(13, "x"), alvo, "read")
    assert negado.startswith(f"aafton is not allowed to read {alvo}.")
    outro = app_messages.mensagem_de_arquivo(OSError(5, "x"), alvo, "open")
    assert outro == f"Opening failed for {alvo}."


@pytest.mark.parametrize("acao", ["open", "show"])
def test_open_e_show_com_o_arquivo_apagado_nao_chamam_o_sistema(tmp_path, monkeypatch, acao):
    chamados = []
    monkeypatch.setattr(app_routes, "launch", chamados.append)
    monkeypatch.setattr(app_routes, "reveal", chamados.append)
    arquivo = tmp_path / "AF_CENA" / "cena.als"
    app_convert.LAST["to_live"] = {"output": arquivo, "report": Report()}

    rota = app_routes._open if acao == "open" else app_routes._reveal
    resposta = rota({"direction": "to_live"})

    assert chamados == []
    assert resposta["ok"] is False
    assert resposta["error"]["code"] == "nothing"
    assert resposta["error"]["message"] == (
        f"cena.als is no longer at {arquivo.parent}. It may have been moved, renamed or "
        "deleted. Convert again to write it."
    )


def test_a_leitura_de_uma_fonte_apagada(aaf):
    aaf.unlink()

    resposta = app_read.read({"direction": "to_live", "input": str(aaf)})

    assert resposta["error"]["code"] == "not_found"
    assert resposta["error"]["message"].startswith(f"cena.aaf is no longer at {aaf.parent}.")
    assert "Choose the file again." in resposta["error"]["message"]
    _sem_texto_do_sistema(resposta)


def _pedido(fonte, saida) -> dict:
    """O pedido de conversao da aba AAF to Live."""
    return {
        "direction": "to_live",
        "input": str(fonte),
        "output": str(saida),
        "options": {"mode": "strict"},
    }


def test_a_conversao_depois_que_a_pasta_da_fonte_mudou(aaf, tmp_path):
    aaf.parent.rename(tmp_path / "MOVIDA")

    resposta = app_convert.convert(_pedido(aaf, tmp_path / "saida" / "cena.als"))

    assert resposta["error"]["code"] == "not_found"
    mensagem = resposta["error"]["message"]
    assert "was not found" not in mensagem
    assert mensagem.startswith(f"cena.aaf is no longer at {aaf.parent}.")
    _sem_texto_do_sistema(resposta)


def test_a_conversao_cuja_pasta_do_destino_e_um_arquivo(aaf, tmp_path):
    arquivo = tmp_path / "notas.txt"
    arquivo.write_text("x", encoding="utf-8")

    resposta = app_convert.convert(_pedido(aaf, arquivo / "cena.als"))

    assert resposta["error"]["code"] == "bad_request"
    assert resposta["error"]["message"] == (
        f"cena.als cannot be written in {arquivo.resolve()}: the folder cannot be created "
        "there. Choose another destination."
    )
    _sem_texto_do_sistema(resposta)


def test_a_permissao_negada_vai_ao_relatorio_e_nao_a_tela(aaf, tmp_path, monkeypatch):
    destino = (tmp_path / "saida" / "cena.als").resolve()

    def negado(pedido, report, **extra):
        raise PermissionError(13, "[WinError 5] Access is denied", str(destino))

    monkeypatch.setattr(app_convert, "import_session", negado)

    resposta = app_convert.convert(_pedido(aaf, destino))

    assert resposta["error"]["code"] == "failed"
    assert resposta["error"]["message"].startswith(f"aafton is not allowed to write {destino}.")
    assert "Choose another destination." in resposta["error"]["message"]
    _sem_texto_do_sistema(resposta)
    notas = [i for i in app_convert.LAST["to_live"]["report"].issues if i.code == "FILE_ERROR"]
    assert len(notas) == 1
    assert "Access is denied" in notas[0].message


def test_o_set_movido_entre_a_leitura_e_o_export(tmp_path):
    if not TEMPLATE.exists():
        pytest.skip("o template do Live nao esta no repositorio")
    pasta = tmp_path / "SESSAO"
    pasta.mkdir()
    wav = pasta / "cena.wav"
    wav.write_bytes(wav_bytes(48000 * 4))
    clips = [{"path": str(wav), "start": 0, "offset": 0, "duration": 96000}]
    fonte = build_calibration_aaf(
        pasta / "calibracao.aaf", [{"name": "DIALOGO 1", "clips": clips}], frames=96000
    )
    als = pasta / "cena.als"
    import_session(
        ImportOptions(input=str(fonte), output=str(als), template=str(TEMPLATE)),
        Report(strict=False),
    )
    assert app_set.read_set({"direction": "to_aaf", "input": str(als)})["ok"] is True
    pasta.rename(tmp_path / "MOVIDA")

    resposta = app_set.convert_to_aaf(
        {
            "direction": "to_aaf",
            "input": str(als),
            "output": str(tmp_path / "cena.aaf"),
            "tracks": [0],
            "options": {"mode": "strict"},
        }
    )

    assert resposta["error"]["code"] == "not_found"
    assert resposta["error"]["message"].startswith(f"cena.als is no longer at {pasta}.")
    _sem_texto_do_sistema(resposta)


def test_aceitar_o_nome_oferecido_mantem_o_destino_sugerido(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    destino = tmp_path / "cena Project" / "cena.als"
    outro = tmp_path / "outro.als"
    respostas = iter([str(tmp_path / "cena.als"), str(outro)])
    dialogs.set_window_backend(lambda *args: next(respostas))

    assert dialogs.pick("save_file", "als", "Save", initial=str(destino)) == {"path": str(destino)}
    assert dialogs.pick("save_file", "als", "Save", initial=str(destino)) == {"path": str(outro)}


def test_wav_que_sumiu_na_pasta_do_destino_e_leitura(tmp_path):
    pasta = tmp_path / "entrega"
    pasta.mkdir()
    destino = pasta / "cena.als"
    wav = pasta / "cena.wav"
    report = Report(strict=False)
    exc = FileNotFoundError(2, "No such file or directory", str(wav))

    mensagem = app_messages.falha_da_conversao(exc, destino, report)

    assert mensagem.startswith("cena.wav is no longer at")


def test_permissao_negada_sem_caminho_nao_diz_que_sumiu():
    mensagem = app_messages.mensagem_de_arquivo(PermissionError(13, "denied"), None, "write")

    assert "not allowed to write" in mensagem
    assert "moved" not in mensagem


def test_o_relatorio_fica_onde_o_usuario_aceitou(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    relatorio = tmp_path / "cena Project" / "cena.md"
    aceito = str(tmp_path / "cena.md")
    dialogs.set_window_backend(lambda *args: aceito)

    assert dialogs.pick("save_file", "report", "Save report", initial=str(relatorio)) == {
        "path": aceito
    }
