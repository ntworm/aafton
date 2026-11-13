"""Ressalvas da revisao das perdas que mudam o codigo.

Cada teste nomeia a ressalva que ele guarda (R6, R7, R8, R10) e arma o
cenario que a revela: o corpus do oraculo de refatoracao, a track master que
o Live 11 nomeia de MasterTrack, o aviso do envelope de clip e o texto das
perdas que somem na exportacao.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from aafton.als_read import read_session
from aafton.als_read_clips import track_name
from aafton.als_xml import load_als, save_als
from aafton.report import Report
from scripts import refactor_oracle as ro
from tests.unit.test_perdas_automacao import (
    _alvo_do_mixer,
    _colar_clip_envelope,
    _colar_envelope,
    _criar_envelope,
    _envelope_da_track,
    _escrever_set,
    _no_do_set,
)

TEMPLATE = Path("templates/live12.als")

pytestmark = pytest.mark.skipif(not TEMPLATE.exists(), reason="templates/live12.als nao existe")


def _corpus_com_aaf(tmp_path: Path) -> Path:
    """Pasta de corpus com um AAF dentro."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "final24.aaf").write_bytes(b"")
    return corpus


def test_corpus_sem_arquivo_aaf_para_com_codigo_2(monkeypatch, tmp_path, capsys):
    """R6: uma pasta de corpus sem nenhum *.aaf para o oraculo com codigo 2."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "leia-me.txt").write_text("nenhum aaf aqui", encoding="utf-8")
    monkeypatch.setenv(ro.CORPUS_ENV, str(corpus))

    assert ro._prepara_corpus([]) == 2
    saida = capsys.readouterr().out
    assert "erro" in saida
    assert ro.CORPUS_ENV in saida


def test_sem_corpus_que_sobrou_no_ambiente_nao_tira_o_corpus(monkeypatch, tmp_path):
    """R6: um AAFTON_SEM_CORPUS que sobrou no ambiente nao tira o corpus dos casos."""
    corpus = _corpus_com_aaf(tmp_path)
    monkeypatch.setenv(ro.CORPUS_ENV, str(corpus))
    monkeypatch.setenv(ro.SEM_CORPUS_ENV, "1")

    assert ro._prepara_corpus([]) == 0
    nomes = [nome for nome, _ in ro.entradas()]
    assert "corpus-final24" in nomes


def _master_track(root: ET.Element) -> ET.Element:
    """O MainTrack do Live 12 vira MasterTrack, o nome que o Live 11 usa."""
    main = _no_do_set(root, "./LiveSet/MainTrack")
    main.tag = "MasterTrack"
    return main


def test_automacao_do_master_track_e_reportada(tmp_path):
    """R7: o MasterTrack do Live 11 nao vira slot: a automacao dele se perde."""
    als_path = _escrever_set(tmp_path)
    root = load_als(als_path)
    master = _master_track(root)
    _colar_envelope(_envelope_da_track(root), master, _alvo_do_mixer(master, "Volume"))
    save_als(root, als_path)

    report = Report(strict=False)
    read_session(als_path, report)

    achados = [i for i in report.issues if i.code == "ALS_AUTOMATION_UNSUPPORTED"]
    assert len(achados) == 1
    assert achados[0].context["kind"] == "MasterTrack"
    assert achados[0].context["target"] == "Volume"
    assert achados[0].context["track"] in achados[0].message


def test_automacao_de_tempo_do_master_track_usa_o_codigo_do_tempo(tmp_path):
    """R7: a automacao de tempo do MasterTrack tem o codigo do tempo."""
    als_path = _escrever_set(tmp_path)
    root = load_als(als_path)
    master = _master_track(root)
    envelopes = master.find("AutomationEnvelopes/Envelopes")
    envelope = _criar_envelope(_alvo_do_mixer(master, "Tempo"), [(0.0, 120.0), (4.0, 140.0)])
    envelope.set("Id", str(len(envelopes)))
    envelopes.append(envelope)
    save_als(root, als_path)

    report = Report(strict=False)
    read_session(als_path, report)

    codigos = [i.code for i in report.issues]
    assert "ALS_TEMPO_AUTOMATION_UNSUPPORTED" in codigos
    assert "ALS_AUTOMATION_UNSUPPORTED" not in codigos


def _tracks_com_clips(root: ET.Element) -> list[tuple[ET.Element, ET.Element]]:
    """Cada track de audio do set e o clip do arrangement dela."""
    achados = []
    for node in root.findall("./LiveSet/Tracks/AudioTrack"):
        clip = node.find("DeviceChain/MainSequencer/Sample/ArrangerAutomation/Events/AudioClip")
        assert clip is not None, "a track de audio do set nao tem clip"
        achados.append((node, clip))
    return achados


def test_o_aviso_do_clip_diz_de_qual_track_ele_e(tmp_path):
    """R8: dois clips com o mesmo nome: cada aviso diz de qual track ele e."""
    als_path = _escrever_set(tmp_path)
    root = load_als(als_path)
    tracks = _tracks_com_clips(root)
    nomes = [track_name(node) for node, _ in tracks]
    assert all(nomes)
    assert nomes[0] != nomes[1]
    for _node, clip in tracks:
        _colar_clip_envelope(clip)
    save_als(root, als_path)

    report = Report(strict=False)
    read_session(als_path, report)

    achados = [i for i in report.issues if i.code == "ALS_AUTOMATION_UNSUPPORTED"]
    assert len(achados) == 2
    for nome, achado in zip(nomes, achados):
        assert achado.context.get("track") == nome
        assert nome in achado.message
    assert achados[0].message != achados[1].message


def test_as_perdas_avisam_do_arquivo_exportado(tmp_path):
    """R10: o aviso da perda fala do arquivo exportado, e nao do AAF."""
    als_path = _escrever_set(tmp_path)
    root = load_als(als_path)
    grupo = _no_do_set(root, "./LiveSet/Tracks/GroupTrack")
    _colar_envelope(_envelope_da_track(root), grupo, _alvo_do_mixer(grupo, "Volume"))
    grupo.find("DeviceChain/Mixer/Volume/Manual").set("Value", "0.5")
    clip = _no_do_set(
        root,
        "./LiveSet/Tracks/AudioTrack/DeviceChain/MainSequencer/Sample"
        "/ArrangerAutomation/Events/AudioClip",
    )
    _colar_clip_envelope(clip)
    save_als(root, als_path)

    report = Report(strict=False)
    session = read_session(als_path, report)

    assert len(session.unsupported_export_features) == 3
    for mensagem in session.unsupported_export_features:
        assert "has no representation in the exported file" in mensagem
        assert "in the AAF" not in mensagem
