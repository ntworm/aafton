"""Corpus externo pinado e fixtures versionados: falta ou divergencia falha, nunca pula.

Antes, 16 testes pulavam em silencio porque apontavam para caminhos absolutos
do Desktop que deixaram de existir. A regra dos goldens
e falhar com mensagem clara; estes testes provam isso para o corpus de WAV e
para os AAF de tests/fixtures/aaf.
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import wave
from pathlib import Path

import pytest

import tests.support.paths as paths
from tests.support.paths import (
    CORPUS_ENV,
    CORPUS_VERIFY_ENV,
    REPO,
    corpus_dir,
    corpus_manifest_path,
    fixture_aaf,
    load_corpus_manifest,
)

Failed = pytest.fail.Exception
Skipped = pytest.skip.Exception

MANIFEST_SCRIPT = REPO / "scripts" / "corpus_manifest.py"
CHECK_SCRIPT = REPO / "scripts" / "check_suite_no_skips.py"
CAMPOS = {"name", "bytes", "sha256", "channels", "sample_rate", "frames", "format"}


def _wav_bytes(frames, *, channels=1, rate=48000, bits=16, tag=1, subformat=None, magic=b"RIFF"):
    block_align = channels * bits // 8
    fmt = struct.pack("<HHIIHH", tag, channels, rate, rate * block_align, block_align, bits)
    if tag == 0xFFFE:
        fmt += struct.pack("<HHI", 22, bits, 0) + struct.pack("<H", subformat) + b"\x00" * 14
    data = bytes(range(256)) * (frames * block_align // 256) + b"\x01" * (
        frames * block_align % 256
    )
    corpo = (
        b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(data)) + data
    )
    return magic + struct.pack("<I", 4 + len(corpo)) + b"WAVE" + corpo


def _gerar_manifesto(pasta: Path, saida: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(MANIFEST_SCRIPT), str(pasta), str(saida)],
        capture_output=True,
        text=True,
    )


@pytest.fixture
def corpus_sintetico(tmp_path):
    pasta = tmp_path / "corpus"
    pasta.mkdir()
    (pasta / "mono16.wav").write_bytes(_wav_bytes(1000))
    (pasta / "estereo24.wav").write_bytes(_wav_bytes(500, channels=2, bits=24))
    (pasta / "float32.wav").write_bytes(
        _wav_bytes(300, channels=2, bits=32, tag=0xFFFE, subformat=3)
    )
    (pasta / "notas.txt").write_text("nao e audio", encoding="utf-8")
    manifesto = tmp_path / "sintetico.json"
    res = _gerar_manifesto(pasta, manifesto)
    assert res.returncode == 0, res.stderr
    return pasta, manifesto


def _reescrever(manifesto: Path, nome: str, campo: str, valor) -> None:
    dados = json.loads(manifesto.read_text(encoding="utf-8"))
    for entrada in dados["files"]:
        if entrada["name"] == nome:
            entrada[campo] = valor
    manifesto.write_text(json.dumps(dados), encoding="utf-8")


# --- falha, nunca pulo --------------------------------------------------------


def test_corpus_em_pasta_vazia_falha_citando_a_variavel(tmp_path, monkeypatch):
    vazia = tmp_path / "vazia"
    vazia.mkdir()
    monkeypatch.setenv(CORPUS_ENV, str(vazia))

    with pytest.raises(Failed) as erro:
        corpus_dir("teste_aaf")

    assert not isinstance(erro.value, Skipped)
    mensagem = str(erro.value)
    assert CORPUS_ENV in mensagem
    assert "ausente" in mensagem
    total = len(load_corpus_manifest("teste_aaf")["files"])
    assert f"{total} de {total} arquivos" in mensagem


def test_corpus_em_pasta_inexistente_falha_citando_a_variavel(tmp_path, monkeypatch):
    monkeypatch.setenv(CORPUS_ENV, str(tmp_path / "nao_existe"))

    with pytest.raises(Failed) as erro:
        corpus_dir("teste_aaf")

    assert CORPUS_ENV in str(erro.value)
    assert "nao encontrado" in str(erro.value)


def test_pytest_relata_falha_e_nao_pulo_com_corpus_vazio(tmp_path):
    """Caminho completo: um teste consumidor roda num pytest separado e falha."""
    vazia = tmp_path / "vazia"
    vazia.mkdir()
    consumidor = tmp_path / "test_consumidor.py"
    consumidor.write_text(
        "from tests.support.paths import corpus_dir\n\n"
        "def test_usa_corpus():\n"
        "    corpus_dir('teste_aaf')\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env[CORPUS_ENV] = str(vazia)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(REPO), env.get("PYTHONPATH", "")]))

    res = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-rfs",
            "-p",
            "no:cacheprovider",
            "-o",
            "addopts=",
            str(consumidor),
        ],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
        env=env,
    )

    saida = res.stdout + res.stderr
    assert res.returncode == 1, saida
    assert "1 failed" in saida
    assert "skipped" not in saida
    assert CORPUS_ENV in saida


def test_manifesto_com_tamanho_divergente_falha(corpus_sintetico, monkeypatch):
    pasta, manifesto = corpus_sintetico
    _reescrever(manifesto, "mono16.wav", "bytes", 12345)
    monkeypatch.setenv(CORPUS_ENV, str(pasta))

    with pytest.raises(Failed) as erro:
        corpus_dir("sintetico", manifest_path=manifesto)

    mensagem = str(erro.value)
    assert CORPUS_ENV in mensagem
    assert "tamanho difere: mono16.wav" in mensagem
    assert "1 de 3 arquivos" in mensagem


def test_sha256_divergente_so_e_conferido_com_verify_hash(corpus_sintetico, monkeypatch):
    pasta, manifesto = corpus_sintetico
    _reescrever(manifesto, "float32.wav", "sha256", "0" * 64)
    monkeypatch.setenv(CORPUS_ENV, str(pasta))
    monkeypatch.delenv(CORPUS_VERIFY_ENV, raising=False)

    assert corpus_dir("sintetico", manifest_path=manifesto) == pasta

    monkeypatch.setenv(CORPUS_VERIFY_ENV, "hash")
    with pytest.raises(Failed) as erro:
        corpus_dir("sintetico", manifest_path=manifesto)
    assert "sha256 difere: float32.wav" in str(erro.value)
    assert CORPUS_ENV in str(erro.value)


def test_corpus_conferido_devolve_a_pasta(corpus_sintetico, monkeypatch):
    pasta, manifesto = corpus_sintetico
    monkeypatch.setenv(CORPUS_ENV, str(pasta))
    monkeypatch.setenv(CORPUS_VERIFY_ENV, "hash")

    assert corpus_dir("sintetico", manifest_path=manifesto) == pasta


def test_fixture_aaf_ausente_falha_e_nao_pula():
    with pytest.raises(Failed) as erro:
        fixture_aaf("NAO_EXISTE.aaf")
    assert not isinstance(erro.value, Skipped)
    assert "tests/fixtures/aaf" in str(erro.value)


def test_fixture_aaf_versionado_resolve_dentro_do_repo():
    caminho = fixture_aaf("TESTE_AFF_PROTOOLS.aaf")
    assert caminho.is_file()
    assert caminho.parent == REPO / "tests" / "fixtures" / "aaf"


# --- checagem da suite sem pulos ----------------------------------------------


def _rodar_checagem(tmp_path, corpo: str) -> subprocess.CompletedProcess:
    alvo = tmp_path / "test_alvo.py"
    alvo.write_text(corpo, encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "src")
    return subprocess.run(
        # pytest 9 no Windows compara cada irmao com lstat; sem corte, um
        # temporario alheio removido durante a coleta causa FileNotFoundError.
        [
            sys.executable,
            str(CHECK_SCRIPT),
            str(alvo),
            "--rootdir",
            str(tmp_path),
            "--confcutdir",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO),
        env=env,
    )


def test_checagem_reprova_suite_com_pulo(tmp_path):
    res = _rodar_checagem(
        tmp_path,
        "import pytest\n\n"
        "def test_passa():\n    pass\n\n"
        "def test_pula():\n    pytest.skip('midia externa ausente')\n",
    )

    assert res.returncode == 1, res.stdout + res.stderr
    assert "passed=1 failed=0 errors=0 skipped=1" in res.stdout, res.stdout + res.stderr
    assert "midia externa ausente" in res.stdout, res.stdout + res.stderr


def test_checagem_reprova_suite_com_falha(tmp_path):
    res = _rodar_checagem(tmp_path, "def test_falha():\n    assert False\n")

    assert res.returncode == 1, res.stdout + res.stderr
    assert "passed=0 failed=1" in res.stdout


def test_checagem_aprova_suite_sem_pulo_nem_falha(tmp_path):
    res = _rodar_checagem(tmp_path, "def test_passa():\n    pass\n")

    assert res.returncode == 0, res.stdout + res.stderr
    assert "passed=1 failed=0 errors=0 skipped=0 xfailed=0" in res.stdout


# --- gerador do manifesto -----------------------------------------------------


def test_gerador_descreve_cada_wav_e_ignora_o_resto(corpus_sintetico):
    _, manifesto = corpus_sintetico
    dados = json.loads(manifesto.read_text(encoding="utf-8"))

    assert dados["corpus"] == "sintetico"
    por_nome = {e["name"]: e for e in dados["files"]}
    assert sorted(por_nome) == ["estereo24.wav", "float32.wav", "mono16.wav"]
    assert por_nome["mono16.wav"]["channels"] == 1
    assert por_nome["mono16.wav"]["frames"] == 1000
    assert por_nome["mono16.wav"]["format"] == "pcm16"
    assert por_nome["estereo24.wav"]["channels"] == 2
    assert por_nome["estereo24.wav"]["frames"] == 500
    assert por_nome["estereo24.wav"]["format"] == "pcm24"
    assert por_nome["float32.wav"]["frames"] == 300
    assert por_nome["float32.wav"]["format"] == "float32"
    for entrada in dados["files"]:
        assert CAMPOS <= set(entrada)
        assert entrada["sample_rate"] == 48000
        assert len(entrada["sha256"]) == 64


def test_gerador_recusa_rf64(tmp_path):
    pasta = tmp_path / "rf64"
    pasta.mkdir()
    (pasta / "grande.wav").write_bytes(_wav_bytes(10, magic=b"RF64"))

    res = _gerar_manifesto(pasta, tmp_path / "rf64.json")

    assert res.returncode == 1
    assert "RF64" in res.stderr
    assert not (tmp_path / "rf64.json").exists()


# --- corpus real pinado -------------------------------------------------------


def test_manifesto_pinado_lista_os_21_wav_do_corpus():
    manifesto = load_corpus_manifest("teste_aaf")
    arquivos = manifesto["files"]

    assert len(arquivos) == 21
    assert len({e["name"] for e in arquivos}) == 21
    for entrada in arquivos:
        assert CAMPOS <= set(entrada)
        assert entrada["name"].lower().endswith(".wav")
        assert entrada["bytes"] > 0 and entrada["frames"] > 0
        assert len(entrada["sha256"]) == 64
    assert sum(e["bytes"] for e in arquivos) == 116045264


def test_pasta_do_manifesto_nao_versiona_audio():
    pasta = corpus_manifest_path("teste_aaf").parent
    assert {p.suffix for p in pasta.iterdir() if p.is_file()} == {".json"}


def test_corpus_local_confere_com_o_manifesto_pinado():
    pasta = corpus_dir("teste_aaf")
    for entrada in load_corpus_manifest("teste_aaf")["files"]:
        assert (pasta / entrada["name"]).stat().st_size == entrada["bytes"]


# --- re-export humano do P03 (D09) -------------------------------------------


def test_reexport_ausente_pula(monkeypatch):
    from tests.support.paths import reexport_corpus

    monkeypatch.delenv("AAFTON_REEXPORT_DIR", raising=False)
    with pytest.raises(Skipped) as erro:
        reexport_corpus()
    assert str(erro.value).startswith("not distributed:")


def test_reexport_configurado_vazio_pula(tmp_path, monkeypatch):
    from tests.support.paths import reexport_corpus

    monkeypatch.setenv("AAFTON_REEXPORT_DIR", str(tmp_path))
    with pytest.raises(Skipped) as erro:
        reexport_corpus()
    assert str(erro.value).startswith("not distributed:")


def test_reexport_variavel_vazia_falha_sem_pular(monkeypatch):
    from tests.support.paths import reexport_corpus

    monkeypatch.setenv("AAFTON_REEXPORT_DIR", "")
    with pytest.raises(Failed, match="AAFTON_REEXPORT_DIR"):
        reexport_corpus()


def test_reexport_manifesto_ausente_falha(tmp_path):
    from tests.support.paths import reexport_corpus

    with pytest.raises(Failed, match="manifesto ausente"):
        reexport_corpus(manifest_path=tmp_path / "inexistente.json")


def test_manifesto_do_reexport_presente_nunca_pula(tmp_path, monkeypatch):
    from tests.support.paths import reexport_corpus

    monkeypatch.delenv("AAFTON_REEXPORT_DIR", raising=False)
    manifesto = tmp_path / "reexport.json"
    manifesto.write_text('{"files": []}', encoding="utf-8")
    with pytest.raises(Failed):
        reexport_corpus(manifest_path=manifesto)


def test_gerador_pina_aaf_junto_com_wav(corpus_sintetico, tmp_path):
    pasta, _ = corpus_sintetico
    (pasta / "sessao.aaf").write_bytes(b"AAF externo")
    manifesto = tmp_path / "reexport.json"
    res = _gerar_manifesto(pasta, manifesto)
    assert res.returncode == 0, res.stderr
    entrada = next(
        e for e in json.loads(manifesto.read_text())["files"] if e["name"] == "sessao.aaf"
    )
    assert entrada["format"] == "aaf"
    assert entrada["bytes"] == 11
    assert len(entrada["sha256"]) == 64


def test_reexport_resolve_aaf_e_midia_e_confere_hash(corpus_sintetico, tmp_path, monkeypatch):
    from tests.support.paths import reexport_corpus

    pasta, _ = corpus_sintetico
    aaf = pasta / "sessao.aaf"
    aaf.write_bytes(b"AAF externo")
    manifesto = tmp_path / "reexport.json"
    assert _gerar_manifesto(pasta, manifesto).returncode == 0
    monkeypatch.setenv("AAFTON_REEXPORT_DIR", str(pasta))
    assert reexport_corpus(manifest_path=manifesto) == (aaf, pasta)
    aaf.write_bytes(b"AAF trocado")  # mesmo tamanho
    with pytest.raises(Skipped) as erro:
        reexport_corpus(manifest_path=manifesto)
    assert str(erro.value).startswith("not distributed:")


# --- geracao automatica do corpus de silencio ---------------------------------


def _wav_silencio(caminho: Path, *, canais: int = 1, quadros: int = 64) -> None:
    """Grava um WAV de silencio PCM16 de 16 bits."""
    with wave.open(str(caminho), "wb") as w:
        w.setnchannels(canais)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(b"\0" * (2 * canais * quadros))


@pytest.fixture
def manifesto_silencio(tmp_path):
    """Manifesto pequeno de silencio PCM16; nunca o manifesto real de 116 MB."""
    pasta = tmp_path / "silencio"
    pasta.mkdir()
    _wav_silencio(pasta / "um.wav")
    _wav_silencio(pasta / "dois.wav", quadros=128)
    _wav_silencio(pasta / "tres.wav", canais=2)
    manifesto = tmp_path / "silencio.json"
    res = _gerar_manifesto(pasta, manifesto)
    assert res.returncode == 0, res.stderr
    return manifesto


def _espiar_gerador(monkeypatch) -> list:
    """Troca o gerador por um espiao que devolve 0 e guarda as chamadas."""
    chamadas = []

    def espiao(manifesto_caminho, pasta):
        chamadas.append((manifesto_caminho, pasta))
        return 0

    monkeypatch.setattr(paths, "_gerar_corpus_de_silencio", espiao)
    return chamadas


def test_silencio_gerado_quando_a_pasta_padrao_falta(manifesto_silencio, tmp_path, monkeypatch):
    monkeypatch.delenv(CORPUS_ENV, raising=False)
    monkeypatch.delenv(CORPUS_VERIFY_ENV, raising=False)
    novo = tmp_path / "novo"

    pasta = corpus_dir("teste_aaf", manifest_path=manifesto_silencio, default_dir=novo)

    assert pasta == novo
    entradas = json.loads(manifesto_silencio.read_text(encoding="utf-8"))["files"]
    assert len(entradas) == 3
    for entrada in entradas:
        arquivo = pasta / entrada["name"]
        assert arquivo.is_file()
        assert arquivo.stat().st_size == entrada["bytes"]


def test_silencio_nao_gerado_com_variavel_definida(manifesto_silencio, tmp_path, monkeypatch):
    chamadas = _espiar_gerador(monkeypatch)
    ausente = tmp_path / "ausente"
    padrao = tmp_path / "padrao"
    monkeypatch.setenv(CORPUS_ENV, str(ausente))

    with pytest.raises(Failed) as erro:
        corpus_dir("teste_aaf", manifest_path=manifesto_silencio, default_dir=padrao)

    assert not isinstance(erro.value, Skipped)
    assert chamadas == []
    assert not ausente.exists()
    assert not padrao.exists()


def test_silencio_nao_gerado_em_pasta_existente(manifesto_silencio, tmp_path, monkeypatch):
    chamadas = _espiar_gerador(monkeypatch)
    monkeypatch.delenv(CORPUS_ENV, raising=False)
    vazia = tmp_path / "vazia"
    vazia.mkdir()

    with pytest.raises(Failed) as erro:
        corpus_dir("teste_aaf", manifest_path=manifesto_silencio, default_dir=vazia)

    assert "ausente" in str(erro.value)
    assert chamadas == []
    assert list(vazia.iterdir()) == []
