"""Tests of the dialog layer: the filters, the backends and the answers."""

from __future__ import annotations

import sys

import pytest

from aafton.studio import dialogs, dialogs_macos, dialogs_windows

# A title with a backslash and a quote, as the page can send it.
TITLE = 'C:\\cenas\\Ana "arquivo"'

# The same title, escaped for the AppleScript string.
ESCAPED = 'C:\\\\cenas\\\\Ana \\"arquivo\\"'

# The names of the filters the page sends.
NOMES = "source als aaf edl xml fcpxml video text report session all".split()

# The scripts of Windows, as they are today.
SCRIPTS = {
    "open_file": """
Add-Type -AssemblyName System.Windows.Forms
$f = New-Object System.Windows.Forms.OpenFileDialog
$f.Title = 'Pick'
$f.Filter = 'AAF files (*.aaf)|*.aaf|All files (*.*)|*.*'
if ($f.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
    [Console]::WriteLine($f.FileName)
}
""",
    "save_file": """
Add-Type -AssemblyName System.Windows.Forms
$f = New-Object System.Windows.Forms.SaveFileDialog
$f.Title = 'Pick'
$f.Filter = 'AAF (*.aaf)|*.aaf|All (*.*)|*.*'
$f.DefaultExt = '.aaf'
if ($f.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
    [Console]::WriteLine($f.FileName)
}
""",
    "folder": """
Add-Type -AssemblyName System.Windows.Forms
$f = New-Object System.Windows.Forms.FolderBrowserDialog
$f.Description = 'Pick'
$f.ShowNewFolderButton = $true
if ($f.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
    [Console]::WriteLine($f.SelectedPath)
}
""",
}


class FakeProc:
    """A resposta de um processo do PowerShell, como o subprocess devolve."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture(autouse=True)
def _sem_janela():
    """Each test starts without the window backend of the desktop app."""
    dialogs.set_window_backend(None)
    yield
    dialogs.set_window_backend(None)


def test_a_tabela_dos_filtros_tem_o_nome_e_a_extensao_de_cada_um():
    assert set(dialogs.FILTERS) == set(NOMES)
    assert dialogs.FILTERS["source"] == ("Source files", ["aaf", "edl", "xml", "fcpxml"])
    assert dialogs.FILTERS["session"] == ("Session files", ["aaf", "als", "ptx"])
    assert dialogs.FILTERS["all"] == ("All files", [])


def test_o_texto_do_osascript_de_cada_tipo_escapa_o_titulo():
    prompt = f'with prompt "{ESCAPED}"'
    abertura = f"POSIX path of (choose file {prompt})"
    salvar = f"POSIX path of (choose file name {prompt})"
    pasta = f"POSIX path of (choose folder {prompt})"

    assert dialogs_macos.build_script("open_file", TITLE, ["aaf"]) == abertura
    assert dialogs_macos.build_script("save_file", TITLE, ["aaf"]) == salvar
    assert dialogs_macos.build_script("folder", TITLE, []) == pasta


def test_no_macos_o_dialogo_vem_do_apple_script(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    scripts = []

    def falso(script):
        scripts.append(script)
        return 0, "/Users/editor/cena.aaf\n", ""

    monkeypatch.setattr(dialogs_macos, "run_osascript", falso)

    assert dialogs.pick("open_file", "aaf", "Pick") == {"path": "/Users/editor/cena.aaf"}
    assert scripts == ['POSIX path of (choose file with prompt "Pick")']


def test_o_dialogo_cancelado_no_macos_da_caminho_nulo(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(dialogs_macos, "run_osascript", lambda script: (1, "", "(-128)"))

    assert dialogs.pick("open_file", "aaf", "Pick") == {"path": None}


def test_o_arquivo_de_outra_extensao_no_macos_da_erro(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(dialogs_macos, "run_osascript", lambda script: (0, "/tmp/cena.txt\n", ""))

    erro = {
        "error": "expected a .aaf, .edl, .xml, .fcpxml file",
        "code": "wrong_extension",
    }
    assert dialogs.pick("open_file", "source", "Pick") == erro


def test_no_windows_o_dialogo_vem_do_powershell(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    scripts = []

    def falso(script):
        scripts.append(script)
        return "C:\\cenas\\EP02.aaf"

    monkeypatch.setattr(dialogs_windows, "run_powershell", falso)

    assert dialogs.pick("open_file", "source", "Pick") == {"path": "C:\\cenas\\EP02.aaf"}
    assert "OpenFileDialog" in scripts[0]


def test_o_dialogo_cancelado_no_windows_da_caminho_nulo(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(dialogs_windows, "run_powershell", lambda script: "")

    assert dialogs.pick("open_file", "aaf", "Pick") == {"path": None}


def test_sem_dialogo_no_sistema_a_resposta_da_o_erro(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")

    erro = {"error": "no file dialog on this system", "code": "no_dialog"}
    assert dialogs.pick("open_file", "aaf", "Pick") == erro
    assert dialogs.has_backend() is False


def test_o_apple_script_que_falha_da_o_erro_com_o_codigo(monkeypatch):
    """O AppleScript que falha da a mensagem dele e o codigo do erro."""
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(dialogs_macos, "run_osascript", lambda script: (2, "", "boom"))

    erro = {"error": "boom", "code": "dialog_failed"}
    assert dialogs.pick("open_file", "aaf", "Pick") == erro


def test_o_windows_que_falha_da_o_erro_com_o_codigo(monkeypatch):
    """O PowerShell que falha da a mensagem do erro e o codigo dele."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(dialogs_windows, "run_powershell", lambda script: None)

    erro = {"error": "the dialog failed", "code": "dialog_failed"}
    assert dialogs.pick("open_file", "aaf", "Pick") == erro


def test_a_janela_que_falha_da_o_erro_com_o_codigo():
    """A janela que nao abre da a mensagem dela e o codigo do erro."""

    def janela(kind, label, extensions, title, folder, name):
        """A janela do desktop que falha antes de abrir o dialogo."""
        raise dialogs.DialogError("the window did not open")

    dialogs.set_window_backend(janela)

    erro = {"error": "the window did not open", "code": "window_not_open"}
    assert dialogs.pick("open_file", "aaf", "Pick") == erro


def test_a_janela_registrada_vence_o_sistema_e_recebe_o_filtro(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    chamadas = []

    def janela(kind, label, extensions, title, folder, name):
        chamadas.append((kind, label, extensions, title))
        return "/Users/editor/cena.aaf"

    dialogs.set_window_backend(janela)

    filtro = ("open_file", "Source files", ["aaf", "edl", "xml", "fcpxml"], "Pick")
    assert dialogs.pick("open_file", "source", "Pick") == {"path": "/Users/editor/cena.aaf"}
    assert chamadas == [filtro]
    assert dialogs.has_backend() is True


def test_a_janela_registrada_que_cancela_da_caminho_nulo():
    dialogs.set_window_backend(lambda kind, label, extensions, title, folder, name: None)

    assert dialogs.pick("open_file", "aaf", "Pick") == {"path": None}


def test_o_script_do_windows_continua_o_mesmo():
    assert dialogs_windows.build_dialog_script("open_file", "aaf", "Pick") == SCRIPTS["open_file"]
    assert dialogs_windows.build_dialog_script("save_file", "aaf", "Pick") == SCRIPTS["save_file"]
    assert dialogs_windows.build_dialog_script("folder", "all", "Pick") == SCRIPTS["folder"]


def test_o_titulo_do_windows_e_texto_e_nao_codigo():
    script = dialogs_windows.build_dialog_script("open_file", "aaf", "Ana's title")

    assert "$f.Title = 'Ana''s title'" in script


def test_a_tabela_dos_filtros_do_windows_tem_o_nome_e_a_extensao_de_cada_um():
    """Cada filtro da pagina tem o filtro do Windows com as extensoes dele."""
    for nome, (_, extensoes) in dialogs.FILTERS.items():
        if nome == "all":
            continue
        filtro = dialogs_windows.OPEN_FILTERS[nome]
        for extensao in extensoes:
            assert f"*.{extensao}" in filtro


def test_o_powershell_que_termina_com_erro_da_none(monkeypatch):
    """O processo do PowerShell que sai com erro nao devolve caminho."""
    monkeypatch.setattr(dialogs_windows.subprocess, "run", lambda *args, **kwargs: FakeProc(1))

    assert dialogs_windows.run_powershell("script") is None


def test_o_powershell_que_levanta_excecao_da_none(monkeypatch):
    """O processo do PowerShell que estoura nao devolve caminho."""

    def falha(*args, **kwargs):
        """O processo do PowerShell que nao existe no sistema."""
        raise OSError("o powershell nao existe")

    monkeypatch.setattr(dialogs_windows.subprocess, "run", falha)

    assert dialogs_windows.run_powershell("script") is None


def test_o_powershell_que_espera_demais_da_none(monkeypatch):
    """O processo do PowerShell que passa do tempo de espera nao devolve caminho."""

    def estoura(*args, **kwargs):
        """O processo do PowerShell que passa do tempo de espera."""
        raise dialogs_windows.subprocess.TimeoutExpired(cmd="powershell", timeout=120)

    monkeypatch.setattr(dialogs_windows.subprocess, "run", estoura)

    assert dialogs_windows.run_powershell("script") is None


def test_o_powershell_que_responde_da_o_caminho_escolhido(monkeypatch):
    """O processo do PowerShell que responde devolve o caminho escolhido."""
    processo = FakeProc(0, "C:\\x.aaf\n")
    monkeypatch.setattr(dialogs_windows.subprocess, "run", lambda *args, **kwargs: processo)

    assert dialogs_windows.run_powershell("script") == "C:\\x.aaf"
