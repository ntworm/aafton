"""Round trip dos tres formatos de intercambio: fixture, .als e arquivo novo.

O ciclo testado e o do produto: a fixture entra pelo comando import, o
verify compara a origem com o .als gerado e o export devolve o arquivo no
mesmo formato. A cena lida do arquivo exportado tem de ser igual a cena
lida da fixture.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from aafton.cli import main
from aafton.formats.origem import read_origin
from aafton.ir import ClipIR, SessionIR
from aafton.report import Report
from aafton.sidecar import read_anchor
from aafton.timecode import TimecodeRate
from tests.unit.test_formats_import import _wav

pytest.importorskip("opentimelineio")

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures" / "formatos"
TEMPLATE = REPO / "templates" / "live12.als"

FORMATO_EDL = "cena12.edl"
FORMATO_XML = "cena12_premiere.xml"
FORMATO_FCPXML = "cena12.fcpxml"
FORMATOS = [FORMATO_EDL, FORMATO_XML, FORMATO_FCPXML]
TC_ARQUIVO = 1728000000


@pytest.fixture
def cena(tmp_path):
    """A pasta com as tres fixtures e as duas midias da cena CENA12."""
    for nome in FORMATOS:
        shutil.copy(FIXTURES / nome, tmp_path / nome)
    _wav(tmp_path / "dial01.wav", 20, TC_ARQUIVO)
    _wav(tmp_path / "dial02.wav", 10, 0)
    return tmp_path


def _importar(cena: Path, formato: str, saida: Path) -> int:
    """Roda o import estrito da fixture; a EDL precisa da taxa de frames."""
    argumentos = ["import", str(cena / formato), str(saida), "--template", str(TEMPLATE)]
    if formato.endswith(".edl"):
        argumentos += ["--edl-rate", "25"]
    return main(argumentos)


def _origem(caminho: Path, pasta: Path) -> SessionIR:
    """Le um arquivo de origem pelo leitor de formatos, sem erros no relatorio."""
    report = Report(strict=False)
    taxa = TimecodeRate(25, 1) if caminho.suffix.lower() == ".edl" else None
    sessao = read_origin(caminho, report, edl_rate=taxa, media_roots=[pasta])
    erros = [issue.code for issue in report.issues if issue.severity == "error"]
    assert erros == [], f"o relatorio de {caminho.name!r} tem erros: {erros}"
    return sessao


def _arquivo(sessao: SessionIR, clip: ClipIR) -> str:
    """Nome do arquivo de midia do clip."""
    asset = sessao.media_by_id(clip.media_id) if clip.media_id else None
    return asset.filename if asset is not None else ""


def _clips(sessao: SessionIR) -> list[list[tuple]]:
    """Clips de cada track, na ordem, com inicio, fim, offset e arquivo."""
    return [
        [
            (
                clip.timeline_start_samples,
                clip.timeline_end_samples,
                clip.source_offset_samples,
                _arquivo(sessao, clip),
            )
            for clip in track.clips
        ]
        for track in sessao.tracks
    ]


def _marcadores(sessao: SessionIR) -> list[tuple[str, int]]:
    """Marcadores da sessao, com nome e posicao em samples."""
    return [(marcador.name, marcador.position_samples) for marcador in sessao.markers]


@pytest.mark.parametrize("formato", FORMATOS)
def test_round_trip_preserva_a_cena_da_fixture(cena, tmp_path, formato):
    als = tmp_path / "cena.als"
    volta = tmp_path / f"volta{Path(formato).suffix}"

    assert _importar(cena, formato, als) == 0
    assert als.exists()
    assert main(["verify", str(cena / formato), str(als)]) == 0
    assert main(["export", str(als), str(volta)]) == 0
    assert volta.exists()

    antes = _origem(cena / formato, tmp_path)
    depois = _origem(volta, tmp_path)

    assert [len(track.clips) for track in antes.tracks] == [2, 1]
    assert _clips(depois) == _clips(antes)
    assert depois.start_timecode_samples == antes.start_timecode_samples
    if formato != FORMATO_EDL:
        assert _marcadores(antes) == [("TESTE", 172896000)]
        assert _marcadores(depois) == _marcadores(antes)


def test_verify_do_edl_tem_a_taxa_do_sidecar_do_als(cena, tmp_path):
    """O verify nao tem --edl-rate: a taxa vem do sidecar escrito com o .als."""
    als = tmp_path / "cena.als"

    assert _importar(cena, FORMATO_EDL, als) == 0
    assert read_anchor(als).timecode_rate == TimecodeRate(25, 1)

    assert main(["verify", str(cena / FORMATO_EDL), str(als)]) == 0
