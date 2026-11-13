"""Perdas silenciosas na leitura do .als: automacao e mix sem representacao.

O AAF exportado tem um slot por AudioTrack do Live. GroupTrack, ReturnTrack e
MainTrack nao viram slot, e o envelope de automacao de um clip tambem nao: por
isso o volume, o pan, o mute e a automacao deles sumiam do export sem aviso.
Os testes montam o set com o proprio escritor do produto, editam o XML no
formato que o Live escreve e exigem que a leitura nomeie a perda.
"""

from __future__ import annotations

import copy
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from aafton.als_read import read_session
from aafton.als_write import WriteOptions, write_session
from aafton.als_xml import load_als, save_als
from aafton.cli import main
from aafton.ir import ClipIR, MediaAsset, SessionIR, TrackIR
from aafton.report import Report
from aafton.timecode import TimecodeRate
from tests.aaf_factory import wav_bytes

TEMPLATE = Path("templates/live12.als")
UMA_HORA = 172800000
SR = 48000
GANHO_6_DB = 10 ** (-6 / 20)

pytestmark = pytest.mark.skipif(not TEMPLATE.exists(), reason="templates/live12.als nao existe")

# As tracks do Live que o AAF exportado nao vira slot.
TRACKS_SEM_SLOT = [
    ("GroupTrack", "./LiveSet/Tracks/GroupTrack"),
    ("ReturnTrack", "./LiveSet/Tracks/ReturnTrack"),
    ("MainTrack", "./LiveSet/MainTrack"),
]


def _sessao(tmp_path: Path) -> SessionIR:
    """Duas tracks que o agrupamento por prefixo junta no mesmo grupo."""
    wav = tmp_path / "cena.wav"
    wav.write_bytes(wav_bytes(SR * 5))
    asset = MediaAsset(id="m1", filename="cena.wav", status="resolved", resolved_path=str(wav))
    tracks = []
    for indice, nome in enumerate(["SFX 1", "SFX 2"]):
        track = TrackIR(
            id=f"t{indice}", source_index=indice, name=nome, kind="audio", channel_count=1
        )
        track.clips.append(
            ClipIR(
                id=f"c{indice}",
                track_id=track.id,
                channel_index=0,
                event_index=0,
                name="cena.wav",
                media_id="m1",
                timeline_start_samples=UMA_HORA,
                timeline_end_samples=UMA_HORA + 2 * SR,
                source_offset_samples=0,
            )
        )
        tracks.append(track)
    # A primeira track ganha automacao: e ela que faz o escritor deixar no set
    # o AutomationEnvelope no formato que o Live le.
    tracks[0].gain_points = [(UMA_HORA, 1.0), (UMA_HORA + SR, 0.5)]
    return SessionIR(
        name="perdas",
        sample_rate=SR,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=UMA_HORA,
        media=[asset],
        tracks=tracks,
    )


def _escrever_set(tmp_path: Path) -> Path:
    """Escreve o set com o proprio escritor, ja com o grupo por prefixo."""
    destino = tmp_path / "set.als"
    write_session(
        _sessao(tmp_path),
        TEMPLATE,
        destino,
        WriteOptions(sample_rate=SR, groups="prefix"),
        Report(strict=False),
    )
    return destino


def _no_do_set(root: ET.Element, caminho: str) -> ET.Element:
    """Um no do set escrito, pelo caminho."""
    node = root.find(caminho)
    assert node is not None, f"o set escrito nao tem {caminho}"
    return node


def _alvo_do_mixer(node: ET.Element, parametro: str) -> str:
    """Id do AutomationTarget de um parametro do mixer."""
    alvo = node.find(f"DeviceChain/Mixer/{parametro}/AutomationTarget")
    assert alvo is not None, f"o mixer nao tem o parametro {parametro}"
    return alvo.get("Id", "")


def _envelope_da_track(root: ET.Element) -> ET.Element:
    """O envelope de automacao que o escritor deixou na AudioTrack."""
    return _no_do_set(
        root, "./LiveSet/Tracks/AudioTrack/AutomationEnvelopes/Envelopes/AutomationEnvelope"
    )


def _colar_envelope(origem: ET.Element, destino: ET.Element, alvo_id: str) -> None:
    """Copia o envelope de uma track para outra, apontando para o alvo dado."""
    envelopes = destino.find("AutomationEnvelopes/Envelopes")
    copia = copy.deepcopy(origem)
    copia.find("EnvelopeTarget/PointeeId").set("Value", alvo_id)
    copia.set("Id", str(len(envelopes)))
    envelopes.append(copia)


def _criar_envelope(
    alvo_id: str,
    pontos: list[tuple[float, float]],
    tag: str = "AutomationEnvelope",
) -> ET.Element:
    """Envelope de automacao no formato que o Live escreve."""
    envelope = ET.Element(tag)
    ponteiro = ET.SubElement(ET.SubElement(envelope, "EnvelopeTarget"), "PointeeId")
    ponteiro.set("Value", alvo_id)
    eventos = ET.SubElement(ET.SubElement(envelope, "Automation"), "Events")
    for indice, (beat, valor) in enumerate(pontos):
        evento = ET.SubElement(eventos, "FloatEvent")
        evento.set("Id", str(indice))
        evento.set("Time", repr(beat))
        evento.set("Value", repr(valor))
    return envelope


def _colar_clip_envelope(clip: ET.Element) -> None:
    """Coloca um ClipEnvelope no clip, no formato que o Live escreve."""
    envelope = _criar_envelope("9001", [(0.0, 0.5)], tag="ClipEnvelope")
    clip.find("Envelopes/Envelopes").append(envelope)


@pytest.mark.parametrize("tipo,caminho", TRACKS_SEM_SLOT)
def test_automacao_de_track_sem_slot_no_aaf_e_reportada(tmp_path, tipo, caminho):
    """GroupTrack, ReturnTrack e MainTrack nao viram slot: a automacao se perde."""
    als_path = _escrever_set(tmp_path)
    root = load_als(als_path)
    node = _no_do_set(root, caminho)
    _colar_envelope(_envelope_da_track(root), node, _alvo_do_mixer(node, "Volume"))
    save_als(root, als_path)

    report = Report(strict=False)
    session = read_session(als_path, report)

    achados = [i for i in report.issues if i.code == "ALS_AUTOMATION_UNSUPPORTED"]
    assert len(achados) == 1
    assert achados[0].context["kind"] == tipo
    assert achados[0].context["target"] == "Volume"
    assert achados[0].context["track"] in achados[0].message
    assert len(session.unsupported_export_features) == 1


def test_automacao_de_tempo_da_main_track_usa_so_o_codigo_do_tempo(tmp_path):
    """O tempo tem regra propria: ele nao vira ALS_AUTOMATION_UNSUPPORTED."""
    als_path = _escrever_set(tmp_path)
    root = load_als(als_path)
    main_track = _no_do_set(root, "./LiveSet/MainTrack")
    envelopes = main_track.find("AutomationEnvelopes/Envelopes")
    envelope = _criar_envelope(_alvo_do_mixer(main_track, "Tempo"), [(0.0, 120.0), (4.0, 140.0)])
    envelope.set("Id", str(len(envelopes)))
    envelopes.append(envelope)
    save_als(root, als_path)

    report = Report(strict=False)
    read_session(als_path, report)

    codigos = [i.code for i in report.issues]
    assert "ALS_TEMPO_AUTOMATION_UNSUPPORTED" in codigos
    assert "ALS_AUTOMATION_UNSUPPORTED" not in codigos


def test_automacao_de_clip_envelope_e_reportada(tmp_path):
    """O envelope de automacao de um clip tambem nao tem representacao no AAF."""
    als_path = _escrever_set(tmp_path)
    root = load_als(als_path)
    clip = _no_do_set(
        root,
        "./LiveSet/Tracks/AudioTrack/DeviceChain/MainSequencer/Sample"
        "/ArrangerAutomation/Events/AudioClip",
    )
    _colar_clip_envelope(clip)
    save_als(root, als_path)

    report = Report(strict=False)
    session = read_session(als_path, report)

    achados = [i for i in report.issues if i.code == "ALS_AUTOMATION_UNSUPPORTED"]
    assert len(achados) == 1
    assert achados[0].context["clip"] == "cena.wav"
    assert "ClipEnvelope" in achados[0].message
    assert len(session.unsupported_export_features) == 1


@pytest.mark.parametrize(
    "parametro,valor,chave",
    [
        ("Volume/Manual", repr(GANHO_6_DB), "volume"),
        ("Speaker/Manual", "false", "mute"),
    ],
)
def test_mix_do_grupo_fora_do_neutro_da_erro(tmp_path, parametro, valor, chave):
    """O volume, o pan e o mute do grupo nao tem onde entrar no AAF."""
    als_path = _escrever_set(tmp_path)
    root = load_als(als_path)
    grupo = _no_do_set(root, "./LiveSet/Tracks/GroupTrack")
    grupo.find(f"DeviceChain/Mixer/{parametro}").set("Value", valor)
    save_als(root, als_path)

    report = Report(strict=False)
    session = read_session(als_path, report)

    achados = [i for i in report.issues if i.code == "ALS_GROUP_MIX_UNSUPPORTED"]
    assert len(achados) == 1
    assert achados[0].context["group"] == "SFX"
    assert chave in achados[0].context
    assert len(session.unsupported_export_features) == 1


def test_export_aafton_do_set_com_automacao_recusa_no_estrito(tmp_path, capsys):
    """O export estrito para; o permissivo entrega o arquivo com o aviso."""
    als_path = _escrever_set(tmp_path)
    root = load_als(als_path)
    grupo = _no_do_set(root, "./LiveSet/Tracks/GroupTrack")
    _colar_envelope(_envelope_da_track(root), grupo, _alvo_do_mixer(grupo, "Volume"))
    save_als(root, als_path)

    codigo = main(["export", str(als_path), str(tmp_path / "estrito.aaf")])
    assert codigo == 1
    assert "WRITE_SESSION_FEATURE_UNSUPPORTED" in capsys.readouterr().err
    assert not (tmp_path / "estrito.aaf").exists()

    destino = tmp_path / "permissivo.aaf"
    relatorio = tmp_path / "permissivo.json"
    main(["export", str(als_path), str(destino), "--permissive", "--report", str(relatorio)])
    assert destino.exists()
    dados = json.loads(relatorio.read_text(encoding="utf-8"))
    codigos = [issue["code"] for issue in dados["issues"]]
    assert "ALS_AUTOMATION_UNSUPPORTED" in codigos
    assert "WRITE_SESSION_FEATURE_UNSUPPORTED" in codigos


def test_read_session_do_set_escrito_nao_da_codigo_novo(tmp_path):
    """O set que o proprio produto escreve nao pode acusar perda do produto."""
    als_path = _escrever_set(tmp_path)

    report = Report(strict=False)
    session = read_session(als_path, report)

    codigos = [i.code for i in report.issues]
    assert "ALS_AUTOMATION_UNSUPPORTED" not in codigos
    assert "ALS_GROUP_MIX_UNSUPPORTED" not in codigos
    assert session.unsupported_export_features == []
