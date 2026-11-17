"""CLI do som direto: import, opcoes recusadas, inspect redirecionado, verify e export."""

import io
import json
import sys

import pytest

from aafton.cli import main
from aafton.sidecar import PRODUCTION_SET_MESSAGE
from tests.unit.test_som_direto_leitor import HORA, RATE, duas_diarias, mixpre


@pytest.fixture
def pasta(tmp_path):
    return duas_diarias(tmp_path / "som")


def _importar(pasta, saida, *extras) -> int:
    saida.parent.mkdir(parents=True, exist_ok=True)
    return main(["import", str(pasta), str(saida), *extras])


@pytest.mark.parametrize(
    ("opcoes", "esperado"),
    [
        (["--media", "link"], "production audio is always split into mono files"),
        (["--ltc"], "--ltc"),
        (["--video", "filme.mov"], "--video"),
        (["--video-start", "01:00:00:00"], "--video-start"),
        (["--session-text", "texto.txt"], "--session-text"),
    ],
)
def test_opcoes_recusadas_no_som_direto(pasta, tmp_path, capsys, opcoes, esperado):
    saida = tmp_path / "out" / "x.als"
    assert _importar(pasta, saida, *opcoes) == 2
    erro = capsys.readouterr().err
    assert esperado in erro
    assert "production audio" in erro
    assert not saida.exists()


def test_groups_e_ignorado_com_nota(pasta, tmp_path, capsys):
    assert _importar(pasta, tmp_path / "out" / "g.als", "--groups", "prefix") == 0
    assert "--groups is ignored for production audio" in capsys.readouterr().out


def test_live_origin_e_ignorado_com_nota(pasta, tmp_path, capsys):
    saida = tmp_path / "out" / "o.als"
    assert _importar(pasta, saida, "--live-origin", "10:00:00:00") == 0
    nota = "each block of the set has its own start; --live-origin ignored"
    assert nota in capsys.readouterr().out
    assert main(["verify", str(pasta), str(saida)]) == 0


def test_set_dentro_da_pasta_do_gravador_e_recusado_na_cli(pasta, capsys):
    saida = pasta / "dentro.als"
    assert main(["import", str(pasta), str(saida)]) == 2
    assert "The Live set cannot go inside the recorder folder." in capsys.readouterr().err
    assert not saida.exists()
    assert not (pasta / "dentro Project").exists()


def test_import_circled_only_e_verify_pela_cli(pasta, tmp_path, capsys):
    saida = tmp_path / "out" / "circled.als"
    assert _importar(pasta, saida, "--circled-only") == 0
    relatorio = capsys.readouterr().out
    assert "2 takes left out because they are not circled" in relatorio
    assert main(["verify", str(pasta), str(saida)]) == 0
    tabela = capsys.readouterr().out
    assert "3 of 3 audio clips compared" in tabela
    assert "largest deviation 0 samples" in tabela


def test_export_de_set_de_som_direto_e_recusado(pasta, tmp_path, capsys):
    saida = tmp_path / "out" / "e.als"
    assert _importar(pasta, saida) == 0
    capsys.readouterr()
    assert main(["export", str(saida), str(tmp_path / "volta.aaf")]) == 2
    assert PRODUCTION_SET_MESSAGE in capsys.readouterr().err
    assert not (tmp_path / "volta.aaf").exists()


def test_inspect_mostra_os_dois_tempos(pasta, capsys):
    assert main(["inspect", str(pasta)]) == 0
    dados = json.loads(capsys.readouterr().out)
    assert dados["layout"] == "production_blocks"
    clip = dados["tracks"][0]["clips"][0]
    assert clip["set"] == {"samples": 10 * RATE, "label": "00:00:10:00"}
    assert clip["timecode"] == {"samples": 10 * HORA, "label": "10:00:00:00"}


def test_inspect_com_nome_acentuado_em_stdout_redirecionado(tmp_path, monkeypatch):
    nome = "cena_ação"
    mixpre(tmp_path / "dia" / f"{nome}.wav", HORA, "2025-01-02", "1", "1", [(3, "São")])
    bruto = io.BytesIO()
    redirecionado = io.TextIOWrapper(bruto, encoding="cp437")
    monkeypatch.setattr(sys, "stdout", redirecionado)
    assert main(["inspect", str(tmp_path / "dia")]) == 0
    redirecionado.flush()
    texto = bruto.getvalue().decode("utf-8")
    assert nome in texto
    assert json.loads(texto)["tracks"][0]["name"] == "In 3 · São"


def test_set_de_wav_solto_dentro_da_diaria_e_recusado(tmp_path):
    from aafton.pipeline import check_production_output

    dia = tmp_path / "gravador" / "05-05-25"
    dia.mkdir(parents=True)
    wav = dia / "take.wav"
    wav.write_bytes(b"")
    with pytest.raises(ValueError, match="recorder folder"):
        check_production_output(wav, dia / "take Project" / "take.als")
    check_production_output(wav, tmp_path / "fora Project" / "fora.als")


def test_set_da_fase_2_dentro_da_pasta_do_gravador_e_recusado(tmp_path):
    from aafton.pipeline import ImportOptions, _check_expansion_options

    gravador = tmp_path / "gravador"
    gravador.mkdir()
    dentro = ImportOptions(input="x.aaf", output=str(gravador / "s Project" / "s.als"))
    with pytest.raises(ValueError, match="recorder folder"):
        _check_expansion_options(dentro, str(gravador))
    fora = ImportOptions(input="x.aaf", output=str(tmp_path / "s Project" / "s.als"))
    _check_expansion_options(fora, str(gravador))
