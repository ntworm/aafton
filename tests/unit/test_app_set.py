"""Tests of the Live to AAF half of the app back end."""

import gzip
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from aafton.aaf_read import read_aaf
from aafton.als_preflight import ScannedTrack
from aafton.als_read import read_session as read_session_do_set
from aafton.pipeline import ImportOptions, import_session
from aafton.report import ConversionIssue, Report
from aafton.sidecar import Anchor, sidecar_path_for, write_sidecar
from aafton.studio import app_convert, app_read, app_set
from aafton.timecode import TimecodeRate
from aafton.verify import VerifyResult, VerifyRow
from tests.aaf_factory import build_calibration_aaf, wav_bytes

TEMPLATE = Path("templates/live12.als")

# The words the verdict of the Live to AAF direction can say.
PALAVRAS = (
    "Stopped",
    "Not written",
    "Written with losses",
    "Written",
    "Differs",
    "Positions verified",
)


@pytest.fixture
def wav(tmp_path):
    caminho = tmp_path / "cena.wav"
    caminho.write_bytes(wav_bytes(48000 * 10))
    return caminho


@pytest.fixture
def aaf(tmp_path, wav):
    """An AAF with two audio tracks, built by the factory of the tests."""
    return build_calibration_aaf(
        tmp_path / "cena.aaf",
        [
            {
                "name": "DIALOGO 1",
                "clips": [{"path": str(wav), "start": 0, "offset": 0, "duration": 96000}],
            },
            {
                "name": "DIALOGO 2",
                "clips": [{"path": str(wav), "start": 0, "offset": 0, "duration": 96000}],
            },
        ],
        frames=96000,
        start_timecode_frames=90000,
    )


@pytest.fixture
def set_live(tmp_path, aaf):
    """The Live set that the import writes, with the sidecar that holds the timecode."""
    if not TEMPLATE.exists():
        pytest.skip("o template do Live nao esta no repositorio")
    destino = tmp_path / "cena.als"
    import_session(
        ImportOptions(input=str(aaf), output=str(destino), template=str(TEMPLATE)),
        Report(strict=False),
    )
    return destino


def _pedido(set_live, saida, tracks, overwrite=False, **opcoes):
    """The convert request of the Live to AAF direction."""
    opcoes.setdefault("mode", "strict")
    return {
        "direction": "to_aaf",
        "input": str(set_live),
        "output": str(saida),
        "tracks": list(tracks),
        "overwrite": overwrite,
        "options": opcoes,
    }


def _tracks_do_aaf(caminho):
    """The tracks of an AAF, read back by the reader of the product."""
    return read_aaf(caminho, Report(strict=False)).tracks


def test_a_leitura_descreve_o_set_e_nao_escreve_nada(set_live, tmp_path):
    antes = sorted(item.name for item in tmp_path.iterdir())

    resultado = app_set.read_set({"direction": "to_aaf", "input": str(set_live)})

    depois = sorted(item.name for item in tmp_path.iterdir())
    assert resultado["ok"] is True
    assert resultado["direction"] == "to_aaf"
    assert [linha["kind"] for linha in resultado["tracks"]] == ["audio", "audio", "return"]
    assert [linha["audio_index"] for linha in resultado["tracks"]] == [0, 1, None]
    assert [linha["name"] for linha in resultado["tracks"]][:2] == ["DIALOGO 1", "DIALOGO 2"]
    assert [linha["clips"] for linha in resultado["tracks"]] == [1, 1, 0]
    assert resultado["suggest"]["output"].endswith("cena.aaf")
    assert depois == antes


def test_a_leitura_mostra_o_que_pode_voltar_e_o_que_precisa_de_render(set_live):
    resultado = app_set.read_set({"direction": "to_aaf", "input": str(set_live)})

    linhas = resultado["tracks"]
    assert linhas[0]["state"] == {"st": "ok", "label": "ready"}
    assert linhas[0]["pick"] is True
    assert linhas[2]["state"] == {"st": "off", "label": "not exported"}
    assert linhas[2]["pick"] is None
    assert resultado["guide"] == []
    assert [fato["k"] for fato in resultado["facts"]] == [
        "Live",
        "Sample rate",
        "Timecode",
        "Duration",
        "Tracks",
        "Media",
    ]


def test_um_set_sem_o_sidecar_mas_com_o_locator_e_lido(set_live, tmp_path):
    """O motor recupera a ancoragem do locator: o app le o set como ele esta."""
    copia = tmp_path / "sem_sidecar.als"
    copia.write_bytes(set_live.read_bytes())

    resultado = app_set.read_set({"direction": "to_aaf", "input": str(copia)})

    assert resultado["ok"] is True
    assert [linha["name"] for linha in resultado["tracks"]][:2] == ["DIALOGO 1", "DIALOGO 2"]


def test_um_set_sem_o_sidecar_e_sem_o_locator_da_erro_de_ancoragem(tmp_path):
    caminho = _set_com_midi(tmp_path)
    copia = tmp_path / "sem_nada.als"
    copia.write_bytes(caminho.read_bytes())

    resultado = app_set.read_set({"direction": "to_aaf", "input": str(copia)})

    assert resultado["ok"] is False
    assert resultado["error"]["code"] == "no_anchor"
    assert ".aafton.json" in resultado["error"]["message"]


def test_um_arquivo_que_nao_existe_e_um_formato_desconhecido_dao_erro(tmp_path):
    sumiu_path = tmp_path / "nao_existe.als"
    sumiu = app_set.read_set({"direction": "to_aaf", "input": str(sumiu_path)})
    assert sumiu["ok"] is False
    assert sumiu["error"]["code"] == "not_found"
    assert sumiu["error"]["message"] == f"The file {sumiu_path} was not found."

    outro = tmp_path / "notas.txt"
    outro.write_text("oi", encoding="utf-8")
    fora = app_set.read_set({"direction": "to_aaf", "input": str(outro)})
    assert fora["ok"] is False
    assert fora["error"]["code"] == "unsupported"
    assert fora["error"]["message"] == "The format .txt is not a Live set."

    vazio = app_set.read_set({"direction": "to_aaf", "input": ""})
    assert vazio["ok"] is False
    assert vazio["error"]["code"] == "bad_request"
    assert vazio["error"]["message"] == "The set path is required."


def test_um_set_que_o_motor_nao_le_da_erro_de_leitura(set_live, tmp_path):
    quebrado = tmp_path / "quebrado.als"
    quebrado.write_bytes(b"isto nao e um set do Live")
    sidecar_path_for(quebrado).write_bytes(sidecar_path_for(set_live).read_bytes())

    resultado = app_set.read_set({"direction": "to_aaf", "input": str(quebrado)})

    assert resultado["ok"] is False
    assert resultado["error"]["code"] == "unreadable"


def _set_com_midi(tmp_path: Path, com_locator: bool = False) -> Path:
    """A set with a MIDI track of two clips and the sidecar that holds the timecode."""
    root = ET.Element("Ableton", Creator="Ableton Live 12.4.5b11")
    live = ET.SubElement(root, "LiveSet")
    tracks = ET.SubElement(live, "Tracks")
    midi = ET.SubElement(tracks, "MidiTrack")
    ET.SubElement(ET.SubElement(midi, "Name"), "EffectiveName", Value="MIDI 1")
    sequencer = ET.SubElement(ET.SubElement(midi, "DeviceChain"), "MainSequencer")
    events = ET.SubElement(
        ET.SubElement(ET.SubElement(sequencer, "ClipTimeable"), "ArrangerAutomation"),
        "Events",
    )
    for start, end in (("4", "8"), ("8", "12")):
        clip = ET.SubElement(events, "MidiClip")
        ET.SubElement(clip, "CurrentStart", Value=start)
        ET.SubElement(clip, "CurrentEnd", Value=end)
    if com_locator:
        locators = ET.SubElement(ET.SubElement(live, "Locators"), "Locators")
        locator = ET.SubElement(locators, "Locator")
        ET.SubElement(locator, "Name", Value="TC 01:00:00:00 @ 48000 @ 25")
    caminho = tmp_path / "com_midi.als"
    with gzip.open(caminho, "wb") as handle:
        handle.write(ET.tostring(root, encoding="utf-8"))
    write_sidecar(
        caminho,
        Anchor(
            live_origin_samples=0,
            session_start_samples=0,
            sample_rate=48000,
            timecode_rate=TimecodeRate(25, 1),
            bpm=60.0,
        ),
    )
    return caminho


def test_uma_track_midi_vai_para_o_guia_e_seus_spans_sao_os_beats_em_samples(tmp_path):
    caminho = _set_com_midi(tmp_path)

    resultado = app_set.read_set({"direction": "to_aaf", "input": str(caminho)})

    linhas = resultado["tracks"]
    assert [linha["kind"] for linha in linhas] == ["MIDI"]
    assert linhas[0]["state"] == {"st": "err", "label": "render first"}
    assert linhas[0]["pick"] is None
    assert linhas[0]["spans"] == [[192000, 384000], [384000, 576000]]
    assert resultado["guide"] == ["MIDI 1"]


def test_um_set_sem_o_sidecar_nao_da_spans_de_midi(tmp_path):
    """Sem o sidecar nao ha bpm nem origem: a track de MIDI fica sem spans."""
    caminho = _set_com_midi(tmp_path, com_locator=True)
    sidecar_path_for(caminho).unlink()

    resultado = app_set.read_set({"direction": "to_aaf", "input": str(caminho)})

    assert resultado["ok"] is True
    assert resultado["tracks"][0]["spans"] == []
    assert resultado["guide"] == ["MIDI 1"]


def test_a_conversao_com_uma_track_escreve_menos_tracks(set_live, tmp_path):
    parcial = tmp_path / "parcial.aaf"
    completo = tmp_path / "completo.aaf"

    primeiro = app_set.convert_to_aaf(_pedido(set_live, parcial, [0]))
    segundo = app_set.convert_to_aaf(_pedido(set_live, completo, [0, 1]))

    assert primeiro["written"] is True
    assert segundo["written"] is True
    assert parcial.exists()
    nomes_parcial = [track.name for track in _tracks_do_aaf(parcial)]
    nomes_completo = [track.name for track in _tracks_do_aaf(completo)]
    assert "DIALOGO 1" in nomes_parcial
    assert "DIALOGO 2" not in nomes_parcial
    assert "DIALOGO 1" in nomes_completo
    assert "DIALOGO 2" in nomes_completo
    assert app_set.LAST["to_aaf"]["output"] == completo.resolve()


def test_a_selecao_vazia_da_erro_de_tracks_e_nao_escreve_nada(set_live, tmp_path):
    saida = tmp_path / "saida.aaf"

    resultado = app_set.convert_to_aaf(_pedido(set_live, saida, []))

    assert resultado["ok"] is False
    assert resultado["error"]["code"] == "no_tracks"
    assert resultado["error"]["message"] == "Choose at least one track to send back."
    assert not saida.exists()


@pytest.mark.parametrize("tracks", [[0.5], [True], ["0"]])
def test_uma_selecao_que_nao_e_de_numeros_inteiros_da_erro_de_pedido(set_live, tmp_path, tracks):
    saida = tmp_path / "saida.aaf"

    resultado = app_set.convert_to_aaf(_pedido(set_live, saida, tracks))

    assert resultado["ok"] is False
    assert resultado["error"]["code"] == "bad_request"
    assert not saida.exists()


def test_a_selecao_e_conferida_antes_de_o_set_ser_lido(tmp_path):
    saida = tmp_path / "saida.aaf"

    resultado = app_set.convert_to_aaf(
        {
            "direction": "to_aaf",
            "input": str(tmp_path / "nao_existe.als"),
            "output": str(saida),
            "tracks": [0.5],
            "options": {"mode": "strict"},
        }
    )

    assert resultado["ok"] is False
    assert resultado["error"]["code"] == "bad_request"
    assert not saida.exists()


def test_uma_selecao_de_numeros_inteiros_ainda_exporta(set_live, tmp_path):
    saida = tmp_path / "saida.aaf"

    resultado = app_set.convert_to_aaf(_pedido(set_live, saida, [0]))

    assert resultado["ok"] is True
    assert resultado["written"] is True
    assert saida.exists()


def test_uma_conversao_que_falha_depois_de_escrever_nao_aponta_para_o_arquivo(
    set_live, tmp_path, monkeypatch
):
    saida = tmp_path / "saida.aaf"

    def escreve_e_falha(origem, destino, report, **opcoes):
        Path(destino).write_text("o aaf pela metade", encoding="utf-8")
        raise RuntimeError("o motor falhou")

    monkeypatch.setattr(app_set, "export_session", escreve_e_falha)

    resultado = app_set.convert_to_aaf(_pedido(set_live, saida, [0]))

    assert resultado["ok"] is False
    assert resultado["error"]["code"] == "failed"
    assert app_set.LAST["to_aaf"]["output"] is None
    assert app_set.LAST["to_aaf"]["report"] is not None
    assert saida.exists()


def test_a_segunda_conversao_pergunta_antes_de_sobrescrever(set_live, tmp_path):
    saida = tmp_path / "saida.aaf"

    primeiro = app_set.convert_to_aaf(_pedido(set_live, saida, [0]))
    assert primeiro["written"] is True

    segundo = app_set.convert_to_aaf(_pedido(set_live, saida, [0]))
    assert segundo["ok"] is False
    assert segundo["error"]["code"] == "exists"

    forcado = app_set.convert_to_aaf(_pedido(set_live, saida, [0], overwrite=True))
    assert forcado["ok"] is True
    assert forcado["written"] is True


def test_o_veredito_nunca_diz_verified_quando_a_verificacao_falhou(set_live, tmp_path, monkeypatch):
    def falha(*args, **kwargs):
        raise ValueError("o mapa do sidecar nao descreve os slots do arquivo novo")

    monkeypatch.setattr(app_set, "verify_conversion", falha)

    resultado = app_set.convert_to_aaf(_pedido(set_live, tmp_path / "saida.aaf", [0]))

    assert resultado["ok"] is True
    assert resultado["verdict"]["word"] in PALAVRAS
    assert resultado["verdict"]["word"] != "Positions verified"
    assert resultado["verdict"]["word"] == "Written"
    assert resultado["verdict"]["detail"][-1] == app_set.POSICAO
    codigos = [nota["code"] for nota in resultado["notes"]["main"]]
    assert "VERIFY_UNAVAILABLE" in codigos
    notas = [nota for nota in resultado["notes"]["main"] if nota["code"] == "VERIFY_UNAVAILABLE"]
    assert notas[0]["text"] == (
        "The position check compares a source AAF with the Live set made from it "
        "and cannot describe this export (o mapa do sidecar nao descreve os slots do arquivo novo)."
    )


def test_a_nota_da_verificacao_diz_que_o_formato_nao_tem_posicao(set_live, tmp_path):
    resultado = app_set.convert_to_aaf(_pedido(set_live, tmp_path / "saida.edl", [0]))

    assert resultado["ok"] is True
    notas = [nota for nota in resultado["notes"]["main"] if nota["code"] == "VERIFY_UNAVAILABLE"]
    assert notas[0]["text"] == "There is no position check for EDL files."


def _scanned(**kwargs) -> ScannedTrack:
    """A scanned track with the values the test needs."""
    base = {
        "index": 0,
        "kind": "audio",
        "name": "AUDIO 1",
        "audio_index": 0,
        "clips": 1,
        "devices": [],
        "frozen": False,
        "midi_spans": [],
    }
    base.update(kwargs)
    return ScannedTrack(**base)


def _issue(severidade: str, codigo: str = "TESTE") -> ConversionIssue:
    """An issue of the given severity."""
    return ConversionIssue(severidade, codigo, f"mensagem {codigo}", {})


def test_uma_track_midi_com_clips_precisa_de_render():
    estado = app_set.readiness(_scanned(kind="midi", audio_index=None, clips=2), [])

    assert estado["state"] == {"st": "err", "label": "render first"}
    assert estado["pick"] is None
    assert estado["reasons"] == [app_set.MIDI]


def test_uma_track_midi_vazia_fica_off():
    estado = app_set.readiness(_scanned(kind="midi", audio_index=None, clips=0), [])

    assert estado["state"] == {"st": "off", "label": "empty"}
    assert estado["pick"] is None
    assert estado["reasons"] == []


def test_um_grupo_e_um_retorno_nao_sao_exportados():
    for kind, motivo in (("group", app_set.GRUPO), ("return", app_set.RETORNO)):
        estado = app_set.readiness(_scanned(kind=kind, audio_index=None), [])

        assert estado["state"] == {"st": "off", "label": "not exported"}
        assert estado["pick"] is None
        assert estado["reasons"] == [motivo]


def test_uma_track_de_audio_sem_clips_fica_off():
    estado = app_set.readiness(_scanned(clips=0), [])

    assert estado["state"] == {"st": "off", "label": "empty"}
    assert estado["pick"] is False


def test_uma_track_de_audio_com_erro_fica_err():
    estado = app_set.readiness(_scanned(), [_issue("error")])

    assert estado["state"] == {"st": "err", "label": "1 error"}
    assert estado["pick"] is False


def test_uma_track_de_audio_com_devices_avisa_dos_efeitos():
    estado = app_set.readiness(_scanned(devices=["Eq8", "Compressor2"]), [])

    assert estado["state"] == {"st": "warn", "label": "effects not carried"}
    assert estado["pick"] is True
    assert estado["reasons"][0] == (
        "2 devices on the track: Eq8, Compressor2. The AAF carries the clips without them."
    )
    assert estado["reasons"][1] == "To keep the processing, freeze and flatten the track in Live."


def test_uma_track_de_audio_com_aviso_fica_warn():
    estado = app_set.readiness(_scanned(), [_issue("warning")])

    assert estado["state"] == {"st": "warn", "label": "1 note"}
    assert estado["pick"] is True


def test_uma_track_de_audio_pronta_fica_ok():
    estado = app_set.readiness(_scanned(), [])

    assert estado["state"] == {"st": "ok", "label": "ready"}
    assert estado["pick"] is True
    assert estado["reasons"] == []


def test_uma_track_gerada_pelo_aafton_nao_volta():
    estado = app_set.readiness(_scanned(), [], lida=False)

    assert estado["state"] == {"st": "off", "label": "not exported"}
    assert estado["pick"] is None
    assert estado["reasons"] == [app_set.GERADA]


def test_o_dispatch_le_e_converte_o_set_do_live(set_live, tmp_path):
    leitura = app_read.read({"direction": "to_aaf", "input": str(set_live)})
    assert leitura["ok"] is True
    assert leitura["direction"] == "to_aaf"

    saida = tmp_path / "saida.aaf"
    conversao = app_convert.convert(_pedido(set_live, saida, [0]))
    assert conversao["ok"] is True
    assert conversao["written"] is True
    assert saida.exists()


def test_um_aaf_nao_e_lido_nesta_direcao(aaf):
    resultado = app_set.read_set({"direction": "to_aaf", "input": str(aaf)})

    assert resultado["ok"] is False
    assert resultado["error"]["code"] == "unsupported"


def test_uma_conversao_permissiva_que_nao_escreve_diz_que_nao_escreveu(
    set_live, tmp_path, monkeypatch
):
    saida = tmp_path / "saida.txt"

    def falha(*args, **kwargs):
        raise AssertionError("a verificacao nao roda sem o arquivo escrito")

    monkeypatch.setattr(app_set, "verify_conversion", falha)
    resultado = app_set.convert_to_aaf(_pedido(set_live, saida, [0], mode="permissive"))

    assert resultado["ok"] is True
    assert resultado["written"] is False
    assert resultado["can_force"] is False
    assert not saida.exists()
    assert app_set.LAST["to_aaf"]["output"] is None
    assert resultado["verdict"]["word"] == "Not written"
    assert resultado["verdict"]["state"] == "fail"
    assert resultado["verdict"]["detail"][-1] == "The destination file was not written."
    relatorio = app_set.LAST["to_aaf"]["report"]
    erros = [issue for issue in relatorio.issues if issue.severity == "error"]
    assert erros
    assert resultado["verdict"]["detail"][0] == f"{erros[0].code}: {erros[0].message}"
    codigos = [nota["code"] for nota in resultado["notes"]["main"]]
    assert "FORMAT_UNKNOWN_SUFFIX" in codigos


def test_o_caminho_do_destino_e_absoluto(set_live, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    resultado = app_set.convert_to_aaf(_pedido(set_live, Path("saida.aaf"), [0]))

    assert resultado["output"] == str((tmp_path / "saida.aaf").resolve())
    assert Path(resultado["output"]).is_absolute()
    assert app_set.LAST["to_aaf"]["output"] == (tmp_path / "saida.aaf").resolve()


def test_o_arquivo_que_existe_vence_a_fonte_que_falta(set_live, tmp_path):
    saida = tmp_path / "saida.aaf"
    saida.write_text("o aaf de antes", encoding="utf-8")

    resultado = app_set.convert_to_aaf(
        {
            "direction": "to_aaf",
            "input": str(tmp_path / "nao_existe.als"),
            "output": str(saida),
            "tracks": [0],
            "options": {"mode": "strict"},
        }
    )

    assert resultado["ok"] is False
    assert resultado["error"]["code"] == "exists"


def test_uma_selecao_sem_track_da_erro_de_tracks_e_nao_escreve_nada(set_live, tmp_path):
    saida = tmp_path / "saida.aaf"

    resultado = app_set.convert_to_aaf(_pedido(set_live, saida, [99]))

    assert resultado["ok"] is False
    assert resultado["error"]["code"] == "no_tracks"
    assert resultado["error"]["message"] == "The selection names no audio track of the set."
    assert not saida.exists()
    assert app_set.LAST["to_aaf"]["output"] is None


def test_o_modo_estrito_para_no_erro_e_nao_escreve_nada(set_live, tmp_path, monkeypatch):
    def leitura_com_erro(caminho, report):
        session = read_session_do_set(caminho, report)
        report.error("ERRO_DE_LEITURA", "erro em DIALOGO 1", {"track": "DIALOGO 1"})
        return session

    monkeypatch.setattr("aafton.pipeline.read_session", leitura_com_erro)
    saida = tmp_path / "saida.aaf"

    resultado = app_set.convert_to_aaf(_pedido(set_live, saida, [0]))

    assert resultado["ok"] is True
    assert resultado["written"] is False
    assert resultado["can_force"] is True
    assert resultado["verdict"]["word"] == "Stopped"
    assert resultado["verdict"]["state"] == "fail"
    assert resultado["verdict"]["detail"][-1] == "The destination file was not written."
    assert not saida.exists()
    assert app_set.LAST["to_aaf"]["output"] is None


def test_o_modo_permissivo_escreve_o_arquivo_e_conta_as_perdas(set_live, tmp_path, monkeypatch):
    def leitura_com_erro(caminho, report):
        session = read_session_do_set(caminho, report)
        report.error("ERRO_DE_LEITURA", "erro em DIALOGO 1", {"track": "DIALOGO 1"})
        return session

    monkeypatch.setattr("aafton.pipeline.read_session", leitura_com_erro)
    saida = tmp_path / "saida.aaf"

    resultado = app_set.convert_to_aaf(_pedido(set_live, saida, [0], mode="permissive"))

    assert resultado["ok"] is True
    assert resultado["written"] is True
    assert resultado["verdict"]["word"] == "Written with losses"
    assert resultado["verdict"]["state"] == "warn"
    assert resultado["verdict"]["detail"][0] == "1 error reported"
    assert saida.exists()


def test_o_veredito_diz_differs_quando_a_verificacao_nao_passou(set_live, tmp_path, monkeypatch):
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
        audio_expected=1,
        audio_compared=0,
        audio_missing=1,
        errors=["o clip C12_T03 nao bate"],
    )
    monkeypatch.setattr(app_set, "verify_conversion", lambda *args, **kwargs: falso)

    resultado = app_set.convert_to_aaf(_pedido(set_live, tmp_path / "saida.aaf", [0]))

    assert resultado["verdict"]["word"] == "Differs"
    assert resultado["verdict"]["state"] == "fail"
    assert "0 of 1 audio clips compared" in resultado["verdict"]["detail"]
    assert "1 missing, 0 extra" in resultado["verdict"]["detail"]
    assert "o clip C12_T03 nao bate" in resultado["verdict"]["detail"]


def _set_sintetico(tmp_path: Path, nome: str | None, clips: int, com_current_end: bool) -> Path:
    """A set sintetica com uma track de audio, com ou sem nome."""
    audio = tmp_path / "cena.wav"
    audio.write_bytes(wav_bytes(48000))
    root = ET.Element("Ableton", Creator="Ableton Live 12.4.5b11")
    tracks = ET.SubElement(ET.SubElement(root, "LiveSet"), "Tracks")
    faixa = ET.SubElement(tracks, "AudioTrack")
    if nome is not None:
        ET.SubElement(ET.SubElement(faixa, "Name"), "EffectiveName", Value=nome)
    chain = ET.SubElement(faixa, "DeviceChain")
    sequencer = ET.SubElement(chain, "MainSequencer")
    sample = ET.SubElement(sequencer, "Sample")
    events = ET.SubElement(ET.SubElement(sample, "ArrangerAutomation"), "Events")
    for n in range(clips):
        clip = ET.SubElement(events, "AudioClip")
        ET.SubElement(clip, "CurrentStart", Value=str(n * 4))
        if com_current_end:
            ET.SubElement(clip, "CurrentEnd", Value=str(n * 4 + 4))
        loop = ET.SubElement(clip, "Loop")
        ET.SubElement(loop, "LoopStart", Value="0")
        ET.SubElement(clip, "Name", Value=f"C{n}")
        ref = ET.SubElement(ET.SubElement(clip, "SampleRef"), "FileRef")
        ET.SubElement(ref, "Path", Value=str(audio))
    caminho = tmp_path / "sintetico.als"
    with gzip.open(caminho, "wb") as handle:
        handle.write(ET.tostring(root, encoding="utf-8"))
    write_sidecar(
        caminho,
        Anchor(
            live_origin_samples=0,
            session_start_samples=0,
            sample_rate=48000,
            timecode_rate=TimecodeRate(25, 1),
            bpm=60.0,
        ),
    )
    return caminho


def test_a_linha_conta_os_clips_que_nao_cabem(tmp_path):
    caminho = _set_sintetico(tmp_path, "AUDIO 1", 350, True)

    resultado = app_set.read_set({"direction": "to_aaf", "input": str(caminho)})

    linha = resultado["tracks"][0]
    assert linha["clips"] == 350
    assert len(linha["clip_rows"]) == 300
    assert linha["clips_more"] == 50


def test_uma_track_sem_nome_mostra_o_tag_e_recebe_as_issues_do_motor(tmp_path):
    caminho = _set_sintetico(tmp_path, None, 1, False)

    resultado = app_set.read_set({"direction": "to_aaf", "input": str(caminho)})

    assert resultado["ok"] is True
    linha = resultado["tracks"][0]
    assert linha["name"] == "AudioTrack"
    assert linha["state"]["st"] == "err"
    assert linha["reasons"]
    codigos = [nota["code"] for nota in resultado["notes"]["main"]]
    assert "ALS_CLIP_UNREADABLE" in codigos
