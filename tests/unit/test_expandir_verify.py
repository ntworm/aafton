"""Verify das irmas, export de volta com as irmas e a CLI do --production-audio."""

import hashlib
import json
import shutil

import pytest

import aafton.pipeline as pipeline
import aafton.verify as verify_module
from aafton.als_read import read_session
from aafton.cli import main
from aafton.expandir_verify import NAO_CONFERIDAS
from aafton.report import Report
from aafton.sidecar import sidecar_path_for
from aafton.studio.app_set import convert_to_aaf, read_set
from aafton.verify import verify_conversion
from tests.aaf_factory import build_calibration_aaf
from tests.unit.test_expandir import (
    FRAMES,
    SEP,
    TC,
    clip,
    copia,
    importar,
    montar_cena,
    original,
)
from tests.unit.test_som_direto_leitor import RATE, duas_diarias

LINHA_OK = "Siblings: 7 sibling clips checked against the production audio (0 differ)"


@pytest.fixture
def cena(tmp_path):
    return montar_cena(tmp_path)


def test_verify_aprova_as_irmas_contra_o_gravador(cena):
    saida, _ = importar(cena)
    resultado = verify_conversion(cena["aaf"], saida)
    assert resultado.ok, resultado.to_table()
    assert resultado.audio_compared == resultado.audio_expected == 5
    assert resultado.expanded_lines == [LINHA_OK]
    assert "7 sibling clips checked" in resultado.to_table()


def test_verify_reprova_irma_movida_um_sample(cena, monkeypatch):
    original = pipeline.expand_production_channels

    def com_irma_movida(session, *args, **kwargs):
        plano = original(session, *args, **kwargs)
        irma = next(t for t in session.tracks if t.name.startswith(f"Audio 1 {SEP} In 5"))
        irma.clips[0].timeline_start_samples += 1
        irma.clips[0].timeline_end_samples += 1
        return plano

    monkeypatch.setattr(pipeline, "expand_production_channels", com_irma_movida)
    saida, _ = importar(cena)
    resultado = verify_conversion(cena["aaf"], saida)
    assert not resultado.ok
    assert resultado.expanded_lines == [LINHA_OK.replace("(0 differ)", "(1 differ)")]
    assert any("1 sibling clips differ" in erro for erro in resultado.errors)


def test_verify_reprova_irma_com_offset_um_sample_fora_do_corte(cena, monkeypatch):
    original = pipeline.expand_production_channels

    def com_offset_errado(session, *args, **kwargs):
        plano = original(session, *args, **kwargs)
        irma = next(t for t in session.tracks if t.name.startswith(f"Audio 1 {SEP} In 5"))
        irma.clips[0].source_offset_samples += 1
        return plano

    monkeypatch.setattr(pipeline, "expand_production_channels", com_offset_errado)
    saida, _ = importar(cena)
    resultado = verify_conversion(cena["aaf"], saida)
    assert not resultado.ok
    assert resultado.sibling_clips["differ"] == 1
    assert any("1 sibling clips differ" in erro for erro in resultado.errors)


def test_verify_reprova_quando_o_offset_do_editor_no_aaf_muda(cena, monkeypatch):
    saida, _ = importar(cena)
    original = verify_module.read_aaf

    def com_offset_mudado(*args, **kwargs):
        sessao = original(*args, **kwargs)
        dona = next(t for t in sessao.tracks if t.name == "Audio 1")
        dona.clips[0].source_offset_samples += 1
        return sessao

    monkeypatch.setattr(verify_module, "read_aaf", com_offset_mudado)
    resultado = verify_conversion(cena["aaf"], saida)
    assert not resultado.ok
    # As tres irmas da Audio 1 tem um clip cada, todos tirados daquele clip.
    assert resultado.sibling_clips["differ"] == 3


def test_verify_reprova_quando_a_midia_do_editor_difere_do_original(cena):
    saida, _ = importar(cena)
    copia = cena["tmp"] / "AAF Media" / "a3.wav"
    bruto = bytearray(copia.read_bytes())
    # Frame 1010 da copia do editor, dentro do trecho do clip (offset 1000).
    inicio_dados = len(bruto) - FRAMES * 2
    bruto[inicio_dados + 1010 * 2 + 1] ^= 0x7F
    copia.write_bytes(bruto)
    resultado = verify_conversion(cena["aaf"], saida)
    assert not resultado.ok
    assert resultado.sibling_clips["differ"] == 3


def test_copia_do_aaf_do_editor_e_reconhecida_pelo_hash(cena):
    saida, _ = importar(cena)
    dados = json.loads(sidecar_path_for(saida).read_text(encoding="utf-8"))["expanded"]
    assert dados["editor_sha256"] == hashlib.sha256(cena["aaf"].read_bytes()).hexdigest()
    outra = cena["tmp"] / "outra pasta" / "copia.aaf"
    outra.parent.mkdir()
    shutil.copyfile(cena["aaf"], outra)
    resultado = verify_conversion(outra, saida)
    assert resultado.ok, resultado.to_table()
    assert resultado.expanded_lines == [LINHA_OK]


def test_aaf_que_nao_e_do_editor_deixa_as_irmas_sem_conferir(cena):
    saida, _ = importar(cena)
    caminho = sidecar_path_for(saida)
    dados = json.loads(caminho.read_text(encoding="utf-8"))
    dados["expanded"]["editor"] = str(cena["tmp"] / "outro.aaf")
    dados["expanded"]["editor_sha256"] = "0" * 64
    caminho.write_text(json.dumps(dados), encoding="utf-8")
    resultado = verify_conversion(cena["aaf"], saida)
    assert resultado.expanded_lines == [NAO_CONFERIDAS]
    assert NAO_CONFERIDAS.startswith("Siblings: not checked (the AAF is neither the editor AAF")
    assert not getattr(resultado, "sibling_clips", None)


def test_irma_com_o_nome_de_uma_track_do_editor_ganha_um_numero(tmp_path):
    gravador, midia = tmp_path / "som" / "05-05-25", tmp_path / "AAF Media"
    entradas = [(3, "BOOM"), (4, "LAP")]
    _, canais_x = original(gravador, "X-001.WAV", "1", "001", entradas)
    _, canais_y = original(gravador, "Y-002.WAV", "1", "002", entradas, TC + RATE)
    copia_x = copia(midia, "x3.wav", canais_x[0], "1", "001", (3, "BOOM"))
    copia_y = copia(midia, "y3.wav", canais_y[1], "1", "002", (4, "LAP"), TC + RATE)
    ocupado = f"A {SEP} In 4 {SEP} LAP"
    tracks = [
        {"name": "A", "clips": [clip(copia_x, "X-001.WAV", 1000, 2000)]},
        {"name": ocupado, "clips": [clip(copia_y, "Y-002.WAV", 30000, 2000)]},
    ]
    aaf = build_calibration_aaf(tmp_path / "dup.aaf", tracks, frames=60000)
    cena = {"aaf": aaf, "folder": tmp_path / "som", "tmp": tmp_path}
    saida, _ = importar(cena)
    nomes = [t.name for t in read_session(saida, Report(strict=False)).tracks]
    assert nomes == ["A", f"{ocupado} (2)", ocupado, f"{ocupado} {SEP} In 3 {SEP} BOOM"]
    dados = json.loads(sidecar_path_for(saida).read_text(encoding="utf-8"))["expanded"]
    assert sorted(dados["tracks"]) == [f"{ocupado} (2)", f"{ocupado} {SEP} In 3 {SEP} BOOM"]
    resultado = verify_conversion(aaf, saida)
    assert resultado.ok, resultado.to_table()
    assert resultado.audio_compared == 2
    assert resultado.sibling_clips == {"checked": 2, "differ": 0, "text": None}


def test_verify_reprova_amostras_diferentes_do_gravador(cena):
    saida, _ = importar(cena)
    bruto = bytearray(cena["a"].read_bytes())
    # Ultimo frame do original, canal 4: fora do trecho que as irmas usam.
    bruto[-2] ^= 0x7F
    cena["a"].write_bytes(bruto)
    assert verify_conversion(cena["aaf"], saida).ok
    inicio_dados = len(bruto) - 9600 * 4 * 2
    # Frame 1010 do original, canal 2 (entrada 4): dentro do trecho do clip.
    bruto[inicio_dados + 1010 * 8 + 2] ^= 0x7F
    cena["a"].write_bytes(bruto)
    resultado = verify_conversion(cena["aaf"], saida)
    assert not resultado.ok
    assert resultado.expanded_lines == [LINHA_OK.replace("(0 differ)", "(1 differ)")]


def _exportar(saida, destino, fora=()):
    leitura = read_set({"input": str(saida)})
    assert leitura["ok"], leitura
    audio = [linha for linha in leitura["tracks"] if linha["kind"] == "audio"]
    escolhidas = [linha["audio_index"] for linha in audio if linha["name"] not in fora]
    pedido = {"input": str(saida), "output": str(destino), "tracks": escolhidas}
    return leitura, convert_to_aaf(pedido)


def test_tabela_do_export_lista_as_irmas_prontas_e_o_veredito_nao_difere(cena):
    saida, _ = importar(cena)
    destino = cena["tmp"] / "volta.aaf"
    leitura, resposta = _exportar(saida, destino)
    irmas = [linha for linha in leitura["tracks"] if f" {SEP} In " in linha["name"]]
    assert len(irmas) == 5
    assert all(linha["state"]["label"] == "ready" and linha["pick"] for linha in irmas)
    assert resposta["written"], resposta
    assert resposta["verdict"]["word"] == "Positions verified", resposta["verdict"]
    resultado = verify_conversion(destino, saida)
    assert resultado.ok, resultado.to_table()
    assert resultado.audio_compared == resultado.audio_expected == 5 + 7
    assert main(["verify", str(destino), str(saida)]) == 0


def test_irma_nao_exportada_nao_conta_como_faltando(cena):
    saida, _ = importar(cena)
    destino = cena["tmp"] / "parcial.aaf"
    fora = {f"Audio 1 {SEP} In 5 {SEP} LAPSOLDADO", f"Audio 2 {SEP} In 4 {SEP} A"}
    _, resposta = _exportar(saida, destino, fora)
    assert resposta["verdict"]["word"] == "Positions verified", resposta["verdict"]
    resultado = verify_conversion(destino, saida)
    assert resultado.ok, resultado.to_table()
    assert resultado.audio_compared == resultado.audio_expected == 5 + 4
    assert resultado.audio_missing == resultado.audio_extra == 0


# --- CLI ---


def test_import_com_media_link_e_recusado(cena, capsys):
    saida = cena["tmp"] / "out" / "link.als"
    argumentos = ["import", str(cena["aaf"]), str(saida), "--media", "link"]
    assert main([*argumentos, "--production-audio", str(cena["folder"])]) == 2
    assert "production audio is always split into mono files" in capsys.readouterr().err
    assert not saida.exists()


def test_production_audio_numa_pasta_de_som_direto_e_recusado(tmp_path, capsys):
    pasta = duas_diarias(tmp_path / "som")
    saida = tmp_path / "out" / "x.als"
    assert main(["import", str(pasta), str(saida), "--production-audio", str(pasta)]) == 2
    assert "--production-audio is refused for a production audio input" in capsys.readouterr().err
    assert not saida.exists()
    assert main(["inspect", str(pasta), "--production-audio", str(pasta)]) == 2


def test_cli_import_verify_e_inspect(cena, capsys):
    saida = cena["tmp"] / "out" / "cli.als"
    saida.parent.mkdir(parents=True, exist_ok=True)
    pasta = str(cena["folder"])
    assert main(["import", str(cena["aaf"]), str(saida), "--production-audio", pasta]) == 0
    capsys.readouterr()
    assert main(["verify", str(cena["aaf"]), str(saida)]) == 0
    assert "7 sibling clips checked against the production audio" in capsys.readouterr().out
    assert main(["inspect", str(cena["aaf"]), "--production-audio", pasta]) == 0
    dados = json.loads(capsys.readouterr().out)["production_audio"]
    takes = {take["original"]: take for take in dados["takes"]}
    a = takes[str(cena["a"])]
    assert a["owner"] == "Audio 1"
    assert a["inputs_used"] == ["In 3"] and a["inputs_added"] == ["In 4", "In 5", "In 6"]
    assert takes[str(cena["b"])]["inputs_added"] == []


def test_relatorio_do_import_tem_o_resumo(cena):
    _, report = importar(cena)
    texto = report.to_markdown()
    assert "3 takes matched to the production audio, 2 with inputs left out" in texto
    assert "5 sibling tracks added" in texto
