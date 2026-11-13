import ast
from pathlib import Path


def test_all_conversion_issues_are_ascii():
    # Verifica todos os arquivos fonte em busca de report.error, report.warning e report.info
    # para garantir que as mensagens hardcoded sao puramente ASCII.
    for py_file in Path("src/aafton").rglob("*.py"):
        content = py_file.read_bytes()
        if content.startswith(b"\xef\xbb\xbf"):
            content = content[3:]

        tree = ast.parse(content.decode("utf-8"))

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute) and node.func.attr in (
                    "info",
                    "warning",
                    "error",
                ):
                    if isinstance(node.func.value, ast.Name) and node.func.value.id == "report":
                        if len(node.args) >= 2:
                            msg_node = node.args[1]
                            strings_to_check = []
                            if isinstance(msg_node, ast.Constant):
                                strings_to_check.append(msg_node.value)
                            elif isinstance(msg_node, ast.JoinedStr):
                                for val in msg_node.values:
                                    if isinstance(val, ast.Constant):
                                        strings_to_check.append(val.value)

                            for s in strings_to_check:
                                if isinstance(s, str):
                                    for char in s:
                                        assert ord(char) < 128, (
                                            f"Caractere nao-ASCII '{char}' encontrado na mensagem: '{s}' no arquivo {py_file}"
                                        )


def test_all_exceptions_are_ascii():
    for py_file in Path("src/aafton").rglob("*.py"):
        content = py_file.read_bytes()
        if content.startswith(b"\xef\xbb\xbf"):
            content = content[3:]
        tree = ast.parse(content.decode("utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Raise)
                and getattr(node, "exc", None)
                and isinstance(node.exc, ast.Call)
            ):
                if isinstance(node.exc.func, ast.Name):
                    if len(node.exc.args) > 0:
                        msg_node = node.exc.args[0]
                        strings = []
                        if isinstance(msg_node, ast.Constant):
                            strings.append(msg_node.value)
                        elif isinstance(msg_node, ast.JoinedStr):
                            for v in msg_node.values:
                                if isinstance(v, ast.Constant):
                                    strings.append(v.value)
                        for s in strings:
                            if isinstance(s, str):
                                for char in s:
                                    assert ord(char) < 128, (
                                        f"Caractere nao-ASCII '{char}' na excecao: '{s}' em {py_file}"
                                    )
