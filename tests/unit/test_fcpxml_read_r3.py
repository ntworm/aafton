"""Testes da terceira revisao do leitor de FCPXML: corte, filhos e lanes.

Os documentos foram escritos a mao a partir das sondas da revisao: corte em L
e corte em J na storyline principal, corte rolado, musica presa a um title e a
um gerador, filhos de um item de audio, storyline dentro de storyline, clip
conectado a item de storyline, corte com conectado, clip desligado e audio
conectado com fade.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aafton.cli import main
from aafton.formats.fcpxml_read import read_fcpxml
from aafton.media_extract import write_wav_with_bext
from aafton.report import Report

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "templates" / "live12.als"
TAXA = 48000

RECURSOS = (
    "<resources>"
    '<format id="r1" name="FFVideoFormat1080p25" frameDuration="100/2500s"'
    ' width="1920" height="1080"/>'
    '<asset id="r2" name="A001" start="0s" duration="60s" hasVideo="1" hasAudio="1"'
    ' audioSources="1" audioChannels="2" audioRate="48000" format="r1">'
    '<media-rep kind="original-media" src="file:///C:/midia/A001.mov"/>'
    "</asset>"
    '<asset id="r3" name="dial02" start="36000s" duration="60s" hasAudio="1"'
    ' audioSources="1" audioChannels="1" audioRate="48000">'
    '<media-rep kind="original-media" src="file:///C:/midia/dial02.wav"/>'
    "</asset>"
    '<asset id="r4" name="musica" start="0s" duration="120s" hasAudio="1"'
    ' audioSources="1" audioChannels="2" audioRate="48000">'
    '<media-rep kind="original-media" src="file:///C:/midia/musica.wav"/>'
    "</asset>"
    '<asset id="r6" name="B002" start="0s" duration="60s" hasVideo="1" hasAudio="1"'
    ' audioSources="1" audioChannels="2" audioRate="48000" format="r1">'
    '<media-rep kind="original-media" src="file:///C:/midia/B002.mov"/>'
    "</asset>"
    '<effect id="rT" name="Basic Title"'
    ' uid=".../Titles.localized/Bumper:Opener.localized/Basic Title.moti"/>'
    '<effect id="rG" name="Custom"'
    ' uid=".../Generators.localized/Solids.localized/Custom.motn"/>'
    "</resources>"
)


def _documento(spine: str) -> str:
    """FCPXML de teste: os recursos, o projeto e a spine dados."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<!DOCTYPE fcpxml>\n"
        '<fcpxml version="1.9">'
        f"{RECURSOS}"
        '<library><event name="E"><project name="P">'
        '<sequence format="r1" duration="30s" tcStart="3600s" tcFormat="NDF"'
        ' audioLayout="stereo" audioRate="48k">'
        f"<spine>{spine}</spine>"
        "</sequence>"
        "</project></event></library>"
        "</fcpxml>"
    )


def _ler(tmp_path: Path, spine: str):
    """Grava o documento, le o FCPXML e devolve o relatorio e a cena."""
    caminho = tmp_path / "caso.fcpxml"
    caminho.write_text(_documento(spine), encoding="utf-8")
    report = Report(strict=False)
    return report, read_fcpxml(caminho, report)


def _codigos(report: Report) -> list[str]:
    """Codigos de todos os problemas registrados no relatorio."""
    return [issue.code for issue in report.issues]


def _notas(report: Report) -> list[str]:
    """Codigos das notas do relatorio."""
    return [issue.code for issue in report.issues if issue.severity == "info"]


def _mensagem(report: Report, codigo: str) -> str:
    """Mensagem da primeira nota com o codigo dado."""
    for issue in report.issues:
        if issue.code == codigo:
            return issue.message
    return ""


def _posicoes(sessao) -> list[list[tuple[str, int, int]]]:
    """Cada track com seus clips: nome, inicio e fim em segundos."""
    return [
        [
            (clip.name, clip.timeline_start_samples // TAXA, clip.timeline_end_samples // TAXA)
            for clip in track.clips
        ]
        for track in sessao.tracks
    ]


def test_corte_em_l_vira_duas_tracks(tmp_path):
    """O corte em L estende o audio: o clip que sobrepoe vai para outra track."""
    spine = (
        '<asset-clip name="A001" ref="r2" offset="3600s" start="0s" duration="10s"'
        ' audioDuration="12s"/>'
        '<asset-clip name="B002" ref="r6" offset="3610s" start="20s" duration="10s"/>'
    )

    report, sessao = _ler(tmp_path, spine)

    assert report.count("error") == 0
    assert _posicoes(sessao) == [[("A001", 3600, 3612)], [("B002", 3610, 3620)]]
    assert _notas(report).count("FORMAT_OVERLAP_MOVED_TO_TRACK") == 1
    mensagem = _mensagem(report, "FORMAT_OVERLAP_MOVED_TO_TRACK")
    assert "B002" in mensagem
    assert "A2" in mensagem


def test_corte_em_j_vira_duas_tracks(tmp_path):
    """O corte em J comeca o audio antes: o clip que sobrepoe vai para outra track."""
    spine = (
        '<asset-clip name="A001" ref="r2" offset="3600s" start="0s" duration="10s"/>'
        '<asset-clip name="B002" ref="r6" offset="3610s" start="20s" duration="10s"'
        ' audioStart="18s" audioDuration="12s"/>'
    )

    report, sessao = _ler(tmp_path, spine)

    assert report.count("error") == 0
    assert _posicoes(sessao) == [[("A001", 3600, 3610)], [("B002", 3608, 3620)]]
    assert _notas(report).count("FORMAT_OVERLAP_MOVED_TO_TRACK") == 1


def test_corte_rolado_fica_numa_track(tmp_path):
    """O corte rolado nao sobrepoe: a lane fica numa track so e sem nota."""
    spine = (
        '<asset-clip name="A001" ref="r2" offset="3600s" start="0s" duration="10s"'
        ' audioDuration="12s"/>'
        '<asset-clip name="B002" ref="r6" offset="3610s" start="20s" duration="10s"'
        ' audioStart="22s" audioDuration="8s"/>'
    )

    report, sessao = _ler(tmp_path, spine)

    assert report.count("error") == 0
    assert _posicoes(sessao) == [[("A001", 3600, 3612), ("B002", 3612, 3620)]]
    assert "FORMAT_OVERLAP_MOVED_TO_TRACK" not in _notas(report)


def test_musica_conectada_a_title_entra_na_cena(tmp_path):
    """A musica e o marcador presos a um title entram na cena."""
    spine = (
        '<title name="Abertura" ref="rT" offset="3600s" start="3600s" duration="10s">'
        '<asset-clip name="musica" ref="r4" lane="-1" offset="3600s" start="0s"'
        ' duration="10s"/>'
        '<marker start="3601s" duration="100/2500s" value="M title"/>'
        "</title>"
        '<asset-clip name="A001" ref="r2" offset="3610s" start="0s" duration="10s"/>'
    )

    report, sessao = _ler(tmp_path, spine)

    assert report.count("error") == 0
    assert _posicoes(sessao) == [[("A001", 3610, 3620)], [("musica", 3600, 3610)]]
    assert [(m.name, m.position_samples // TAXA) for m in sessao.markers] == [("M title", 3601)]


def test_musica_conectada_a_gerador_entra_na_cena(tmp_path):
    """A musica presa a um gerador de video entra na cena."""
    spine = (
        '<video name="Custom" ref="rG" offset="3600s" start="3600s" duration="10s">'
        '<asset-clip name="musica" ref="r4" lane="-1" offset="3600s" start="0s"'
        ' duration="10s"/>'
        "</video>"
        '<asset-clip name="A001" ref="r2" offset="3610s" start="0s" duration="10s"/>'
    )

    report, sessao = _ler(tmp_path, spine)

    assert report.count("error") == 0
    assert _posicoes(sessao) == [[("A001", 3610, 3620)], [("musica", 3600, 3610)]]


def test_filhos_de_audio_primario_entram_na_cena(tmp_path):
    """O clip conectado e o marcador de um item de audio primario entram na cena."""
    spine = (
        '<audio name="dial02" ref="r3" offset="3600s" start="36000s" duration="10s">'
        '<asset-clip name="musica" ref="r4" lane="-1" offset="36002s" start="0s"'
        ' duration="3s"/>'
        '<marker start="36001s" duration="100/2500s" value="M audio"/>'
        "</audio>"
    )

    report, sessao = _ler(tmp_path, spine)

    assert report.count("error") == 0
    assert _posicoes(sessao) == [[("dial02", 3600, 3610)], [("musica", 3602, 3605)]]
    assert [(m.name, m.position_samples // TAXA) for m in sessao.markers] == [("M audio", 3601)]


def test_storyline_dentro_de_storyline_usa_lane_relativa(tmp_path):
    """A lane de um item de storyline e a lane do pai mais a lane que ele declara."""
    spine = (
        '<gap name="Gap" offset="3600s" start="3600s" duration="30s">'
        '<spine lane="-1" offset="3602s">'
        '<asset-clip name="dial02" ref="r3" offset="0s" start="36001s" duration="5s">'
        '<spine lane="-1" offset="36002s">'
        '<asset-clip name="musica" ref="r4" offset="0s" start="0s" duration="2s"/>'
        "</spine>"
        "</asset-clip>"
        "</spine>"
        "</gap>"
    )

    report, sessao = _ler(tmp_path, spine)

    assert report.count("error") == 0
    assert _posicoes(sessao) == [[("dial02", 3602, 3607)], [("musica", 3603, 3605)]]
    assert "FORMAT_OVERLAP_MOVED_TO_TRACK" not in _notas(report)


def test_conectado_a_item_de_storyline_usa_lane_relativa(tmp_path):
    """O clip conectado a um item de storyline fica na lane relativa ao pai."""
    spine = (
        '<gap name="Gap" offset="3600s" start="3600s" duration="30s">'
        '<spine lane="-1" offset="3602s">'
        '<asset-clip name="dial02" ref="r3" offset="0s" start="36001s" duration="5s">'
        '<asset-clip name="musica" ref="r4" lane="-1" offset="36002s" start="0s"'
        ' duration="2s"/>'
        "</asset-clip>"
        "</spine>"
        "</gap>"
    )

    report, sessao = _ler(tmp_path, spine)

    assert report.count("error") == 0
    assert _posicoes(sessao) == [[("dial02", 3602, 3607)], [("musica", 3603, 3605)]]
    assert "FORMAT_OVERLAP_MOVED_TO_TRACK" not in _notas(report)


def test_corte_com_conectado_nao_sobrepoe(tmp_path):
    """O corte com um clip conectado nao vira sobreposicao nem track extra."""
    spine = (
        '<asset-clip name="A001" ref="r2" offset="3600s" start="10s" duration="10s"'
        ' audioStart="8s" audioDuration="14s">'
        '<asset-clip name="dial02" ref="r3" lane="-1" offset="12s" start="36001s"'
        ' duration="3s"/>'
        "</asset-clip>"
    )

    report, sessao = _ler(tmp_path, spine)

    assert report.count("error") == 0
    assert _posicoes(sessao) == [[("A001", 3598, 3612)], [("dial02", 3602, 3605)]]
    assert "FORMAT_OVERLAP_MOVED_TO_TRACK" not in _notas(report)


def test_clip_desligado_anda_com_os_filhos(tmp_path):
    """Um clip desligado e erro, mas os clips presos a ele continuam na cena."""
    spine = (
        '<asset-clip name="A001" ref="r2" offset="3600s" start="0s" duration="10s"'
        ' enabled="0">'
        '<asset-clip name="musica" ref="r4" lane="-1" offset="2s" start="0s" duration="3s"/>'
        "</asset-clip>"
    )

    report, sessao = _ler(tmp_path, spine)

    assert "FORMAT_CLIP_DISABLED" in _codigos(report)
    assert _posicoes(sessao) == [[("musica", 3602, 3605)]]


def test_fade_do_audio_conectado_nao_e_erro_do_primario(tmp_path):
    """O fade do audio conectado nao vira erro do clip primario.

    O fade e erro do item de audio que o carrega: o relatorio nomeia o
    dial02, e nao o A001.
    """
    spine = (
        '<asset-clip name="A001" ref="r2" offset="3600s" start="0s" duration="10s">'
        '<audio name="dial02" ref="r3" lane="-1" offset="2s" start="36001s" duration="3s">'
        '<adjust-volume><param name="amount">'
        '<fadeIn type="easeIn" duration="1s"/></param></adjust-volume>'
        "</audio>"
        "</asset-clip>"
    )

    report, sessao = _ler(tmp_path, spine)

    assert _codigos(report).count("FORMAT_FADE_UNSUPPORTED") == 1
    mensagem = _mensagem(report, "FORMAT_FADE_UNSUPPORTED")
    assert "dial02" in mensagem
    assert "A001" not in mensagem
    assert _posicoes(sessao) == [[("A001", 3600, 3610)], [("dial02", 3602, 3605)]]
    assert [c.gain_linear for c in sessao.all_clips()] == [
        pytest.approx(1.0),
        pytest.approx(1.0),
    ]


def test_volume_sem_amount_e_ganho_neutro(tmp_path):
    """Um adjust-volume sem amount e 0 dB: o ganho do clip fica neutro."""
    spine = (
        '<gap name="Gap" offset="3600s" start="3600s" duration="30s">'
        '<asset-clip name="musica" ref="r4" lane="-1" offset="3602s" start="1s"'
        ' duration="3s"><adjust-volume/></asset-clip>'
        "</gap>"
    )

    report, sessao = _ler(tmp_path, spine)

    assert report.count("error") == 0, _codigos(report)
    assert "FORMAT_GAIN_UNSUPPORTED" not in _codigos(report)
    clips = list(sessao.all_clips())
    assert [c.gain_linear for c in clips] == [pytest.approx(1.0)]


def _wav(destino: Path) -> Path:
    """WAV mono de 48 kHz e 24 bits, com o chunk bext."""
    return write_wav_with_bext(
        destino,
        b"\x00\x00\x00" * TAXA * 20,
        channels=1,
        sample_rate=TAXA,
        bits_per_sample=24,
        time_reference=0,
    )


def _documento_do_corte(pasta: Path) -> str:
    """FCPXML do corte em L, com os arquivos de midia reais da pasta."""
    recursos = (
        "<resources>"
        '<format id="r1" name="FFVideoFormat1080p25" frameDuration="100/2500s"'
        ' width="1920" height="1080"/>'
        '<asset id="r2" name="dialA.wav" start="0s" duration="20s" hasAudio="1"'
        ' audioSources="1" audioChannels="1" audioRate="48000" format="r1">'
        f'<media-rep kind="original-media" src="{(pasta / "dialA.wav").as_uri()}"/>'
        "</asset>"
        '<asset id="r3" name="dialB.wav" start="0s" duration="20s" hasAudio="1"'
        ' audioSources="1" audioChannels="1" audioRate="48000" format="r1">'
        f'<media-rep kind="original-media" src="{(pasta / "dialB.wav").as_uri()}"/>'
        "</asset>"
        "</resources>"
    )
    spine = (
        '<asset-clip name="dialA" ref="r2" offset="3600s" start="0s" duration="10s"'
        ' audioDuration="12s"/>'
        '<asset-clip name="dialB" ref="r3" offset="3610s" start="0s" duration="10s"/>'
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<!DOCTYPE fcpxml>\n"
        '<fcpxml version="1.9">'
        f"{recursos}"
        '<library><event name="E"><project name="P">'
        '<sequence format="r1" duration="30s" tcStart="3600s" tcFormat="NDF"'
        ' audioLayout="stereo" audioRate="48k">'
        f"<spine>{spine}</spine>"
        "</sequence>"
        "</project></event></library>"
        "</fcpxml>"
    )


def _codigos_json(relatorio: Path) -> list[str]:
    """Codigos dos problemas do relatorio gravado em json."""
    dados = json.loads(relatorio.read_text(encoding="utf-8"))
    return [issue["code"] for issue in dados["issues"]]


def test_import_e_export_do_corte_em_l_nao_da_sobreposicao(tmp_path):
    """O corte em L importado e exportado nao vira WRITE_OVERLAP no AAF."""
    pasta = tmp_path / "cena"
    pasta.mkdir()
    _wav(pasta / "dialA.wav")
    _wav(pasta / "dialB.wav")
    origem = pasta / "lcut.fcpxml"
    origem.write_text(_documento_do_corte(pasta), encoding="utf-8")
    als = tmp_path / "lcut.als"
    aaf = tmp_path / "lcut.aaf"
    relatorio_import = tmp_path / "import.json"
    relatorio_export = tmp_path / "export.json"

    codigo = main(
        [
            "import",
            str(origem),
            str(als),
            "--template",
            str(TEMPLATE),
            "--report",
            str(relatorio_import),
        ]
    )

    assert codigo == 0, _codigos_json(relatorio_import)
    assert "FORMAT_OVERLAP_MOVED_TO_TRACK" in _codigos_json(relatorio_import)
    codigo = main(
        [
            "export",
            str(als),
            str(aaf),
            "--report",
            str(relatorio_export),
        ]
    )

    assert "WRITE_OVERLAP" not in _codigos_json(relatorio_export)
    assert codigo == 0, _codigos_json(relatorio_export)
    assert aaf.exists()
