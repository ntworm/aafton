"""Grupos por sufixo quando a track tem mais de dois canais.

A regra do sufixo contava e atribuia a track multicanal como se fosse uma
track comum: a track 5.1 entrava no grupo "AMB" e saia duas vezes no .als.
Agora ela pula as tracks com mais de dois canais, como a regra do prefixo.
"""

from pathlib import Path

import pytest

from aafton.als_write import WriteOptions, write_session
from aafton.als_write_groups import infer_track_groups
from aafton.als_xml import load_als
from aafton.ir import SessionIR
from aafton.report import Report
from aafton.timecode import TimecodeRate
from tests.unit.test_melhorias_grupos import _track

TEMPLATE = Path("templates/live12.als")


def _sessao():
    """Sessao com uma track stereo e uma track 5.1 de mesmo nome base."""
    return SessionIR(
        name="grupos_sufixo",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=0,
        tracks=[_track("t1", "AMB [ST]", 2, 0), _track("t2", "AMB [5.1]", 6, 1)],
        media=[],
    )


def _audio_tracks(caminho):
    """Nomes das AudioTracks do .als gerado."""
    root = load_als(caminho)
    nomes = []
    for node in root.findall(".//LiveSet/Tracks/AudioTrack"):
        nome = node.find(".//UserName")
        if nome is not None:
            nomes.append(nome.get("Value"))
    return nomes


def test_sufixo_deixa_a_track_multicanal_fora_do_grupo():
    """A track 5.1 nao entra em nenhum grupo pelo sufixo."""
    sessao = _sessao()
    grupos = infer_track_groups(sessao, WriteOptions(sample_rate=48000, groups="prefix"))

    por_nome = {t.name: grupos.get(id(t)) for t in sessao.tracks}
    assert por_nome["AMB [5.1]"] is None


@pytest.mark.skipif(
    not TEMPLATE.exists(), reason="Portao B: templates/live12.als ainda nao foi autorado"
)
def test_escrita_emite_a_track_51_uma_vez_so(tmp_path):
    """O .als tem uma AudioTrack da track 5.1, e nao duas."""
    sessao = _sessao()
    destino = tmp_path / "saida.als"

    write_session(
        sessao,
        TEMPLATE,
        destino,
        WriteOptions(sample_rate=48000, groups="prefix"),
        Report(strict=False),
    )

    nomes = _audio_tracks(destino)
    assert "AMB [5.1]" not in nomes
    assert len(nomes) == 7
