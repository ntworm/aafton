"""Fade de um crossfade que conflita com a automacao segmentada.

Quando a automacao de ganho corta a regiao de overlap, o crossfade nao vira
Transition: o preflight ja reporta
WRITE_TRANSITION_SEGMENTED_AUTOMATION_UNSUPPORTED. Os dois fades do par
continuavam marcados como consumidos, e o fade-out do clipe anterior sumia
do AAF sem aviso e sem envelope de ganho.
"""

import math

import aaf2
from aaf2.auid import AUID

from aafton.aaf_read_points import append_control_point, point_list_of
from aafton.aaf_write import write_aaf
from aafton.ir import ClipIR, FadeIR, MediaAsset, SessionIR, TrackIR
from aafton.report import Report
from aafton.timecode import TimecodeRate

SR = 48000
ORIGEM = 172800000
OVERLAP = 500
DURACAO = 2000
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
        DURACAO,
        fade_out=FadeIR(kind="out", duration_samples=OVERLAP),
    )
    segundo = _clip(
        prefixo,
        track_id,
        2,
        inicio + DURACAO - OVERLAP,
        DURACAO,
        fade_in=FadeIR(kind="in", duration_samples=OVERLAP),
    )
    return [primeiro, segundo]


def _track(track_id, nome, indice, clips, gain_points=None):
    """Track de audio mono com os clips e a automacao informados."""
    track = TrackIR(id=track_id, source_index=indice, name=nome, kind="audio", channel_count=1)
    track.clips.extend(clips)
    track.gain_points.extend(gain_points or [])
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


def _envelopes_de_ganho(componentes):
    """OperationGroups de ganho com envelope variavel (VaryingValue)."""
    achados = []
    for componente in componentes:
        if type(componente).__name__ != "OperationGroup":
            continue
        if componente.operation.auid != AUID_GANHO:
            continue
        for parametro in getattr(componente, "parameters", []):
            if type(parametro).__name__ == "VaryingValue":
                achados.append(componente)
    return achados


def _keyframes(envelope):
    """Keyframes (tempo, valor) de um envelope de ganho."""
    pontos = []
    for parametro in envelope.parameters:
        if type(parametro).__name__ != "VaryingValue":
            continue
        pares = []
        for ponto in point_list_of(parametro) or []:
            append_control_point(ponto, pares, [])
        pontos.extend(pares)
    return pontos


def _dados_do_slot(sequence):
    """Tipos, transicoes, envelopes e keyframes do Sequence de um slot."""
    componentes = list(sequence.components)
    envelopes = _envelopes_de_ganho(componentes)
    pontos = []
    for envelope in envelopes:
        pontos.extend(_keyframes(envelope))
    return {
        "tipos": [type(c).__name__ for c in componentes],
        "transicoes": len([c for c in componentes if type(c).__name__ == "Transition"]),
        "envelopes": len(envelopes),
        "pontos": pontos,
    }


def _slots_de_audio(caminho):
    """Nome e dados de cada slot de audio do AAF exportado.

    O arquivo fica aberto enquanto os componentes sao lidos: o pyaaf2 resolve
    as referencias de forma preguicosa e a leitura depois do fechamento falha.
    """
    with aaf2.open(str(caminho), "r") as f:
        comp = next(m for m in f.content.mobs if type(m).__name__ == "CompositionMob")
        slots = [s for s in comp.slots if type(s.segment).__name__ != "Timecode"]
        return [(s.name, _dados_do_slot(_sequencia(s))) for s in slots]


def _sem_lacunas(tipos):
    """Tipos dos componentes sem os Filler das lacunas entre clips."""
    return [t for t in tipos if t != "Filler"]


def test_fade_out_de_crossfade_conflitante_vira_envelope(tmp_path):
    """O fade-out do clipe anterior sobrevive como envelope de ganho.

    Antes da correcao, o par era marcado como consumido mesmo sem Transition:
    o fade-out sumia do AAF e nada era reportado sobre ele.
    """
    wav = _make_dummy_wav(tmp_path / "audio.wav")
    asset = _asset(wav)
    tracks = [
        _track(
            "t0",
            "AUDIO 1",
            0,
            _crossfade("a", "t0", ORIGEM),
            [(ORIGEM + 1600, 1.0)],
        ),
    ]
    sessao = _sessao("conflito", tracks, asset)
    destino = tmp_path / "conflito.aaf"
    relatorio = Report(strict=False)

    write_aaf(sessao, destino, relatorio)

    codigos = [issue.code for issue in relatorio.issues]
    assert "WRITE_TRANSITION_SEGMENTED_AUTOMATION_UNSUPPORTED" in codigos
    assert "WRITE_FADE_UNSUPPORTED" in codigos
    assert "FADE_CONVERTED_TO_ENVELOPE" in codigos

    slots = _slots_de_audio(destino)
    dados = slots[0][1]
    assert dados["envelopes"] == 1
    pontos = dados["pontos"]
    assert pontos
    assert math.isclose(pontos[-1][1], 0.0, abs_tol=1e-6)


def test_fade_independente_e_crossfade_seguem_caminhos_proprios(tmp_path):
    """O fade independente vira Transition ao lado de um Filler e o crossfade continua uma Transition."""
    wav = _make_dummy_wav(tmp_path / "audio.wav")
    asset = _asset(wav)
    fade = FadeIR(kind="in", duration_samples=OVERLAP)
    tracks = [
        _track("t0", "AUDIO 1", 0, _crossfade("a", "t0", ORIGEM)),
        _track(
            "t1",
            "AUDIO 2",
            1,
            [_clip("b", "t1", 1, ORIGEM + 100000, DURACAO, fade_in=fade)],
        ),
    ]
    sessao = _sessao("caminhos", tracks, asset)
    destino = tmp_path / "caminhos.aaf"
    relatorio = Report(strict=False)

    write_aaf(sessao, destino, relatorio)

    codigos = [issue.code for issue in relatorio.issues]
    assert "FADE_WRITTEN_AS_TRANSITION" in codigos
    assert "FADE_CONVERTED_TO_ENVELOPE" not in codigos

    slots = _slots_de_audio(destino)
    assert [nome for nome, _ in slots] == ["AUDIO 1", "AUDIO 2"]

    crossfade = slots[0][1]
    assert _sem_lacunas(crossfade["tipos"]) == ["SourceClip", "Transition", "SourceClip"]
    assert crossfade["transicoes"] == 1
    assert crossfade["envelopes"] == 0

    independente = slots[1][1]
    assert _sem_lacunas(independente["tipos"]) == ["Transition", "SourceClip"]
    assert independente["transicoes"] == 1
    assert independente["envelopes"] == 0


def test_crossfade_sem_conflito_passa_no_relatorio_estrito(tmp_path):
    """O crossfade sem conflito de automacao continua valendo em modo estrito."""
    wav = _make_dummy_wav(tmp_path / "audio.wav")
    asset = _asset(wav)
    tracks = [
        _track("t0", "AUDIO 1", 0, _crossfade("a", "t0", ORIGEM)),
        _track("t1", "AUDIO 2", 1, _crossfade("b", "t1", ORIGEM + 100000)),
    ]
    sessao = _sessao("duas_tracks", tracks, asset)
    destino = tmp_path / "duas_tracks.aaf"
    relatorio = Report(strict=True)

    write_aaf(sessao, destino, relatorio)

    assert not relatorio.incomplete

    slots = _slots_de_audio(destino)
    assert [nome for nome, _ in slots] == ["AUDIO 1", "AUDIO 2"]
    for nome, dados in slots:
        assert dados["transicoes"] == 1, nome
        assert dados["envelopes"] == 0, nome
