"""Pasta do som direto na aba AAF -> Live: leitura com as irmas, conversao e o campo."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from aafton.studio import app_convert, app_expandir, app_read
from aafton.studio.page import load_page
from tests.unit.test_expandir import montar_cena

SEP = "·"
IRMAS_1 = [f"Audio 1 {SEP} In 4 {SEP} CCM8L", f"Audio 1 {SEP} In 5 {SEP} LAPSOLDADO"]
IRMAS_1.append(f"Audio 1 {SEP} In 6 {SEP} LAPJS")
IRMAS_2 = [f"Audio 2 {SEP} In 4 {SEP} A", f"Audio 2 {SEP} In 5 {SEP} B"]


@pytest.fixture
def cena(tmp_path):
    return montar_cena(tmp_path)


def _ler(cena, pasta=None):
    payload = {
        "direction": "to_live",
        "input": str(cena["aaf"]),
        "audio_dir": str(cena["tmp"] / "AAF Media"),
    }
    if pasta is not None:
        payload["production_audio"] = str(pasta)
    return app_read.read(payload)


def _fato(resultado, chave):
    return next((f for f in resultado["facts"] if f["k"] == chave), None)


def test_sem_a_pasta_a_leitura_sai_como_hoje(cena):
    resultado = _ler(cena)
    assert resultado["ok"] is True
    assert _fato(resultado, "Production audio") is None
    assert [t["name"] for t in resultado["tracks"]] == ["Audio 1", "Audio 2", "Audio 3", "Audio 4"]
    assert all("sibling" not in t for t in resultado["tracks"])
    assert [t["index"] for t in resultado["tracks"]] == [0, 1, 2, 3]


def test_com_a_pasta_o_fato_conta_os_takes_e_os_que_ganham_canais(cena):
    resultado = _ler(cena, cena["folder"])
    assert resultado["ok"] is True
    fato = _fato(resultado, "Production audio")
    assert (fato["v"], fato["sub"]) == ("3 takes found", "2 with channels to add")
    assert _fato(resultado, "Tracks")["v"] == "4"


def test_as_irmas_seguem_a_dona_recuadas_e_a_dona_fica_no_grupo(cena):
    linhas = _ler(cena, cena["folder"])["tracks"]
    nomes = [t["name"] for t in linhas]
    assert nomes == ["Audio 1", *IRMAS_1, "Audio 2", *IRMAS_2, "Audio 3", "Audio 4"]
    por_nome = {t["name"]: t for t in linhas}
    for nome in IRMAS_1 + IRMAS_2:
        irma = por_nome[nome]
        assert irma["sibling"] is True
        assert irma["kind"] == "mono"
        assert irma["state"] == {"st": "ok", "label": "From recorder"}
    assert [por_nome[n]["clips"] for n in IRMAS_2] == [2, 2]
    assert len(por_nome[IRMAS_2[0]]["clip_rows"]) == 2
    assert por_nome[IRMAS_2[0]]["spans"]
    sem_pasta = {t["name"]: t["state"] for t in _ler(cena)["tracks"]}
    for dona in ("Audio 1", "Audio 2"):
        # A dona deste AAF tem uma nota de quadro: o estado leva a nota e o grupo.
        antes = sem_pasta[dona]
        esperado = f"Ready {SEP} group" if antes["st"] == "ok" else f"{antes['label']} {SEP} group"
        assert por_nome[dona]["state"]["label"] == esperado
        assert por_nome[dona]["sibling"] is False
    assert por_nome["Audio 3"]["state"] == sem_pasta["Audio 3"]
    editor = ("Audio 1", "Audio 2", "Audio 3", "Audio 4")
    assert [por_nome[n]["index"] for n in editor] == [0, 1, 2, 3]
    indices = [t["index"] for t in linhas]
    assert len(set(indices)) == len(indices)


def test_as_notas_da_expansao_vao_para_as_notas_da_leitura(cena):
    notas = _ler(cena, cena["folder"])["notes"]
    codigos = [n["code"] for n in notas["main"] + notas["info"]]
    assert "EXPANSION_SUMMARY" in codigos
    assert "EXPANSION_NOTE" in codigos


def test_a_pasta_que_nao_existe_e_o_erro_da_leitura(cena):
    resultado = _ler(cena, cena["tmp"] / "nao-existe")
    assert resultado["ok"] is False
    assert "does not exist" in resultado["error"]["message"]


def test_a_pasta_sem_wav_legivel_e_o_erro_da_leitura(cena):
    vazia = cena["tmp"] / "vazia"
    vazia.mkdir()
    (vazia / "x.wav").write_bytes(b"nao e um wav")
    resultado = _ler(cena, vazia)
    assert resultado["ok"] is False
    assert resultado["error"]["message"] == f"no readable WAV file in {vazia}"


def test_converter_com_a_pasta_diz_os_clips_do_gravador(cena):
    saida = cena["tmp"] / "out" / "app.als"
    resposta = app_convert.convert(
        {
            "direction": "to_live",
            "input": str(cena["aaf"]),
            "output": str(saida),
            "audio_dir": str(cena["tmp"] / "AAF Media"),
            "production_audio": str(cena["folder"]),
            "options": {"mode": "permissive"},
        }
    )
    assert resposta["ok"] is True and resposta["written"] is True
    detalhe = resposta["verdict"]["detail"]
    assert "7 clips added from the recorder, equal to their channel" in detalhe
    assert not [linha for linha in detalhe if linha.startswith("Siblings: ")]


def test_converter_sem_a_pasta_chama_o_import_como_hoje(cena, monkeypatch):
    chamadas = []

    def guarda(pedido, report):
        chamadas.append(pedido)

    monkeypatch.setattr(app_convert, "import_session", guarda)
    app_convert.convert(
        {"direction": "to_live", "input": str(cena["aaf"]), "output": str(cena["tmp"] / "s.als")}
    )
    assert len(chamadas) == 1


def test_dona_pronta_le_ready_no_grupo():
    estado = app_expandir._estado_da_dona({"st": "ok", "label": "ok"})
    assert estado == {"st": "ok", "label": f"Ready {SEP} group"}
    nota = app_expandir._estado_da_dona({"st": "warn", "label": "1 note"})
    assert nota == {"st": "warn", "label": f"1 note {SEP} group"}


def test_linha_do_veredito_quando_clips_diferem():
    falha = "2 sibling clips differ from the production audio: X #1, X #2"
    linha = "Siblings: 9 sibling clips checked against the production audio (2 differ)"
    medida = {"checked": 9, "differ": 2, "text": falha}
    medido = SimpleNamespace(expanded_lines=[linha], sibling_clips=medida)
    assert app_expandir.verdict_lines(medido) == [falha]
    sem_medida = SimpleNamespace(expanded_lines=["Siblings: not exported in this AAF"])
    assert app_expandir.verdict_lines(sem_medida) == ["Siblings: not exported in this AAF"]


def test_o_campo_da_pasta_esta_na_pagina():
    pagina = load_page()
    assert "Production audio folder" in pagina
    assert '<span class="opt-mark">optional</span>' in pagina
    assert 'data-f="production_audio"' in pagina
    assert 'placeholder="Folder of the recorder files"' in pagina
    assert 'data-act="browse-production-audio"' in pagina
    assert 'data-f="siblings-foot"' in pagina
    assert "Indented tracks come from the recorder files" in pagina
