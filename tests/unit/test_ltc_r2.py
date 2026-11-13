"""Testes da segunda rodada da revisao do LTC (fase 2).

Cobre os achados da revisao independente da fase 2:

- F9: a track gerada nao repete o nome de uma track que ja existe na sessao;
- F11: taxa de sessao sem LTC equivalente nao aborta o import;
- F12: os parametros do WAV de LTC sao validados antes de escrever o arquivo.
"""

import json
from pathlib import Path

import pytest

from aafton.audio_audit import normalize_name
from aafton.cli import main
from aafton.ir import SessionIR, TrackIR
from aafton.ltc import write_ltc_wav
from aafton.ltc_track import add_ltc_track
from aafton.report import Report
from aafton.sidecar import sidecar_path_for
from aafton.timecode import TimecodeRate
from tests.aaf_factory import build_linked_aaf, wav_bytes

TAXA_25 = TimecodeRate(25, 1)
TAXA_50 = TimecodeRate(50, 1)
TAXA_5994 = TimecodeRate(60000, 1001)
TAXA_60 = TimecodeRate(60, 1)
SR = 48000
TEMPLATE = Path("templates/live12.als")


def _sessao(rate=TAXA_25, nomes=("DIALOGO 1",)):
    """SessionIR minima, com as tracks nomeadas que o teste pedir."""
    tracks = [
        TrackIR(id=f"t{i}", source_index=i, name=nome, kind="audio", channel_count=1)
        for i, nome in enumerate(nomes)
    ]
    return SessionIR(
        name="sessao",
        sample_rate=SR,
        timecode_rate=rate,
        start_timecode_samples=0,
        tracks=tracks,
    )


def _codigo_cli(argumentos):
    """Codigo de saida do CLI, aceitando o SystemExit que o argparse levanta."""
    try:
        return main(argumentos)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1


def _importar(aaf, saida, relatorio=None, opcoes=()):
    """Roda o import com o template do projeto e devolve o codigo de saida."""
    argumentos = ["import", str(aaf), str(saida), "--template", str(TEMPLATE), "--media", "link"]
    if relatorio is not None:
        argumentos += ["--report", str(relatorio)]
    return _codigo_cli(argumentos + list(opcoes))


def _sidecar(saida):
    """Le o sidecar JSON que o import gravou ao lado do .als."""
    return json.loads(sidecar_path_for(saida).read_text(encoding="utf-8"))


def _aaf_com_ltc(tmp_path, nome_da_track="LTC", fps=25):
    """AAF de uma track de audio com o nome pedido e o WAV que ela usa."""
    wav = tmp_path / "cena.wav"
    wav.write_bytes(wav_bytes(SR * 10))
    return build_linked_aaf(
        tmp_path / "cena.aaf", [(0, 96000, 24000)], wav, track_name=nome_da_track, fps=fps
    )


# F9: nome da track gerada


def test_track_gerada_leva_o_nome_ltc_quando_nada_ocupa_o_nome(tmp_path):
    sessao = _sessao()
    track = add_ltc_track(sessao, 0, tmp_path, Report())
    assert track is not None
    assert track.name == "LTC"
    assert track.raw == {"generated": "ltc"}


def test_track_gerada_muda_de_nome_quando_a_sessao_ja_tem_ltc(tmp_path):
    sessao = _sessao(nomes=("LTC",))
    track = add_ltc_track(sessao, 0, tmp_path, Report())
    assert track is not None
    assert track.name == "LTC (aafton)"
    assert track.raw == {"generated": "ltc"}


@pytest.mark.skipif(not TEMPLATE.exists(), reason="template do projeto ausente")
def test_import_com_ltc_nao_repete_o_nome_da_track_no_sidecar(tmp_path):
    aaf = _aaf_com_ltc(tmp_path)
    saida = tmp_path / "saida.als"
    assert _importar(aaf, saida, opcoes=["--ltc"]) == 0

    dados = _sidecar(saida)
    nomes = [entrada["track"] for entrada in dados["clip_ranges"]]
    assert len(nomes) == len(set(nomes))
    assert "LTC" in nomes
    assert "LTC (aafton)" in nomes

    # O audit-audio --release-mask recusa um nome repetido depois de normalizar.
    normalizados = [normalize_name(nome) for nome in nomes]
    assert len(normalizados) == len(set(normalizados))

    gerados = [b for b in dados["track_bindings"] if b.get("generated") == "ltc"]
    assert len(gerados) == 1
    assert gerados[0]["track_name"] == "LTC (aafton)"


@pytest.mark.parametrize("nome", ["ltc", "Ltc", "LTC "])
def test_track_gerada_muda_de_nome_com_nome_normalizado(nome, tmp_path):
    """O audit normaliza o nome: 'Ltc' e 'LTC ' colidem com 'LTC'."""
    sessao = _sessao(nomes=(nome,))
    track = add_ltc_track(sessao, 0, tmp_path, Report())
    assert track is not None
    assert track.name == "LTC (aafton)"
    assert normalize_name(track.name) != normalize_name(nome)


def test_track_gerada_nao_repete_nome_quando_ja_existe_o_aafton(tmp_path):
    """Com o 'LTC (aafton)' ja na sessao, o nome gerado continua livre."""
    sessao = _sessao(nomes=("LTC", "LTC (aafton)"))
    track = add_ltc_track(sessao, 0, tmp_path, Report())
    assert track is not None
    nomes = [normalize_name(t.name) for t in sessao.tracks]
    assert len(nomes) == len(set(nomes))


# F10r: o aviso da track gerada


def test_aviso_da_track_de_ltc_manda_rotear_para_uma_saida_dedicada(tmp_path):
    """A track sai pela saida Main: o aviso diz para rotear antes de tocar."""
    sessao = _sessao()
    relatorio = Report()
    add_ltc_track(sessao, 0, tmp_path, relatorio)
    avisos = [i for i in relatorio.issues if i.code == "LTC_TRACK_WRITTEN"]
    assert len(avisos) == 1
    mensagem = avisos[0].message
    assert "Main" in mensagem
    assert "route it" in mensagem
    assert "before playing" in mensagem


# F11: taxa de sessao sem LTC equivalente


@pytest.mark.parametrize("rate", [TAXA_50, TAXA_5994, TAXA_60])
def test_taxa_sem_ltc_avisa_e_nao_cria_track(rate, tmp_path):
    sessao = _sessao(rate=rate)
    relatorio = Report()
    track = add_ltc_track(sessao, 0, tmp_path, relatorio)
    assert track is None
    assert [t.name for t in sessao.tracks] == ["DIALOGO 1"]

    avisos = [i for i in relatorio.issues if i.code == "LTC_RATE_UNSUPPORTED"]
    assert len(avisos) == 1
    assert avisos[0].severity == "warning"
    assert f"{rate.num}/{rate.den}" in avisos[0].message
    assert not list(tmp_path.iterdir())


@pytest.mark.skipif(not TEMPLATE.exists(), reason="template do projeto ausente")
def test_import_em_taxa_sem_ltc_continua_e_avisa(tmp_path):
    aaf = _aaf_com_ltc(tmp_path, nome_da_track="DIALOGO 1", fps=60)
    saida = tmp_path / "saida.als"
    relatorio = tmp_path / "relatorio.json"
    assert _importar(aaf, saida, relatorio=relatorio, opcoes=["--ltc"]) == 0

    dados = json.loads(relatorio.read_text(encoding="utf-8"))
    avisos = [i for i in dados["issues"] if i["code"] == "LTC_RATE_UNSUPPORTED"]
    assert len(avisos) == 1
    assert avisos[0]["severity"] == "warning"
    assert "60/1" in avisos[0]["message"]

    bindings = _sidecar(saida)["track_bindings"]
    assert all(b.get("generated") != "ltc" for b in bindings)


# F12: validacao antes de escrever


def test_write_ltc_wav_recusa_nivel_acima_de_zero(tmp_path):
    saida = tmp_path / "ltc.wav"
    with pytest.raises(ValueError, match="the LTC level must be <= 0 dBFS"):
        write_ltc_wav(saida, "00:00:00:00", TAXA_25, 25, SR, 0.5)
    assert not saida.exists()


@pytest.mark.parametrize("sample_rate", [7999, 384001])
def test_write_ltc_wav_recusa_taxa_de_audio_fora_da_faixa(tmp_path, sample_rate):
    saida = tmp_path / "ltc.wav"
    with pytest.raises(ValueError, match="between 8000 and 384000"):
        write_ltc_wav(saida, "00:00:00:00", TAXA_25, 25, sample_rate)
    assert not saida.exists()


def test_write_ltc_wav_recusa_arquivo_maior_que_o_limite_do_riff(tmp_path):
    saida = tmp_path / "ltc.wav"
    # Dez horas de LTC: dentro das 24 horas, mas o WAV passa do RIFF.
    with pytest.raises(ValueError, match="4294967296"):
        write_ltc_wav(saida, "00:00:00:00", TAXA_25, 25 * 3600 * 10, SR)
    assert not saida.exists()


def test_write_ltc_wav_recusa_nivel_nan(tmp_path):
    """Um nivel nan nao e um nivel: nada pode ser escrito."""
    saida = tmp_path / "ltc.wav"
    with pytest.raises(ValueError, match="the LTC level must be <= 0 dBFS"):
        write_ltc_wav(saida, "00:00:00:00", TAXA_25, 25, SR, float("nan"))
    assert not saida.exists()


def test_write_ltc_wav_recusa_ltc_que_passa_de_24_horas(tmp_path):
    """Um LTC que passa de 24:00:00:00 e recusado antes do arquivo."""
    saida = tmp_path / "ltc.wav"
    with pytest.raises(ValueError, match="24:00:00:00"):
        write_ltc_wav(saida, "23:59:00:00", TAXA_25, 25 * 3600, SR)
    assert not saida.exists()


def test_write_ltc_wav_recusa_rotulo_com_24_horas(tmp_path):
    """Um rotulo de 24 horas ou mais ja e o fim da linha."""
    saida = tmp_path / "ltc.wav"
    with pytest.raises(ValueError, match="24:00:00:00"):
        write_ltc_wav(saida, "24:00:00:00", TAXA_25, 25, SR)
    assert not saida.exists()


@pytest.mark.parametrize(
    "extra,mensagem",
    [
        (["--level-dbfs", "0.5"], "the LTC level must be <= 0 dBFS"),
        (["--level-dbfs", "nan"], "the LTC level must be <= 0 dBFS"),
        (["--sample-rate", "7999"], "between 8000 and 384000"),
        (["--sample-rate", "384001"], "between 8000 and 384000"),
        (["--duration", "10:00:00:00"], "4294967296"),
    ],
)
def test_cli_ltc_recusa_valor_invalido_sem_escrever_arquivo(tmp_path, extra, mensagem, capsys):
    saida = tmp_path / "ltc.wav"
    argumentos = ["ltc", "--start", "00:00:00:00", "--rate", "25", "--duration", "00:00:10:00"]
    argumentos.append(str(saida))
    assert _codigo_cli(argumentos + extra) == 2
    assert mensagem in capsys.readouterr().err
    assert not saida.exists()


def test_cli_ltc_recusa_ltc_que_passa_de_24_horas(tmp_path, capsys):
    """A linha de comando tambem recusa o LTC que passa do limite."""
    saida = tmp_path / "ltc.wav"
    argumentos = [
        "ltc",
        "--start",
        "24:00:00:00",
        "--rate",
        "25",
        "--duration",
        "00:00:10:00",
        str(saida),
    ]
    assert _codigo_cli(argumentos) == 2
    assert "24:00:00:00" in capsys.readouterr().err
    assert not saida.exists()


def test_ajuda_do_ltc_diz_que_a_duracao_e_um_rotulo_de_timecode(capsys):
    with pytest.raises(SystemExit):
        main(["ltc", "--help"])
    ajuda = " ".join(capsys.readouterr().out.split())
    assert "duration as a TC label at the chosen rate" in ajuda
    assert "drop frame" in ajuda
