"""Agrupamento por prefixo quando a track tem mais de dois canais.

A contagem dos prefixos pulava as tracks multicanal, mas a atribuicao nao
pulava: um prefixo que so a track multicanal tem levantava KeyError ao ser
consultado no dicionario de contagens. A track multicanal ja sai no seu
proprio grupo, entao ela nao entra em nenhum grupo por prefixo.
"""

from aafton.als_write_base import WriteOptions
from aafton.als_write_groups import infer_track_groups
from aafton.ir import SessionIR, TrackIR
from aafton.timecode import TimecodeRate


def _track(track_id, nome, canais, indice):
    """Track de audio com o nome e a contagem de canais informados."""
    return TrackIR(
        id=track_id,
        source_index=indice,
        name=nome,
        kind="audio",
        channel_count=canais,
    )


def _sessao():
    """Sessao com duas tracks mono de prefixo DX e uma track 5.1 de prefixo SURR."""
    return SessionIR(
        name="grupos",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=0,
        tracks=[
            _track("t1", "DX 1", 1, 0),
            _track("t2", "DX 2", 1, 1),
            _track("t3", "SURR 1", 6, 2),
        ],
        media=[],
    )


def test_prefixo_so_da_track_multicanal_nao_entra_em_grupo():
    """O prefixo que so a track 5.1 tem nao levanta KeyError e nao agrupa."""
    sessao = _sessao()
    opcoes = WriteOptions(sample_rate=48000, groups="prefix")

    grupos = infer_track_groups(sessao, opcoes)

    por_nome = {t.name: grupos.get(id(t)) for t in sessao.tracks}
    assert por_nome == {"DX 1": "DX", "DX 2": "DX", "SURR 1": None}
