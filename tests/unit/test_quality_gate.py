"""Testes do ratchet de qualidade (scripts/quality_gate.py)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts import quality_gate as qg  # noqa: E402

MODULE = "src/aafton/modulo.py"
KEY = "src/aafton/modulo.py::f"
CONFIG = {"paths": ["src/aafton", "scripts"], "exclude": []}


@pytest.fixture(autouse=True)
def _arvore_medida(tmp_path):
    """A arvore que o gate mede: os diretorios e o arquivo de configuracao.

    A medida acontece fora do Git: o repositorio temporario comeca vazio, e
    assim o gate mede os arquivos que o teste escreve, e nao so os que o
    indice do Git conhece.
    """
    (tmp_path / "src" / "aafton").mkdir(parents=True)
    (tmp_path / "scripts").mkdir(parents=True)
    (tmp_path / "quality-gate.json").write_text(json.dumps(CONFIG) + "\n", encoding="utf-8")
    try:
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, capture_output=True, check=False)
    except OSError:
        pass
    return tmp_path


def _write(root: Path, relative: str, text: str) -> Path:
    """Grava um arquivo dentro da arvore temporaria."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def _if_function(count: int, name: str = "f") -> str:
    """Gera uma funcao com count blocos if (complexity = count + 1)."""
    lines = [f"def {name}(x):"]
    for index in range(count):
        lines.append(f"    if x == {index}:")
        lines.append("        pass")
    return "\n".join(lines) + "\n"


def _write_baseline(root: Path, functions: dict, files: dict | None = None) -> Path:
    """Grava um baseline na raiz temporaria."""
    payload = {
        "version": 1,
        "limits": dict(qg.LIMITS),
        "functions": functions,
        "files": files or {},
    }
    path = root / "quality-baseline.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _read_baseline(root: Path) -> dict:
    """Le o baseline gravado na raiz temporaria."""
    return json.loads((root / "quality-baseline.json").read_text(encoding="utf-8"))


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    """Roda um comando git dentro da raiz temporaria."""
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)


def _commit_baseline(root: Path) -> None:
    """Cria um repositorio git com o baseline atual commitado."""
    if shutil.which("git") is None:
        pytest.skip("git nao disponivel")
    init = _git(root, "init", "-q")
    if init.returncode != 0:
        pytest.skip(f"git init falhou: {init.stderr}")
    _git(root, "add", "quality-baseline.json")
    commit = _git(
        root,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-q",
        "-m",
        "baseline",
    )
    if commit.returncode != 0:
        pytest.skip(f"git commit falhou: {commit.stderr}")


def test_funcao_nova_acima_do_limite_falha(tmp_path, capsys):
    """Funcao nova acima do limite falha e a linha mostra chave e limites."""
    _write(tmp_path, MODULE, _if_function(11))
    code = qg.main(["--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 1
    assert f"{KEY} complexity 12 > 10" in out


def test_arquivo_acima_do_limite_falha(tmp_path, capsys):
    """Arquivo com mais de 500 linhas falha com o limite de file_lines."""
    source = "\n".join(f"# linha {index}" for index in range(510)) + "\n"
    _write(tmp_path, "scripts/grande.py", source)
    code = qg.main(["--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 1
    assert "scripts/grande.py file_lines 510 > 500" in out


def test_entrada_do_baseline_que_cresce_falha(tmp_path, capsys):
    """Funcao que cresce acima do valor guardado no baseline falha."""
    _write_baseline(tmp_path, {KEY: {"complexity": 12}})
    _write(tmp_path, MODULE, _if_function(13))
    code = qg.main(["--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 1
    assert f"{KEY} complexity 14 > 12 (baseline)" in out


def test_melhoria_passa(tmp_path, capsys):
    """Funcao que melhora continua passando."""
    _write_baseline(tmp_path, {KEY: {"complexity": 12}})
    _write(tmp_path, MODULE, _if_function(5))
    code = qg.main(["--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 0
    assert "quality_gate: ok" in out


def test_update_reduz_valor_e_remove_entrada(tmp_path, capsys):
    """--update reduz o valor guardado e remove entrada que sumiu."""
    gone = "src/aafton/antigo.py::g"
    _write_baseline(tmp_path, {KEY: {"complexity": 12}, gone: {"complexity": 15}})
    _write(tmp_path, MODULE, _if_function(10))
    code = qg.main(["--root", str(tmp_path), "--update"])
    out = capsys.readouterr().out
    assert code == 0
    assert _read_baseline(tmp_path)["functions"] == {KEY: {"complexity": 11}}
    assert "removed entries do not come back to the baseline" in out
    assert gone in out


def test_update_recusa_subir_valor(tmp_path, capsys):
    """--update nao sobe valor e deixa o arquivo do baseline intacto."""
    path = _write_baseline(tmp_path, {KEY: {"complexity": 12}})
    before = path.read_bytes()
    _write(tmp_path, MODULE, _if_function(13))
    code = qg.main(["--root", str(tmp_path), "--update"])
    out = capsys.readouterr().out
    assert code == 1
    assert path.read_bytes() == before
    assert "quality_gate: --update does not raise a value nor create an entry" in out


def test_update_recusa_criar_entrada(tmp_path):
    """--update nao cria entrada nova acima do limite."""
    path = _write_baseline(tmp_path, {})
    before = path.read_bytes()
    _write(tmp_path, MODULE, _if_function(11))
    code = qg.main(["--root", str(tmp_path), "--update"])
    assert code == 1
    assert path.read_bytes() == before


def test_strict_ignora_baseline(tmp_path, capsys):
    """--strict aplica os limites e ignora o valor guardado no baseline."""
    _write_baseline(tmp_path, {KEY: {"complexity": 12}})
    _write(tmp_path, MODULE, _if_function(13))
    code = qg.main(["--root", str(tmp_path), "--strict", MODULE])
    out = capsys.readouterr().out
    assert code == 1
    assert f"{KEY} complexity 14 > 10" in out
    assert "baseline" not in out


def test_strict_ignora_os_outros_arquivos(tmp_path, capsys):
    """--strict nao olha os arquivos que nao foram pedidos."""
    _write(tmp_path, MODULE, _if_function(11))
    _write(tmp_path, "scripts/fora.py", _if_function(11, "g"))
    code = qg.main(["--root", str(tmp_path), "--strict", MODULE])
    out = capsys.readouterr().out
    assert code == 1
    assert KEY in out
    assert "scripts/fora.py" not in out


def test_ratchet_ref_falha_com_entrada_nova(tmp_path, capsys):
    """--ratchet-ref falha quando a arvore ganha entrada nova."""
    _write(tmp_path, MODULE, _if_function(3))
    _write_baseline(tmp_path, {})
    _commit_baseline(tmp_path)
    _write_baseline(tmp_path, {KEY: {"complexity": 12}})
    code = qg.main(["--root", str(tmp_path), "--ratchet-ref", "HEAD"])
    out = capsys.readouterr().out
    assert code == 1
    assert KEY in out


def test_ratchet_ref_falha_com_valor_maior(tmp_path, capsys):
    """--ratchet-ref falha quando a arvore sobe um valor."""
    _write(tmp_path, MODULE, _if_function(3))
    _write_baseline(tmp_path, {KEY: {"complexity": 12}})
    _commit_baseline(tmp_path)
    _write_baseline(tmp_path, {KEY: {"complexity": 14}})
    code = qg.main(["--root", str(tmp_path), "--ratchet-ref", "HEAD"])
    out = capsys.readouterr().out
    assert code == 1
    assert "complexity 14 > 12" in out


def test_ratchet_ref_passa_com_valor_menor_ou_igual(tmp_path, capsys):
    """--ratchet-ref passa quando a arvore mantem ou baixa o valor."""
    _write(tmp_path, MODULE, _if_function(3))
    _write_baseline(tmp_path, {KEY: {"complexity": 12}})
    _commit_baseline(tmp_path)
    _write_baseline(tmp_path, {KEY: {"complexity": 12}})
    code = qg.main(["--root", str(tmp_path), "--ratchet-ref", "HEAD"])
    out = capsys.readouterr().out
    assert code == 0
    assert "ratchet ok" in out
    _write_baseline(tmp_path, {KEY: {"complexity": 10}})
    code = qg.main(["--root", str(tmp_path), "--ratchet-ref", "HEAD"])
    assert code == 0


def test_ratchet_ref_sem_arquivo_no_ref_falha(tmp_path, capsys):
    """Sem arquivo no ref o baseline vale vazio e a arvore falha."""
    if shutil.which("git") is None:
        pytest.skip("git nao disponivel")
    init = _git(tmp_path, "init", "-q")
    if init.returncode != 0:
        pytest.skip(f"git init falhou: {init.stderr}")
    _write(tmp_path, "nota.txt", "nota\n")
    _git(tmp_path, "add", "nota.txt")
    commit = _git(
        tmp_path,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-q",
        "-m",
        "nota",
    )
    if commit.returncode != 0:
        pytest.skip(f"git commit falhou: {commit.stderr}")
    _write_baseline(tmp_path, {KEY: {"complexity": 12}})
    code = qg.main(["--root", str(tmp_path), "--ratchet-ref", "HEAD"])
    out = capsys.readouterr().out
    assert code == 1
    assert KEY in out


def test_ratchet_ref_com_ref_inexistente_da_erro(tmp_path):
    """Ref que o git nao conhece termina com codigo 2."""
    if shutil.which("git") is None:
        pytest.skip("git nao disponivel")
    init = _git(tmp_path, "init", "-q")
    if init.returncode != 0:
        pytest.skip(f"git init falhou: {init.stderr}")
    _write_baseline(tmp_path, {})
    code = qg.main(["--root", str(tmp_path), "--ratchet-ref", "ref_que_nao_existe"])
    assert code == 2


def test_init_cria_baseline(tmp_path, capsys):
    """--init grava o baseline com apenas as metricas acima do limite."""
    _write(tmp_path, MODULE, _if_function(11))
    code = qg.main(["--root", str(tmp_path), "--init"])
    out = capsys.readouterr().out
    assert code == 0
    payload = _read_baseline(tmp_path)
    assert payload["version"] == 1
    assert payload["limits"] == qg.LIMITS
    assert payload["functions"] == {KEY: {"complexity": 12}}
    assert payload["files"] == {}
    assert "baseline created" in out


def test_init_recusa_arquivo_existente(tmp_path):
    """--init nao sobrescreve um baseline que ja existe."""
    path = _write_baseline(tmp_path, {})
    before = path.read_bytes()
    _write(tmp_path, MODULE, _if_function(3))
    code = qg.main(["--root", str(tmp_path), "--init"])
    assert code == 1
    assert path.read_bytes() == before


def test_contagem_de_complexidade(tmp_path):
    """Elif, booleanos, compreensao, IfExp, match e funcao aninhada."""
    source = (
        "def com_elif(x):\n"
        "    if x > 0:\n"
        "        return 1\n"
        "    elif x < 0:\n"
        "        return 2\n"
        "    return 0\n"
        "\n"
        "def com_boolop(a, b, c):\n"
        "    if a and b and c:\n"
        "        return 1\n"
        "    return 0\n"
        "\n"
        "def com_compreensao(itens):\n"
        "    return [i for i in itens if i > 0]\n"
        "\n"
        "def com_ifexp(x):\n"
        "    return 1 if x else 2\n"
        "\n"
        "def com_match(x):\n"
        "    match x:\n"
        "        case 1:\n"
        "            return 'a'\n"
        "        case 2:\n"
        "            return 'b'\n"
        "        case _:\n"
        "            return 'c'\n"
        "\n"
        "def externa(x):\n"
        "    def interna(y):\n"
        "        if y:\n"
        "            return 1\n"
        "        return 0\n"
        "    return interna(x)\n"
    )
    _write(tmp_path, MODULE, source)
    functions = qg.measure(tmp_path)["functions"]
    prefix = "src/aafton/modulo.py::"
    assert functions[prefix + "com_elif"]["complexity"] == 3
    assert functions[prefix + "com_boolop"]["complexity"] == 4
    assert functions[prefix + "com_compreensao"]["complexity"] == 2
    assert functions[prefix + "com_ifexp"]["complexity"] == 2
    assert functions[prefix + "com_match"]["complexity"] == 4
    assert functions[prefix + "externa"]["complexity"] == 1
    assert functions[prefix + "externa.interna"]["complexity"] == 2


def test_qualname_e_tamanho_de_funcao(tmp_path):
    """Qualname junta classe e funcao e o tamanho conta da def ate o fim."""
    source = (
        "class Klass:\n"
        "    def metodo(self):\n"
        "        return 1\n"
        "\n"
        "def externa():\n"
        "    def interna():\n"
        "        return 2\n"
        "    return interna\n"
    )
    _write(tmp_path, MODULE, source)
    functions = qg.measure(tmp_path)["functions"]
    prefix = "src/aafton/modulo.py::"
    assert functions[prefix + "Klass.metodo"]["function_lines"] == 2
    assert functions[prefix + "externa"]["function_lines"] == 4
    assert functions[prefix + "externa.interna"]["function_lines"] == 2


def test_funcao_acima_do_limite_de_linhas_falha(tmp_path, capsys):
    """Funcao com mais de 60 linhas viola o limite de function_lines."""
    body = "\n".join("    x = 1" for _ in range(69))
    _write(tmp_path, MODULE, f"def f():\n{body}\n")
    code = qg.main(["--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 1
    assert f"{KEY} function_lines 70 > 60" in out


def test_uso_invalido_da_codigo_2(tmp_path):
    """Opcoes que nao podem andar juntas terminam com codigo 2."""
    with pytest.raises(SystemExit) as excinfo:
        qg.main(["--root", str(tmp_path), "--init", "--update"])
    assert excinfo.value.code == 2


def test_config_ausente_termina_com_codigo_2(tmp_path):
    """Sem o arquivo de configuracao o gate recusa a medicao."""
    (tmp_path / "quality-gate.json").unlink()
    assert qg.main(["--root", str(tmp_path)]) == 2


def test_config_com_caminho_invalido_termina_com_codigo_2(tmp_path):
    """Caminho que sobe da raiz ou traz drive letter nao serve."""
    (tmp_path / "quality-gate.json").write_text(
        json.dumps({"paths": ["../fora"], "exclude": []}) + "\n", encoding="utf-8"
    )
    assert qg.main(["--root", str(tmp_path)]) == 2


def test_config_com_chave_desconhecida_termina_com_codigo_2(tmp_path):
    """Chave que o gate nao conhece faz a configuracao ser recusada."""
    (tmp_path / "quality-gate.json").write_text(
        json.dumps({"paths": ["src/aafton"], "exclude": [], "extra": 1}) + "\n",
        encoding="utf-8",
    )
    assert qg.main(["--root", str(tmp_path)]) == 2


def test_somente_o_que_o_git_rastreia_e_medido(tmp_path):
    """Arquivo fora do indice do Git nao entra na medicao do gate."""
    _write(tmp_path, MODULE, _if_function(11))
    _write(tmp_path, "scripts/rastreado.py", "x = 1\n")
    _git(tmp_path, "add", "scripts/rastreado.py")
    assert qg.main(["--root", str(tmp_path)]) == 0
    _git(tmp_path, "add", MODULE)
    assert qg.main(["--root", str(tmp_path)]) == 1


def test_gate_no_repositorio_real():
    """O gate roda no repositorio real e precisa passar."""
    result = subprocess.run(
        [sys.executable, "scripts/quality_gate.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
