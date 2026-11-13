"""Exportacao de transicoes por track: um crossfade vira uma Transition so.

Regressao do defeito de aaf_write._preflight: os conjuntos de fades
consumidos por transicao eram sobrescritos a cada track nao-video, e
chegavam ao writer apenas os da ultima track. O crossfade de qualquer
outra track era exportado como Transition E como envelope de ganho, com o
fade aplicado duas vezes no AAF.
"""

import aaf2
from aaf2.auid import AUID

from aafton.aaf_write import write_aaf
from aafton.ir import ClipIR, FadeIR, MediaAsset, SessionIR, TrackIR
from aafton.report import Report
from aafton.timecode import TimecodeRate

SR = 48000
ORIGEM = 172800000
OVERLAP = 500
AUID_GANHO = AUID("9d2ea894-0968-11d3-8a38-0050040ef7d2")


def _make_dummy_wav(path, sample_rate=SR, duration_seconds=10.0):
    """WAV PCM 16 bits de silencio, com cabecalho RIFF valido."""
    num_samples = int(sample_rate * duration_seconds)
    bytes_per_sample = 2
    data_size = num_samples * bytes_per_sample
    file_size = 36 + data_size
    header = (
        b"RIFF"
        + file_size.to_bytes(4, "little")
        + b"WAVEfmt \x10\x00\x00\x00"
        + (1).to_bytes(2, "little")
        + (1).to_bytes(2, "little")
        + sample_rate.to_bytes(4, "little")
        + (sample_rate * bytes_per_sample).to_bytes(4, "little")
        + bytes_per_sample.to_bytes(2, "little")
        + (16).to_bytes(2, "little")
        + b"data"
        + data_size.to_bytes(4, "little")
    )
    path.write_bytes(header + b"\x00" * data_size)
    return path


def _asset(caminho, segundos=10):
    """Asset resolvido do WAV mono."""
    return MediaAsset(
        id="m1",
        filename=caminho.name,
        channels=1,
        sample_rate=SR,
        duration_samples=SR * segundos,
        status="resolved",
        resolved_path=str(caminho),
    )


def _clip(prefixo, track_id, indice, inicio, duracao, fade_in=None, fade_out=None):
    """Clip do canal 0 da track, com fades opcionais."""
    return ClipIR(
        id=f"{prefixo}{indice}",
        track_id=track_id,
        channel_index=0,
        event_index=indice,
        name=f"{prefixo}{indice}.wav",
        media_id="m1",
        timeline_start_samples=inicio,
        timeline_end_samples=inicio + duracao,
        source_offset_samples=0,
        fade_in=fade_in,
        fade_out=fade_out,
    )


def _crossfade(prefixo, track_id, inicio):
    """Dois clipes que se encostam com um crossfade simetrico."""
    primeiro = _clip(
        prefixo,
        track_id,
        1,
        inicio,
        2000,
        fade_out=FadeIR(kind="out", duration_samples=OVERLAP),
    )
    segundo = _clip(
        prefixo,
        track_id,
        2,
        inicio + 2000 - OVERLAP,
        2000,
        fade_in=FadeIR(kind="in", duration_samples=OVERLAP),
    )
    return [primeiro, segundo]


def _track(track_id, nome, indice, clips):
    """Track de audio mono com os clips informados."""
    track = TrackIR(id=track_id, source_index=indice, name=nome, kind="audio", channel_count=1)
    track.clips.extend(clips)
    return track


def _sessao(nome, tracks, asset):
    """Sessao a 25 fps com o asset informado."""
    return SessionIR(
        name=nome,
        sample_rate=SR,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=ORIGEM,
        tracks=tracks,
        media=[asset],
    )


def _sequencia(slot):
    """Sequence do slot, descendo do grupo de pan estatico."""
    if type(slot.segment).__name__ == "Sequence":
        return slot.segment
    for interno in getattr(slot.segment, "segments", []):
        if type(interno).__name__ == "Sequence":
            return interno
    return None


def _slots_de_audio(caminho):
    """Nome e componentes de cada slot de audio do AAF exportado."""
    with aaf2.open(str(caminho), "r") as f:
        comp = next(m for m in f.content.mobs if type(m).__name__ == "CompositionMob")
        slots = [s for s in comp.slots if type(s.segment).__name__ != "Timecode"]
        return [(s.name, list(_sequencia(s).components)) for s in slots]


def _transicoes(componentes):
    """Componentes Transition da Sequence."""
    return [c for c in componentes if type(c).__name__ == "Transition"]


def _sem_lacunas(componentes):
    """Componentes da Sequence sem os Filler das lacunas entre clips."""
    return [c for c in componentes if type(c).__name__ != "Filler"]


def _grupos_de_ganho(componentes):
    """OperationGroups de ganho entre os componentes da Sequence."""
    achados = []
    for componente in componentes:
        if type(componente).__name__ != "OperationGroup":
            continue
        if componente.operation.auid == AUID_GANHO:
            achados.append(componente)
    return achados


def _envelopes_de_ganho(componentes):
    """OperationGroups de ganho com envelope variavel (VaryingValue)."""
    achados = []
    for componente in _grupos_de_ganho(componentes):
        for parametro in getattr(componente, "parameters", []):
            if type(parametro).__name__ == "VaryingValue":
                achados.append(componente)
    return achados


def test_crossfade_em_duas_tracks_vira_uma_transition_por_slot(tmp_path):
    """Cada crossfade vira uma Transition no seu slot, sem envelope de ganho.

    Antes da correcao, so a ultima track nao-video conservava os fades
    consumidos: o crossfade da outra track virava Transition E envelope de
    ganho, e o fade era aplicado duas vezes no AAF exportado.
    """
    wav = _make_dummy_wav(tmp_path / "audio.wav")
    asset = _asset(wav)
    tracks = [
        _track("t0", "AUDIO 1", 0, _crossfade("a", "t0", ORIGEM)),
        _track("t1", "AUDIO 2", 1, _crossfade("b", "t1", ORIGEM + 100000)),
    ]
    sessao = _sessao("duas_tracks", tracks, asset)
    destino = tmp_path / "duas_tracks.aaf"
    relatorio = Report(strict=False)

    write_aaf(sessao, destino, relatorio)

    codigos = [issue.code for issue in relatorio.issues]
    assert "FADE_CONVERTED_TO_ENVELOPE" not in codigos
    assert "WRITE_FADE_UNSUPPORTED" not in codigos

    slots = _slots_de_audio(destino)
    assert [nome for nome, _ in slots] == ["AUDIO 1", "AUDIO 2"]
    for nome, componentes in slots:
        assert [type(c).__name__ for c in _sem_lacunas(componentes)] == [
            "SourceClip",
            "Transition",
            "SourceClip",
        ], nome
        assert len(_transicoes(componentes)) == 1, nome
        assert _grupos_de_ganho(componentes) == [], nome
        assert _envelopes_de_ganho(componentes) == [], nome


def test_crossfade_na_primeira_track_com_track_simples_depois(tmp_path):
    """O crossfade da primeira track nao vira envelope quando outra track vem depois.

    Este e o caso exato do defeito: a track do crossfade nao era a ultima
    nao-video da sessao, e seus clips recebiam o fade duas vezes.
    """
    wav = _make_dummy_wav(tmp_path / "audio.wav")
    asset = _asset(wav)
    tracks = [
        _track("t0", "AUDIO 1", 0, _crossfade("a", "t0", ORIGEM)),
        _track("t1", "AUDIO 2", 1, [_clip("b", "t1", 1, ORIGEM + 100000, 2000)]),
    ]
    sessao = _sessao("crossfade_primeira", tracks, asset)
    destino = tmp_path / "crossfade_primeira.aaf"
    relatorio = Report(strict=False)

    write_aaf(sessao, destino, relatorio)

    codigos = [issue.code for issue in relatorio.issues]
    assert "FADE_CONVERTED_TO_ENVELOPE" not in codigos
    assert "WRITE_FADE_UNSUPPORTED" not in codigos

    slots = _slots_de_audio(destino)
    assert [nome for nome, _ in slots] == ["AUDIO 1", "AUDIO 2"]
    componentes = slots[0][1]
    assert [type(c).__name__ for c in _sem_lacunas(componentes)] == [
        "SourceClip",
        "Transition",
        "SourceClip",
    ]
    assert len(_transicoes(componentes)) == 1
    assert _grupos_de_ganho(componentes) == []
    assert _envelopes_de_ganho(componentes) == []
    assert _transicoes(slots[1][1]) == []
