"""Fade independente no export: Transition ao lado de um Filler.

O fade de um clip que nao faz par com um vizinho vai para o AAF na forma que o
leitor ja le: depois de um Filler e o fade-in a partir do silencio, antes de um
Filler e o fade-out para o silencio. As posicoes dos clips nao mudam, e o AAF
lido de volta devolve os mesmos fades, sem envelope de ganho no clip.
"""

import pytest

from aafton.aaf_read import read_aaf
from aafton.aaf_write import write_aaf
from aafton.ir import FadeIR, TrackIR
from aafton.report import Report, StrictModeError

from tests.unit.test_aaf_write_transicoes import (
    ORIGEM,
    _asset,
    _clip,
    _make_dummy_wav,
    _sessao,
    _slots_de_audio,
    _track,
)

DUR = 4000
FI = 300
FO = 500


def _entrada(samples=FI):
    return FadeIR(kind="in", duration_samples=samples)


def _saida(samples=FO):
    return FadeIR(kind="out", duration_samples=samples)


def _exportar(tmp_path, clips, strict=True, track=None):
    """Exporta uma track com os clips e devolve o caminho, o relatorio e a sessao."""
    wav = _make_dummy_wav(tmp_path / "audio.wav")
    track = track or _track("t1", "Dialogo", 1, clips)
    sessao = _sessao("fades", [track], _asset(wav))
    destino = tmp_path / "fades.aaf"
    relatorio = Report(strict=strict)
    write_aaf(sessao, destino, relatorio)
    return destino, relatorio, sessao


def _forma(destino, slot=0):
    """Tipo e comprimento de cada componente da Sequence do slot."""
    componentes = _slots_de_audio(destino)[slot][1]
    return [(_tipo(c), int(c.length)) for c in componentes]


def _tipo(componente):
    """Filler, Transition ou Clip (o SourceClip, com ou sem o grupo de ganho)."""
    nome = type(componente).__name__
    return nome if nome in ("Filler", "Transition") else "Clip"


def _lidos(destino):
    """Inicio, fim, fade-in e fade-out de cada clip do AAF lido de volta."""
    sessao = read_aaf(destino, Report(strict=False), fades="native")
    clips = sorted(sessao.tracks[0].clips, key=lambda c: c.timeline_start_samples)
    return [
        (
            c.timeline_start_samples - ORIGEM,
            c.timeline_end_samples - ORIGEM,
            c.fade_in.duration_samples if c.fade_in else 0,
            c.fade_out.duration_samples if c.fade_out else 0,
        )
        for c in clips
    ]


def _codigos(relatorio):
    return [i.code for i in relatorio.issues]


def test_fade_in_a_partir_do_silencio_vira_filler_e_transition(tmp_path):
    clips = [_clip("a", "t1", 1, ORIGEM + 1000, DUR, fade_in=_entrada())]
    destino, relatorio, _ = _exportar(tmp_path, clips)

    assert _forma(destino) == [("Filler", 1000 + FI), ("Transition", FI), ("Clip", DUR)]
    assert _lidos(destino) == [(1000, 1000 + DUR, FI, 0)]
    assert "WRITE_FADE_UNSUPPORTED" not in _codigos(relatorio)
    assert "FADE_CONVERTED_TO_ENVELOPE" not in _codigos(relatorio)


def test_fade_out_para_o_silencio_vira_transition_e_filler(tmp_path):
    clips = [_clip("a", "t1", 1, ORIGEM + 1000, DUR, fade_out=_saida())]
    destino, _, _ = _exportar(tmp_path, clips)

    assert _forma(destino) == [
        ("Filler", 1000),
        ("Clip", DUR),
        ("Transition", FO),
        ("Filler", FO),
    ]
    assert _lidos(destino) == [(1000, 1000 + DUR, 0, FO)]


def test_clip_no_inicio_da_timeline_com_os_dois_fades(tmp_path):
    clips = [_clip("a", "t1", 1, ORIGEM, DUR, fade_in=_entrada(), fade_out=_saida())]
    destino, _, _ = _exportar(tmp_path, clips)

    assert _forma(destino) == [
        ("Filler", FI),
        ("Transition", FI),
        ("Clip", DUR),
        ("Transition", FO),
        ("Filler", FO),
    ]
    assert _lidos(destino) == [(0, DUR, FI, FO)]


def test_dois_clips_encostados_dividem_um_filler(tmp_path):
    clips = [
        _clip("a", "t1", 1, ORIGEM, DUR, fade_out=_saida()),
        _clip("a", "t1", 2, ORIGEM + DUR, DUR, fade_in=_entrada()),
    ]
    destino, _, _ = _exportar(tmp_path, clips)

    assert _forma(destino) == [
        ("Clip", DUR),
        ("Transition", FO),
        ("Filler", FO + FI),
        ("Transition", FI),
        ("Clip", DUR),
    ]
    assert _lidos(destino) == [(0, DUR, 0, FO), (DUR, 2 * DUR, FI, 0)]


def test_lacuna_entre_dois_clips_soma_os_dois_fades(tmp_path):
    clips = [
        _clip("a", "t1", 1, ORIGEM, DUR, fade_out=_saida()),
        _clip("a", "t1", 2, ORIGEM + DUR + 700, DUR, fade_in=_entrada()),
    ]
    destino, _, _ = _exportar(tmp_path, clips)

    assert _forma(destino)[2] == ("Filler", FO + 700 + FI)
    assert _lidos(destino) == [(0, DUR, 0, FO), (DUR + 700, 2 * DUR + 700, FI, 0)]


def test_crossfade_de_um_lado_e_fade_para_o_silencio_do_outro(tmp_path):
    cruzado = 400
    clips = [
        _clip("a", "t1", 1, ORIGEM, DUR, fade_in=_entrada(), fade_out=_saida(cruzado)),
        _clip(
            "a",
            "t1",
            2,
            ORIGEM + DUR - cruzado,
            DUR,
            fade_in=_entrada(cruzado),
            fade_out=_saida(),
        ),
    ]
    destino, relatorio, _ = _exportar(tmp_path, clips)

    assert _forma(destino) == [
        ("Filler", FI),
        ("Transition", FI),
        ("Clip", DUR),
        ("Transition", cruzado),
        ("Clip", DUR),
        ("Transition", FO),
        ("Filler", FO),
    ]
    assert _lidos(destino) == [
        (0, DUR, FI, cruzado),
        (DUR - cruzado, 2 * DUR - cruzado, cruzado, FO),
    ]
    assert "WRITE_FADE_UNSUPPORTED" not in _codigos(relatorio)


def test_track_stereo_grava_o_fade_uma_vez_no_slot(tmp_path):
    esquerdo = _clip("a", "t1", 1, ORIGEM + 1000, DUR, fade_in=_entrada(), fade_out=_saida())
    direito = _clip("b", "t1", 1, ORIGEM + 1000, DUR, fade_in=_entrada(), fade_out=_saida())
    direito.channel_index = 1
    track = TrackIR(id="t1", source_index=1, name="Musica", kind="audio", channel_count=2)
    track.clips.extend([esquerdo, direito])
    destino, relatorio, _ = _exportar(tmp_path, [], track=track)

    assert len(_slots_de_audio(destino)) == 1
    assert _forma(destino) == [
        ("Filler", 1000 + FI),
        ("Transition", FI),
        ("Clip", DUR),
        ("Transition", FO),
        ("Filler", FO),
    ]
    avisos = [i for i in relatorio.issues if i.code == "FADE_WRITTEN_AS_TRANSITION"]
    assert len(avisos) == 1
    assert avisos[0].context == {"track": "Musica", "fades": 2}


def test_o_relatorio_conta_os_fades_gravados_por_track(tmp_path):
    clips = [
        _clip("a", "t1", 1, ORIGEM, DUR, fade_in=_entrada(), fade_out=_saida()),
        _clip("a", "t1", 2, ORIGEM + DUR, DUR, fade_in=_entrada()),
    ]
    _, relatorio, _ = _exportar(tmp_path, clips)

    avisos = [i for i in relatorio.issues if i.code == "FADE_WRITTEN_AS_TRANSITION"]
    assert len(avisos) == 1
    assert avisos[0].severity == "info"
    assert avisos[0].context == {"track": "Dialogo", "fades": 3}


def test_fades_que_nao_cabem_no_clip_continuam_como_envelope_e_erro(tmp_path):
    clips = [_clip("a", "t1", 1, ORIGEM + 1000, 600, fade_in=_entrada(400), fade_out=_saida(400))]

    with pytest.raises(StrictModeError, match="WRITE_FADE_UNSUPPORTED"):
        _exportar(tmp_path, clips, strict=True)

    destino, relatorio, _ = _exportar(tmp_path, clips, strict=False)
    assert _forma(destino) == [("Filler", 1000), ("Clip", 600)]
    assert "WRITE_FADE_UNSUPPORTED" in _codigos(relatorio)
    assert "FADE_CONVERTED_TO_ENVELOPE" in _codigos(relatorio)
    assert "FADE_WRITTEN_AS_TRANSITION" not in _codigos(relatorio)


def test_export_sem_fade_independente_nao_ganha_componentes(tmp_path):
    clips = [
        _clip("a", "t1", 1, ORIGEM + 1000, DUR),
        _clip("a", "t1", 2, ORIGEM + 1000 + DUR + 250, DUR),
    ]
    destino, relatorio, _ = _exportar(tmp_path, clips)

    assert _forma(destino) == [("Filler", 1000), ("Clip", DUR), ("Filler", 250), ("Clip", DUR)]
    assert "FADE_WRITTEN_AS_TRANSITION" not in _codigos(relatorio)


def _track_de_tres_canais(fade_in=None):
    """Track de tres canais, com um clip por canal no mesmo trecho."""
    track = TrackIR(id="t1", source_index=1, name="LCR", kind="audio", channel_count=3)
    for canal in range(3):
        clip = _clip("c", "t1", canal, ORIGEM + 1000, DUR, fade_in=fade_in)
        clip.channel_index = canal
        clip.event_index = 1
        track.clips.append(clip)
    return track


def test_track_multicanal_grava_o_fade_em_cada_slot(tmp_path):
    destino, relatorio, _ = _exportar(tmp_path, [], track=_track_de_tres_canais(_entrada()))

    assert len(_slots_de_audio(destino)) == 3
    for slot in range(3):
        assert _forma(destino, slot) == [
            ("Filler", 1000 + FI),
            ("Transition", FI),
            ("Clip", DUR),
        ]
    avisos = [i for i in relatorio.issues if i.code == "FADE_WRITTEN_AS_TRANSITION"]
    assert [a.context for a in avisos] == [{"track": "LCR", "fades": 1}]


def test_clip_pulado_nao_quebra_o_fade_do_clip_anterior(tmp_path):
    clips = [
        _clip("a", "t1", 1, ORIGEM, DUR, fade_out=_saida()),
        _clip("a", "t1", 2, ORIGEM + DUR - 1000, 2000),
        _clip("a", "t1", 3, ORIGEM + DUR + 5000, DUR, fade_in=_entrada()),
    ]
    destino, relatorio, _ = _exportar(tmp_path, clips, strict=False)

    assert "WRITE_CLIP_SKIPPED" in _codigos(relatorio)
    assert _forma(destino) == [
        ("Clip", DUR),
        ("Transition", FO),
        ("Filler", FO + 5000 + FI),
        ("Transition", FI),
        ("Clip", DUR),
    ]
    assert _lidos(destino) == [(0, DUR, 0, FO), (DUR + 5000, 2 * DUR + 5000, FI, 0)]


def test_automacao_dentro_do_fade_mantem_o_envelope_e_o_erro(tmp_path):
    clips = [_clip("a", "t1", 1, ORIGEM + 1000, DUR, fade_in=_entrada(), fade_out=_saida())]
    track = _track("t1", "Dialogo", 1, clips)
    track.gain_points.extend([(ORIGEM + 1000 + FI // 2, 0.5), (ORIGEM + 1000 + DUR // 2, 1.0)])
    destino, relatorio, _ = _exportar(tmp_path, [], strict=False, track=track)

    assert _forma(destino) == [
        ("Filler", 1000),
        ("Clip", DUR),
        ("Transition", FO),
        ("Filler", FO),
    ]
    assert "WRITE_FADE_UNSUPPORTED" in _codigos(relatorio)
    assert "FADE_CONVERTED_TO_ENVELOPE" in _codigos(relatorio)
    avisos = [i for i in relatorio.issues if i.code == "FADE_WRITTEN_AS_TRANSITION"]
    assert [a.context for a in avisos] == [{"track": "Dialogo", "fades": 1}]


def test_automacao_fora_do_fade_nao_impede_a_transition(tmp_path):
    clips = [_clip("a", "t1", 1, ORIGEM + 1000, DUR, fade_in=_entrada())]
    track = _track("t1", "Dialogo", 1, clips)
    track.gain_points.extend([(ORIGEM + 1000 + FI, 0.5), (ORIGEM + 1000 + DUR // 2, 1.0)])
    destino, relatorio, _ = _exportar(tmp_path, [], track=track)

    assert _forma(destino) == [("Filler", 1000 + FI), ("Transition", FI), ("Clip", DUR)]
    assert "WRITE_FADE_UNSUPPORTED" not in _codigos(relatorio)
    assert _lidos(destino) == [(1000, 1000 + DUR, FI, 0)]


def test_fade_do_clip_depois_de_um_par_pulado_nao_some(tmp_path):
    """O clip do meio fica fora do AAF; o fade-in do clip seguinte, que fazia par com ele,
    passa a ser um fade a partir do silencio."""
    clips = [
        _clip("a", "t1", 1, ORIGEM, DUR),
        _clip("a", "t1", 2, ORIGEM + DUR - 1000, DUR, fade_out=_saida()),
        _clip("a", "t1", 3, ORIGEM + 2 * DUR - 1000 - FO, DUR, fade_in=_entrada(FO)),
    ]
    destino, relatorio, _ = _exportar(tmp_path, clips, strict=False)

    assert "WRITE_CLIP_SKIPPED" in _codigos(relatorio)
    assert "FADE_CONVERTED_TO_ENVELOPE" not in _codigos(relatorio)
    assert "WRITE_FADE_UNSUPPORTED" not in _codigos(relatorio)
    assert _forma(destino) == [
        ("Clip", DUR),
        ("Filler", DUR - 1000 - FO + FO),
        ("Transition", FO),
        ("Clip", DUR),
    ]
    assert _lidos(destino) == [(0, DUR, 0, 0), (2 * DUR - 1000 - FO, 3 * DUR - 1000 - FO, FO, 0)]
    avisos = [i for i in relatorio.issues if i.code == "FADE_WRITTEN_AS_TRANSITION"]
    assert [a.context for a in avisos] == [{"track": "Dialogo", "fades": 1}]


def test_fade_do_clip_depois_de_um_crossfade_recusado_pela_automacao_nao_some(tmp_path):
    """O crossfade que conflita com a automacao deixa o clip do meio fora do AAF; o fade-in
    do clip seguinte e gravado, e o fade-out do primeiro, com automacao dentro, e erro."""
    curto = 2000
    clips = [
        _clip("a", "t1", 1, ORIGEM, curto, fade_out=_saida(500)),
        _clip("a", "t1", 2, ORIGEM + 1500, curto, fade_in=_entrada(500), fade_out=_saida(500)),
        _clip("a", "t1", 3, ORIGEM + 3000, curto, fade_in=_entrada(500)),
    ]
    track = _track("t1", "Dialogo", 1, clips)
    track.gain_points.extend([(ORIGEM + 1600, 0.5)])
    destino, relatorio, _ = _exportar(tmp_path, [], strict=False, track=track)

    codigos = _codigos(relatorio)
    assert "WRITE_TRANSITION_SEGMENTED_AUTOMATION_UNSUPPORTED" in codigos
    recusados = [i.context["clip"] for i in relatorio.issues if i.code == "WRITE_FADE_UNSUPPORTED"]
    assert recusados == ["a1.wav"]
    assert _forma(destino) == [
        ("Clip", curto),
        ("Filler", 1000 + 500),
        ("Transition", 500),
        ("Clip", curto),
    ]
    assert _lidos(destino) == [(0, curto, 0, 0), (3000, 3000 + curto, 500, 0)]
    avisos = [i for i in relatorio.issues if i.code == "FADE_WRITTEN_AS_TRANSITION"]
    assert [a.context for a in avisos] == [{"track": "Dialogo", "fades": 1}]


def test_fade_do_clip_depois_de_um_clip_anterior_ao_inicio_da_sessao_nao_some(tmp_path):
    """O clip que comeca antes do inicio da sessao fica fora do AAF; o fade-in do clip
    seguinte, que fazia par com ele, passa a ser um fade a partir do silencio."""
    clips = [
        _clip("a", "t1", 1, ORIGEM - 500, DUR, fade_out=_saida(FO)),
        _clip("a", "t1", 2, ORIGEM - 500 + DUR - FO, DUR, fade_in=_entrada(FO)),
    ]
    destino, relatorio, _ = _exportar(tmp_path, clips, strict=False)

    assert "WRITE_CLIP_SKIPPED" in _codigos(relatorio)
    assert "FADE_CONVERTED_TO_ENVELOPE" not in _codigos(relatorio)
    assert _forma(destino) == [
        ("Filler", DUR - 500 - FO + FO),
        ("Transition", FO),
        ("Clip", DUR),
    ]
    assert _lidos(destino) == [(DUR - 500 - FO, 2 * DUR - 500 - FO, FO, 0)]
    avisos = [i for i in relatorio.issues if i.code == "FADE_WRITTEN_AS_TRANSITION"]
    assert [a.context for a in avisos] == [{"track": "Dialogo", "fades": 1}]


def test_fade_do_clip_depois_de_dois_clips_pulados_em_fila_nao_some(tmp_path):
    """Dois clips seguidos ficam fora do AAF; o fade-in do clip seguinte, que fazia par com
    o segundo deles, passa a ser um fade a partir do silencio."""
    clips = [
        _clip("a", "t1", 1, ORIGEM, DUR),
        _clip("a", "t1", 2, ORIGEM + DUR - 1000, DUR),
        _clip("a", "t1", 3, ORIGEM + DUR - 500, DUR, fade_out=_saida()),
        _clip("a", "t1", 4, ORIGEM + 2 * DUR - 500 - FO, DUR, fade_in=_entrada(FO)),
    ]
    destino, relatorio, _ = _exportar(tmp_path, clips, strict=False)

    pulados = [i for i in relatorio.issues if i.code == "WRITE_CLIP_SKIPPED"]
    assert len(pulados) == 2
    assert "FADE_CONVERTED_TO_ENVELOPE" not in _codigos(relatorio)
    assert "WRITE_FADE_UNSUPPORTED" not in _codigos(relatorio)
    assert _forma(destino) == [
        ("Clip", DUR),
        ("Filler", DUR - 500 - FO + FO),
        ("Transition", FO),
        ("Clip", DUR),
    ]
    assert _lidos(destino) == [(0, DUR, 0, 0), (2 * DUR - 500 - FO, 3 * DUR - 500 - FO, FO, 0)]
    avisos = [i for i in relatorio.issues if i.code == "FADE_WRITTEN_AS_TRANSITION"]
    assert [a.context for a in avisos] == [{"track": "Dialogo", "fades": 1}]


def test_crossfade_por_cima_de_um_clip_pulado_e_gravado_uma_vez(tmp_path):
    """O clip que cabe inteiro dentro do anterior fica fora do AAF; o crossfade do anterior
    com o clip seguinte e o par que o writer grava, e os dois fades sao so essa Transition."""
    clips = [
        _clip("a", "t1", 1, ORIGEM, DUR, fade_out=_saida(FO)),
        _clip("a", "t1", 2, ORIGEM + 1000, 1000),
        _clip("a", "t1", 3, ORIGEM + DUR - FO, DUR, fade_in=_entrada(FO)),
    ]
    destino, relatorio, _ = _exportar(tmp_path, clips, strict=False)

    codigos = _codigos(relatorio)
    assert "WRITE_CLIP_SKIPPED" in codigos
    assert "FADE_CONVERTED_TO_ENVELOPE" not in codigos
    assert "WRITE_FADE_UNSUPPORTED" not in codigos
    assert "FADE_WRITTEN_AS_TRANSITION" not in codigos
    assert _forma(destino) == [("Clip", DUR), ("Transition", FO), ("Clip", DUR)]
    assert _lidos(destino) == [(0, DUR, 0, FO), (DUR - FO, 2 * DUR - FO, FO, 0)]
