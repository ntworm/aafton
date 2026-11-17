"""Tests of the AAF to Live half of the app back end."""

from pathlib import Path

import pytest

from aafton.ir import MediaAsset, SessionIR
from aafton.studio import app_convert, app_read
from aafton.timecode import TimecodeRate
from aafton.verify import VerifyResult, VerifyRow
from tests.aaf_factory import build_linked_aaf, wav_bytes

TEMPLATE = Path("templates/live12.als")

# The middle dot the sheet uses between the clips and the markers.
MIDDLE_DOT = chr(0x00B7)

# The space the sheet puts between the thousands of the sample rate.
NBSP = chr(0x00A0)

# A Pro Tools session text export beside the AAF: it carries one marker.
SESSION_TEXT = """SESSION NAME:\tCena 12
SAMPLE RATE:\t48000.000000
TIMECODE FORMAT:\t25 Frame
SESSION START TIMECODE:\t01:00:00:00.00
# OF AUDIO TRACKS:\t1
# OF AUDIO CLIPS:\t2
# OF AUDIO FILES:\t1

T R A C K   L I S T I N G

TRACK NAME:\tDIALOGO 1

    CHANNEL\tEVENT\tNAME\tSTART\tEND\tDURATION\tTIMESTAMP\tSTATE
    1\t1\tClip A\t01:00:00:00.00\t01:00:02:00.00\t00:00:02:00.00\t00:00:00.00\t01:00:00:00.00

M A R K E R S   L I S T I N G

#\tMarker Number\tMarker Location\tMarker Time Reference\tMarker Units\tMarker Name\tTrack Name\tTrack Type\tComments
1\tM1\t48000\tSamples\tMARCA UM\tDIALOGO 1\tAudio\tComentario do marcador
"""


@pytest.fixture
def wav(tmp_path):
    caminho = tmp_path / "cena12_tk3.wav"
    caminho.write_bytes(wav_bytes(48000 * 120))
    return caminho


@pytest.fixture
def aaf(tmp_path, wav):
    return build_linked_aaf(
        tmp_path / "cena12.aaf",
        [(0, 96000, 24000), (192000, 96000, 0)],
        wav,
    )


def _pedido(aaf, saida, **opcoes):
    """The convert request of the AAF to Live direction."""
    opcoes.setdefault("mode", "strict")
    return {
        "direction": "to_live",
        "input": str(aaf),
        "output": str(saida),
        "options": opcoes,
    }


def test_le_o_AAF_ligado_descreve_a_sessao_e_nao_escreve_nada(aaf, tmp_path):
    antes = sorted(item.name for item in tmp_path.iterdir())
    resultado = app_read.read_source({"direction": "to_live", "input": str(aaf)})
    depois = sorted(item.name for item in tmp_path.iterdir())

    assert resultado["ok"] is True
    assert resultado["direction"] == "to_live"
    assert resultado["source"]["format"] == "aaf"
    assert resultado["source"]["name"] == "cena12"
    assert len(resultado["tracks"]) == 1
    assert [fato["k"] for fato in resultado["facts"]] == [
        "Program",
        "Sample rate",
        "Timecode",
        "Duration",
        "Tracks",
        "Media",
    ]
    fatos = {fato["k"]: fato for fato in resultado["facts"]}
    assert fatos["Program"]["v"] == "PyAAF"
    assert fatos["Sample rate"]["v"] == f"48{NBSP}000 Hz"
    assert fatos["Timecode"]["v"] == "25 fps"
    assert fatos["Timecode"]["sub"] == "starts 01:00:00:00"
    assert fatos["Duration"]["v"] == "00:00:06:00"
    assert fatos["Tracks"]["v"] == "1"
    assert fatos["Tracks"]["sub"] == "2 clips"
    assert fatos["Media"]["v"] == "1 of 1"
    assert fatos["Media"]["sub"] == "linked, all found"
    assert resultado["media"]["total"] == 1
    assert resultado["media"]["found"] == 1
    assert resultado["media"]["missing"] == 0
    assert resultado["media"]["missing_text"] == ""
    assert depois == antes


def test_o_arquivo_de_audio_que_falta_conta_como_faltando(tmp_path, wav):
    aaf = build_linked_aaf(tmp_path / "cena12.aaf", [(0, 96000, 24000)], wav)
    wav.unlink()

    resultado = app_read.read_source({"direction": "to_live", "input": str(aaf)})

    assert resultado["ok"] is True
    assert resultado["media"]["missing"] == 1
    assert resultado["media"]["found"] == 0
    assert resultado["media"]["missing_text"] == "1 of 1 audio files were not found."
    media = [fato for fato in resultado["facts"] if fato["k"] == "Media"][0]
    assert media["st"] == "err"


def test_arquivo_que_nao_existe_e_formato_desconhecido_dao_erro(tmp_path):
    sumiu_path = tmp_path / "nao_existe.aaf"
    sumiu = app_read.read_source({"direction": "to_live", "input": str(sumiu_path)})
    assert sumiu["ok"] is False
    assert sumiu["error"]["code"] == "not_found"
    assert sumiu["error"]["message"] == (
        f"{sumiu_path.name} is no longer at {sumiu_path.parent}. "
        "It may have been moved, renamed or deleted. Choose the file again."
    )

    outro = tmp_path / "notas.txt"
    outro.write_text("oi", encoding="utf-8")
    fora = app_read.read_source({"direction": "to_live", "input": str(outro)})
    assert fora["ok"] is False
    assert fora["error"]["code"] == "unsupported"
    assert fora["error"]["message"] == "The format .txt is not read by this direction."


def test_a_leitura_sem_caminho_da_erro_de_pedido():
    resposta = app_read.read_source({"direction": "to_live", "input": ""})
    assert resposta["ok"] is False
    assert resposta["error"]["code"] == "bad_request"
    assert resposta["error"]["message"] == "The source path is required."


@pytest.mark.skipif(not TEMPLATE.exists(), reason="o template do Live nao esta no repositorio")
def test_a_conversao_escreve_o_set_e_verifica_o_resultado(aaf, tmp_path):
    saida = tmp_path / "saida.als"

    resultado = app_convert.convert_to_live(_pedido(aaf, saida))

    assert resultado["ok"] is True
    assert resultado["written"] is True
    assert resultado["can_force"] is False
    assert resultado["verdict"]["word"] == "Positions verified"
    assert resultado["verdict"]["state"] == "ok"
    assert "2 of 2 audio clips compared" in resultado["verdict"]["detail"]
    assert "largest deviation 0 samples" in resultado["verdict"]["detail"]
    assert saida.exists()
    assert app_convert.LAST["to_live"]["output"] == saida.resolve()


@pytest.mark.skipif(not TEMPLATE.exists(), reason="o template do Live nao esta no repositorio")
def test_a_segunda_conversao_pergunta_antes_de_sobrescrever(aaf, tmp_path):
    saida = tmp_path / "saida.als"
    pedido = _pedido(aaf, saida)

    primeiro = app_convert.convert_to_live(pedido)
    assert primeiro["written"] is True

    segundo = app_convert.convert_to_live(pedido)
    assert segundo["ok"] is False
    assert segundo["error"]["code"] == "exists"
    assert segundo["error"]["message"] == f"The file {saida.resolve()} already exists."

    forcado = app_convert.convert_to_live({**pedido, "overwrite": True})
    assert forcado["ok"] is True
    assert forcado["written"] is True


@pytest.mark.skipif(not TEMPLATE.exists(), reason="o template do Live nao esta no repositorio")
def test_o_modo_estrito_para_no_erro_e_nao_escreve_nada(tmp_path, wav):
    aaf = build_linked_aaf(tmp_path / "cena12.aaf", [(0, 96000, 24000)], wav)
    wav.unlink()
    saida = tmp_path / "saida.als"

    resultado = app_convert.convert_to_live(_pedido(aaf, saida))

    assert resultado["ok"] is True
    assert resultado["written"] is False
    assert resultado["can_force"] is True
    assert resultado["verdict"]["word"] == "Stopped"
    assert resultado["verdict"]["state"] == "fail"
    assert resultado["verdict"]["detail"][-1] == "The destination file was not written."
    assert not saida.exists()
    assert resultado["output"] == str(saida.resolve())


@pytest.mark.skipif(not TEMPLATE.exists(), reason="o template do Live nao esta no repositorio")
def test_o_modo_estrito_guarda_o_relatorio_mesmo_sem_escrever_arquivo(tmp_path, wav):
    aaf = build_linked_aaf(tmp_path / "cena12.aaf", [(0, 96000, 24000)], wav)
    wav.unlink()
    saida = tmp_path / "saida.als"

    resultado = app_convert.convert_to_live(_pedido(aaf, saida))

    assert resultado["written"] is False
    assert not saida.exists()
    assert app_convert.LAST["to_live"]["output"] is None
    assert app_convert.LAST["to_live"]["report"] is not None


@pytest.mark.skipif(not TEMPLATE.exists(), reason="o template do Live nao esta no repositorio")
def test_o_modo_permissivo_escreve_o_set_e_conta_as_perdas(tmp_path, wav):
    aaf = build_linked_aaf(tmp_path / "cena12.aaf", [(0, 96000, 24000)], wav)
    wav.unlink()
    saida = tmp_path / "saida.als"

    resultado = app_convert.convert_to_live(_pedido(aaf, saida, mode="permissive"))

    assert resultado["ok"] is True
    assert resultado["written"] is True
    assert resultado["verdict"]["word"] == "Written with losses"
    assert resultado["verdict"]["state"] == "warn"
    assert "1 error reported" in resultado["verdict"]["detail"]
    assert saida.exists()


def test_uma_conversao_que_nao_deixa_o_destino_no_disco_diz_que_nao_escreveu(
    aaf, tmp_path, monkeypatch
):
    """O motor devolve sem excecao e o destino nao existe: o veredito diz so isso."""

    def nao_escreve(pedido, report):
        report.error("SEM_ESCRITA", "o motor nao deixou o arquivo no disco")

    def falha(*args, **kwargs):
        raise AssertionError("a verificacao nao roda sem o arquivo escrito")

    monkeypatch.setattr(app_convert, "import_session", nao_escreve)
    monkeypatch.setattr(app_convert, "verify_conversion", falha)
    saida = tmp_path / "saida.als"

    resultado = app_convert.convert_to_live(_pedido(aaf, saida, mode="permissive"))

    assert resultado["ok"] is True
    assert resultado["written"] is False
    assert resultado["can_force"] is False
    assert resultado["verdict"]["word"] == "Not written"
    assert resultado["verdict"]["state"] == "fail"
    assert resultado["verdict"]["detail"] == [
        "SEM_ESCRITA: o motor nao deixou o arquivo no disco",
        "The destination file was not written.",
    ]
    assert not saida.exists()
    assert app_convert.LAST["to_live"]["output"] is None
    assert app_convert.LAST["to_live"]["report"] is not None


def test_o_tempo_invalido_e_erro_de_pedido(aaf, tmp_path):
    resultado = app_convert.convert_to_live(_pedido(aaf, tmp_path / "saida.als", bpm="abc"))

    assert resultado["ok"] is False
    assert resultado["error"]["code"] == "bad_request"
    assert resultado["error"]["message"] == "The tempo 'abc' is not a number."


def test_a_conversao_sem_caminhos_diz_que_eles_sao_necessarios():
    resultado = app_convert.convert_to_live({"direction": "to_live", "input": "", "output": ""})

    assert resultado["ok"] is False
    assert resultado["error"]["code"] == "bad_request"
    assert resultado["error"]["message"] == "The source and the destination are required."


def test_a_leitura_somente_serve_as_direcoes_do_contrato():
    """Sem direcao, ou com uma direcao que nao existe, a leitura pergunta."""
    for payload in ({}, {"direction": "to_outro"}):
        resultado = app_read.read(payload)

        assert resultado["ok"] is False
        assert resultado["error"]["code"] == "bad_request"


def test_a_conversao_somente_serve_as_direcoes_do_contrato():
    """Sem direcao, ou com uma direcao que nao existe, a conversao pergunta."""
    for payload in ({}, {"direction": "to_outro"}):
        resultado = app_convert.convert(payload)

        assert resultado["ok"] is False
        assert resultado["error"]["code"] == "bad_request"


def test_o_estado_do_destino_compara_o_mtime_em_nanossegundos_e_o_tamanho(tmp_path):
    """O estado do destino carrega o mtime em nanossegundos e o tamanho."""
    arquivo = tmp_path / "saida.als"
    arquivo.write_text("o set", encoding="utf-8")
    dados = arquivo.stat()

    estado = app_convert.estado_do_destino(arquivo)

    assert estado == (True, dados.st_mtime_ns, dados.st_size)


def _fato(resultado, nome):
    """The fact of the sheet with the given name."""
    return [fato for fato in resultado["facts"] if fato["k"] == nome][0]


def test_a_duracao_e_o_tempo_entre_o_primeiro_e_o_ultimo_clip(aaf):
    resultado = app_read.read_source({"direction": "to_live", "input": str(aaf)})

    # O primeiro clip comeca em 0 e o ultimo termina em 288000 samples:
    # seis segundos a 25 fps, contados do zero.
    assert _fato(resultado, "Duration")["v"] == "00:00:06:00"


def test_o_fato_de_tracks_mostra_os_clips_e_os_marcadores(aaf, tmp_path, wav):
    sem_marcadores = app_read.read_source({"direction": "to_live", "input": str(aaf)})
    assert _fato(sem_marcadores, "Tracks")["sub"] == "2 clips"

    com_marcador = build_linked_aaf(
        tmp_path / "com_marcador.aaf",
        [(0, 96000, 0), (192000, 96000, 0)],
        wav,
        with_marker_at=48000,
    )
    resultado = app_read.read_source({"direction": "to_live", "input": str(com_marcador)})
    assert _fato(resultado, "Tracks")["sub"] == f"2 clips {MIDDLE_DOT} 1 markers"


def test_a_leitura_aplica_o_texto_de_sessao_e_mostra_os_marcadores(tmp_path, wav):
    aaf = build_linked_aaf(
        tmp_path / "cena12.aaf",
        [(0, 96000, 24000), (192000, 96000, 0)],
        wav,
    )
    (tmp_path / "cena12.txt").write_text(SESSION_TEXT, encoding="utf-8")

    antes = sorted(item.name for item in tmp_path.iterdir())
    resultado = app_read.read_source({"direction": "to_live", "input": str(aaf)})
    depois = sorted(item.name for item in tmp_path.iterdir())

    assert resultado["ok"] is True
    assert _fato(resultado, "Tracks")["sub"] == f"2 clips {MIDDLE_DOT} 1 markers"
    codigos = [nota["code"] for nota in resultado["notes"]["info"]]
    assert "MARKER_COMMENT_IN_ANNOTATION" in codigos
    assert resultado["suggest"]["session_text"] == str(tmp_path / "cena12.txt")
    assert depois == antes


def test_a_fonte_que_nao_existe_da_erro_de_not_found(tmp_path):
    fonte = tmp_path / "nao_existe.aaf"
    resultado = app_convert.convert_to_live(
        {
            "direction": "to_live",
            "input": str(fonte),
            "output": str(tmp_path / "saida.als"),
            "options": {"mode": "strict"},
        }
    )

    assert resultado["ok"] is False
    assert resultado["error"]["code"] == "not_found"
    assert resultado["error"]["message"] == (
        f"{fonte.name} is no longer at {fonte.parent}. "
        "It may have been moved, renamed or deleted. Choose the file again."
    )
    assert not (tmp_path / "saida.als").exists()


def test_um_template_que_nao_existe_da_erro_de_not_found(aaf, tmp_path):
    resultado = app_convert.convert_to_live(
        _pedido(aaf, tmp_path / "saida.als", template=str(tmp_path / "sem_template.als"))
    )

    assert resultado["ok"] is False
    assert resultado["error"]["code"] == "not_found"
    assert not (tmp_path / "saida.als").exists()


@pytest.mark.skipif(not TEMPLATE.exists(), reason="o template do Live nao esta no repositorio")
def test_o_caminho_do_destino_e_absoluto(aaf, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    resultado = app_convert.convert_to_live(_pedido(aaf, Path("saida.als")))

    assert resultado["output"] == str((tmp_path / "saida.als").resolve())
    assert Path(resultado["output"]).is_absolute()
    assert app_convert.LAST["to_live"]["output"] == (tmp_path / "saida.als").resolve()


def test_uma_conversao_que_falha_nao_aponta_para_o_arquivo_antigo(aaf, tmp_path, monkeypatch):
    saida = tmp_path / "saida.als"
    saida.write_text("o set de antes", encoding="utf-8")

    def falha(*args, **kwargs):
        raise RuntimeError("o motor falhou")

    monkeypatch.setattr(app_convert, "import_session", falha)

    resultado = app_convert.convert_to_live(
        {
            "direction": "to_live",
            "input": str(aaf),
            "output": str(saida),
            "overwrite": True,
            "options": {"mode": "strict"},
        }
    )

    assert resultado["ok"] is False
    assert resultado["error"]["code"] == "failed"
    assert app_convert.LAST["to_live"]["output"] is None
    assert saida.read_text(encoding="utf-8") == "o set de antes"


def test_uma_conversao_que_falha_depois_de_escrever_nao_aponta_para_o_arquivo(
    aaf, tmp_path, monkeypatch
):
    saida = tmp_path / "saida.als"

    def escreve_e_falha(pedido, report):
        Path(pedido.output).write_text("o set pela metade", encoding="utf-8")
        raise RuntimeError("o motor falhou")

    monkeypatch.setattr(app_convert, "import_session", escreve_e_falha)

    resultado = app_convert.convert_to_live(_pedido(aaf, saida))

    assert resultado["ok"] is False
    assert resultado["error"]["code"] == "failed"
    assert app_convert.LAST["to_live"]["output"] is None
    assert app_convert.LAST["to_live"]["report"] is not None
    assert saida.exists()


def test_o_veredito_diz_not_verified_quando_a_verificacao_levantou(aaf, tmp_path, monkeypatch):
    def falha(*args, **kwargs):
        raise ValueError("o mapa do sidecar nao descreve os slots do arquivo novo")

    monkeypatch.setattr(app_convert, "verify_conversion", falha)

    resultado = app_convert.convert_to_live(_pedido(aaf, tmp_path / "saida.als"))

    assert resultado["ok"] is True
    assert resultado["written"] is True
    assert resultado["verdict"]["word"] == "Not verified"
    assert resultado["verdict"]["state"] == "warn"
    assert resultado["verdict"]["detail"][0] == "The set was written."
    assert "o mapa do sidecar" in resultado["verdict"]["detail"][1]


def test_o_veredito_diz_differs_quando_a_verificacao_nao_passou(aaf, tmp_path, monkeypatch):
    linha = VerifyRow(
        track="DIALOGO 1",
        clip="C12_T03",
        aaf_in="01:00:00:00",
        aaf_out="01:00:02:00",
        als_in="01:00:00:00",
        als_out="01:00:01:23",
        delta_samples=48,
        delta_frames=1.0,
        status="out of tolerance",
        delta_end_samples=48,
    )
    falso = VerifyResult(
        rows=[linha],
        audio_expected=2,
        audio_compared=1,
        audio_missing=1,
        errors=["o clip C12_T03 nao bate"],
    )
    monkeypatch.setattr(app_convert, "verify_conversion", lambda *args, **kwargs: falso)

    resultado = app_convert.convert_to_live(_pedido(aaf, tmp_path / "saida.als"))

    assert resultado["verdict"]["word"] == "Differs"
    assert resultado["verdict"]["state"] == "fail"
    assert "1 of 2 audio clips compared" in resultado["verdict"]["detail"]
    assert "largest deviation 48 samples" in resultado["verdict"]["detail"]
    assert "1 missing, 0 extra" in resultado["verdict"]["detail"]
    assert "o clip C12_T03 nao bate" in resultado["verdict"]["detail"]


def test_um_set_do_live_nao_e_lido_nesta_direcao(tmp_path):
    caminho = tmp_path / "cena.als"
    caminho.write_text("isto nao e um set do Live", encoding="utf-8")

    resultado = app_read.read_source({"direction": "to_live", "input": str(caminho)})

    assert resultado["ok"] is False
    assert resultado["error"]["code"] == "unsupported"


def test_o_ltc_so_e_verdade_quando_o_json_diz_verdade(aaf, tmp_path, monkeypatch):
    pedidos = []

    def guarda(pedido, report):
        pedidos.append(pedido)

    monkeypatch.setattr(app_convert, "import_session", guarda)

    app_convert.convert_to_live(_pedido(aaf, tmp_path / "saida.als", ltc="false"))
    app_convert.convert_to_live(_pedido(aaf, tmp_path / "outra.als", ltc=True))

    assert pedidos[0].ltc is False
    assert pedidos[1].ltc is True


def test_o_mxf_que_a_conversao_extrai_nao_vira_erro_na_leitura(tmp_path, monkeypatch):
    """O MXF esta no disco: a conversao o extrai, a leitura nao reclama dele."""
    mxf = tmp_path / "cena.mxf"
    mxf.write_bytes(b"conteudo de mxf")
    sessao = SessionIR(
        name="cena",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=0,
    )
    sessao.media.append(
        MediaAsset(id="m0", filename="cena.mxf", status="unresolved", declared_location=str(mxf))
    )

    def leitura(caminho, report, **kwargs):
        return sessao

    monkeypatch.setattr(app_read, "read_origin", leitura)
    aaf = tmp_path / "cena12.aaf"
    aaf.write_bytes(b"isto nao e um aaf de verdade")

    resultado = app_read.read_source({"direction": "to_live", "input": str(aaf)})

    assert resultado["ok"] is True
    codigos = [nota["code"] for nota in resultado["notes"]["main"]]
    assert "MEDIA_MXF_UNSUPPORTED" not in codigos
    assert resultado["media"]["total"] == 1
    assert resultado["media"]["found"] == 1
    assert resultado["media"]["missing"] == 0
    media = [fato for fato in resultado["facts"] if fato["k"] == "Media"][0]
    assert media["v"] == "1 of 1"
    assert media["sub"] == "linked, all found"
