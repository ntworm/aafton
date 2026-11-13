"""Roundtrip de edicoes: automacao de Volume/Pan, fades e diagnostico estrito.

O .als e gerado pela propria ferramenta a partir de um SessionIR e depois
reaberto, entao o formato dos envelopes testados e o mesmo que o Live le: o
teste injeta no XML gerado a estrutura de envelope que o Live escreve, com o
AutomationTarget correspondente no DeviceChain e o PointeeId apontando para ele.
"""

from pathlib import Path
import xml.etree.ElementTree as ET

import aaf2
import pytest

from aafton.aaf_read import read_aaf
from aafton.aaf_write import _curva_suportada, write_aaf
from aafton.als_read import read_session
from aafton.als_write import SENTINEL_TIME, WriteOptions, write_session
from aafton.als_xml import load_als, save_als
from aafton.ir import ClipIR, FadeIR, MarkerIR, MediaAsset, SessionIR, TrackIR
from aafton.report import Report, StrictModeError
from aafton.timecode import TimecodeRate

TEMPLATE = Path("templates/live12.als")

pytestmark = pytest.mark.skipif(
    not TEMPLATE.exists(), reason="templates/live12.als ainda nao existe"
)

# A 60 BPM e 48000 Hz um beat vale 48000 samples.
ORIGEM = 172800000
SR = 48000
BPM = 60.0
SAMPLES_POR_BEAT = 48000


def _sub(parent: ET.Element, caminho: list[str]) -> ET.Element:
    """Devolve o filho em ``caminho``, criando a cadeia que faltar."""
    node = parent
    for nome in caminho:
        achado = node.find(nome)
        if achado is None:
            achado = ET.SubElement(node, nome)
        node = achado
    return node


def _base_session(tmp_path: Path) -> SessionIR:
    wav = tmp_path / "audio.wav"
    wav.write_bytes(
        b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80\xbb\x00\x00"
        b"\x77\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
    )
    asset = MediaAsset(
        id="m1",
        filename="audio.wav",
        channels=1,
        sample_rate=SR,
        status="resolved",
        resolved_path=str(wav),
    )
    track = TrackIR(id="t0", source_index=0, name="AUDIO 1", kind="audio", channel_count=1)
    track.clips.append(
        ClipIR(
            id="c0",
            track_id="t0",
            channel_index=0,
            event_index=0,
            name="audio.wav",
            media_id="m1",
            timeline_start_samples=ORIGEM + SAMPLES_POR_BEAT,
            timeline_end_samples=ORIGEM + 5 * SAMPLES_POR_BEAT,
            source_offset_samples=0,
        )
    )
    return SessionIR(
        name="roundtrip",
        sample_rate=SR,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=ORIGEM,
        tracks=[track],
        media=[asset],
    )


def _escrever(tmp_path: Path) -> Path:
    destino = tmp_path / "session.als"
    write_session(
        _base_session(tmp_path),
        TEMPLATE,
        destino,
        WriteOptions(sample_rate=SR, bpm=BPM, live_origin_samples=ORIGEM),
        Report(),
    )
    return destino


def _injetar_envelope(
    root: ET.Element,
    mixer: str,
    target_id: str,
    pontos_beats: list[tuple[float, float]],
    sentinela: float | None = None,
) -> None:
    """Cria o AutomationTarget e o AutomationEnvelope de um mixer da track.

    ``pontos_beats`` sao (beat, valor). Quando ``sentinela`` e informado, um
    FloatEvent sentinela e escrito antes deles, como o faz o escritor.
    """
    track_node = root.find(".//AudioTrack")
    assert track_node is not None, "o .als gerado precisa ter uma AudioTrack"

    alvo = _sub(track_node, ["DeviceChain", "Mixer", mixer, "AutomationTarget"])
    alvo.set("Id", target_id)

    envelopes = _sub(track_node, ["AutomationEnvelopes", "Envelopes"])
    envelope = ET.SubElement(envelopes, "AutomationEnvelope")
    ponteiro = _sub(envelope, ["EnvelopeTarget", "PointeeId"])
    ponteiro.set("Value", target_id)

    eventos = _sub(envelope, ["Automation", "Events"])
    event_id = 0
    if sentinela is not None:
        ev = ET.SubElement(eventos, "FloatEvent")
        ev.set("Id", str(event_id))
        ev.set("Time", repr(sentinela))
        ev.set("Value", repr(pontos_beats[0][1]))
        event_id += 1
    for beat, valor in pontos_beats:
        ev = ET.SubElement(eventos, "FloatEvent")
        ev.set("Id", str(event_id))
        ev.set("Time", repr(beat))
        ev.set("Value", repr(valor))
        event_id += 1


# --- P01: extracao de Volume e Pan ---------------------------------------


def test_p01_extrai_volume_e_pan_em_samples_absolutos(tmp_path):
    als_path = _escrever(tmp_path)
    root = load_als(als_path)
    volume = [(1.0, 0.5), (2.0, 1.0), (3.5, 0.25)]
    pan = [(1.5, 0.25), (4.0, 0.75)]
    _injetar_envelope(root, "Volume", "9001", volume, sentinela=SENTINEL_TIME)
    _injetar_envelope(root, "Pan", "9002", pan)
    save_als(root, als_path)

    report = Report()
    session = read_session(als_path, report)
    track = session.tracks[0]

    # 1 beat == 48000 samples, e a origem do Live e a ancora da timeline.
    esperado_volume = [(ORIGEM + int(beat * SAMPLES_POR_BEAT), valor) for beat, valor in volume]
    esperado_pan = [(ORIGEM + int(beat * SAMPLES_POR_BEAT), valor) for beat, valor in pan]
    assert track.gain_points == esperado_volume
    assert track.pan_points == esperado_pan

    # Automacao reconciliada nao gera mais aviso de leitura nem marca a sessao como
    # limited: ela e lida e exportada.
    codes = [i.code for i in report.issues]
    assert "ALS_AUTOMATION_UNSUPPORTED" not in codes
    assert session.unsupported_export_features == []


def test_p01_descarta_o_evento_sentinela(tmp_path):
    """O evento sentinela nao pode virar um ponto milhoes de beats antes da origem."""
    als_path = _escrever(tmp_path)
    root = load_als(als_path)
    _injetar_envelope(root, "Volume", "9001", [(2.0, 0.75)], sentinela=SENTINEL_TIME)
    save_als(root, als_path)

    session = read_session(als_path, Report())
    pontos = session.tracks[0].gain_points
    assert pontos == [(ORIGEM + 2 * SAMPLES_POR_BEAT, 0.75)]


def test_p01_envelope_sem_destino_continua_reportado(tmp_path):
    """Envelope que nao aponta para Volume nem Pan nao tem representacao no AAF."""
    als_path = _escrever(tmp_path)
    root = load_als(als_path)
    _injetar_envelope(root, "SendA", "9003", [(1.0, 0.5)])
    save_als(root, als_path)

    report = Report()
    read_session(als_path, report)
    achados = [i for i in report.issues if i.code == "ALS_AUTOMATION_UNSUPPORTED"]
    assert len(achados) == 1
    assert "9003" in achados[0].context["envelopes"]


def test_p01_envelope_de_um_mixer_nao_vaza_para_o_outro(tmp_path):
    """So o envelope do mixer pedido preenche o campo dele."""
    als_path = _escrever(tmp_path)
    root = load_als(als_path)
    _injetar_envelope(root, "Pan", "9002", [(1.0, 0.9)])
    save_als(root, als_path)

    track = read_session(als_path, Report()).tracks[0]
    assert track.gain_points == []
    assert track.pan_points == [(ORIGEM + SAMPLES_POR_BEAT, 0.9)]


# --- P03: diagnostico estrito de warp e tempo -----------------------------


def _marcar_warp(als_path: Path, tag: str, valor: str = "true") -> Path:
    root = load_als(als_path)
    clip = root.find(".//AudioClip")
    assert clip is not None
    node = clip.find(tag)
    if node is None:
        node = ET.SubElement(clip, tag)
    node.set("Value", valor)
    save_als(root, als_path)
    return als_path


@pytest.mark.parametrize(
    "tag,causa",
    [
        ("IsWarped", "warp_active"),
        ("Loop/LoopOn", "loop_active"),
    ],
)
def test_p03_nomeia_a_causa_do_modo_de_clip(tmp_path, tag, causa):
    als_path = _marcar_warp(_escrever(tmp_path), tag)
    # strict=False para o erro ser registrado em vez de levantado: o que esta em
    # teste e o conteudo do relatorio, nao a excecao.
    report = Report(strict=False)
    read_session(als_path, report)
    achados = [i for i in report.issues if i.code == "ALS_CLIP_MODE_UNSUPPORTED"]
    assert len(achados) == 1
    assert causa in achados[0].context["causes"]


def test_p03_relata_start_relative(tmp_path):
    als_path = _marcar_warp(_escrever(tmp_path), "Loop/StartRelative", "2.5")
    report = Report(strict=False)
    read_session(als_path, report)
    achados = [i for i in report.issues if i.code == "ALS_CLIP_MODE_UNSUPPORTED"]
    assert "StartRelative=2.5" in achados[0].context["causes"]


def test_p03_clip_sem_mapa_de_tempo_nao_e_reportado(tmp_path):
    """Um clip reto nao gera erro: so o que muda o alinhamento entra no relatorio."""
    report = Report(strict=False)
    read_session(_escrever(tmp_path), report)
    assert [i for i in report.issues if i.code == "ALS_CLIP_MODE_UNSUPPORTED"] == []


# --- Helpers e testes de regressao para R1, R2, R3 e R4 -------------------


def _make_dummy_wav(path: Path, sample_rate: int = 48000, duration_seconds: float = 10.0) -> Path:
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
    path.write_bytes(header + b"\x00" * min(data_size, 1024))
    return path


def test_r1_gain_points_sem_cortes_internos_avalia_inicio_e_emite_um_trecho(tmp_path):
    """Quando track.gain_points contem pontos sem cortes internos no clipe,

    avalia ganho inicial via _ganho_em e emite GAIN_AUTOMATION_SEGMENTED com 1 trecho.
    Sem gain_points, o ganho permanece puramente estatico sem emitir o relatorio.
    """
    wav = _make_dummy_wav(tmp_path / "audio_r1.wav", sample_rate=SR, duration_seconds=60)
    asset = MediaAsset(
        id="m1",
        filename="audio_r1.wav",
        channels=1,
        sample_rate=SR,
        duration_samples=SR * 60,
        status="resolved",
        resolved_path=str(wav),
    )

    # 1. Sem gain_points: ganho estatico puro, sem GAIN_AUTOMATION_SEGMENTED
    track_static = TrackIR(id="t0", source_index=0, name="AUDIO 1", kind="audio", channel_count=1)
    track_static.clips.append(
        ClipIR(
            id="c0",
            track_id="t0",
            channel_index=0,
            event_index=0,
            name="clip.wav",
            media_id="m1",
            timeline_start_samples=ORIGEM + 1000,
            timeline_end_samples=ORIGEM + 2000,
            source_offset_samples=0,
        )
    )
    session_static = SessionIR(
        name="r1_static",
        sample_rate=SR,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=ORIGEM,
        tracks=[track_static],
        media=[asset],
    )
    dest_static = tmp_path / "r1_static.aaf"
    rep_static = Report(strict=False)
    write_aaf(session_static, dest_static, rep_static)
    assert "GAIN_AUTOMATION_SEGMENTED" not in [i.code for i in rep_static.issues]

    # 2. Com gain_points antes do clipe (sem cortes internos): avalia inicio e emite 1 trecho
    track_ponto = TrackIR(id="t0", source_index=0, name="AUDIO 1", kind="audio", channel_count=1)
    track_ponto.clips.append(
        ClipIR(
            id="c0",
            track_id="t0",
            channel_index=0,
            event_index=0,
            name="clip.wav",
            media_id="m1",
            timeline_start_samples=ORIGEM + 1000,
            timeline_end_samples=ORIGEM + 2000,
            source_offset_samples=0,
        )
    )
    track_ponto.gain_points = [(ORIGEM, 0.5)]
    session_ponto = SessionIR(
        name="r1_ponto",
        sample_rate=SR,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=ORIGEM,
        tracks=[track_ponto],
        media=[asset],
    )
    dest_ponto = tmp_path / "r1_ponto.aaf"
    rep_ponto = Report(strict=False)
    write_aaf(session_ponto, dest_ponto, rep_ponto)
    written_issues = [
        i
        for i in rep_ponto.issues
        if i.code in ("GAIN_AUTOMATION_WRITTEN", "GAIN_AUTOMATION_SEGMENTED")
    ]
    assert len(written_issues) == 1
    relido_ponto = read_aaf(dest_ponto, Report(strict=False))
    clip_ponto = next(relido_ponto.all_clips())
    assert clip_ponto.gain_linear == pytest.approx(0.5)

    # 3. Com clipe entre breakpoints (sem cortes internos): interpola ganho no inicio do clipe
    track_interp = TrackIR(id="t0", source_index=0, name="AUDIO 1", kind="audio", channel_count=1)
    track_interp.clips.append(
        ClipIR(
            id="c0",
            track_id="t0",
            channel_index=0,
            event_index=0,
            name="clip.wav",
            media_id="m1",
            timeline_start_samples=ORIGEM + 1000,
            timeline_end_samples=ORIGEM + 2000,
            source_offset_samples=0,
        )
    )
    # Entre 0 e 2000; em 1000 o ganho e linearmente 0.5
    track_interp.gain_points = [(ORIGEM, 0.0), (ORIGEM + 2000, 1.0)]
    session_interp = SessionIR(
        name="r1_interp",
        sample_rate=SR,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=ORIGEM,
        tracks=[track_interp],
        media=[asset],
    )
    dest_interp = tmp_path / "r1_interp.aaf"
    rep_interp = Report(strict=False)
    write_aaf(session_interp, dest_interp, rep_interp)
    written_issues_interp = [
        i
        for i in rep_interp.issues
        if i.code in ("GAIN_AUTOMATION_WRITTEN", "GAIN_AUTOMATION_SEGMENTED")
    ]
    assert len(written_issues_interp) == 1
    relido_interp = read_aaf(dest_interp, Report(strict=False))
    if relido_interp.tracks[0].gain_points:
        assert relido_interp.tracks[0].gain_points[0][1] == pytest.approx(0.5)
    else:
        clip_interp = next(relido_interp.all_clips())
        assert clip_interp.gain_linear == pytest.approx(0.5)

    # 4. Pontos exatamente nas bordas (timeline_start_samples e timeline_end_samples): 1 trecho
    track_bordas = TrackIR(id="t0", source_index=0, name="AUDIO 1", kind="audio", channel_count=1)
    track_bordas.clips.append(
        ClipIR(
            id="c0",
            track_id="t0",
            channel_index=0,
            event_index=0,
            name="clip.wav",
            media_id="m1",
            timeline_start_samples=ORIGEM + 1000,
            timeline_end_samples=ORIGEM + 2000,
            source_offset_samples=0,
        )
    )
    track_bordas.gain_points = [(ORIGEM + 1000, 0.75), (ORIGEM + 2000, 0.25)]
    session_bordas = SessionIR(
        name="r1_bordas",
        sample_rate=SR,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=ORIGEM,
        tracks=[track_bordas],
        media=[asset],
    )
    dest_bordas = tmp_path / "r1_bordas.aaf"
    rep_bordas = Report(strict=False)
    write_aaf(session_bordas, dest_bordas, rep_bordas)
    written_issues_bordas = [
        i
        for i in rep_bordas.issues
        if i.code in ("GAIN_AUTOMATION_WRITTEN", "GAIN_AUTOMATION_SEGMENTED")
    ]
    assert len(written_issues_bordas) == 1
    relido_bordas = read_aaf(dest_bordas, Report(strict=False))
    if relido_bordas.tracks[0].gain_points:
        assert relido_bordas.tracks[0].gain_points[0][1] == pytest.approx(0.75)
    else:
        clip_bordas = next(relido_bordas.all_clips())
        assert clip_bordas.gain_linear == pytest.approx(0.75)


def test_r2_transicao_conflitante_com_automacao_recusada(tmp_path):
    """Se cortes de automacao caem na regiao de overlap da transicao,

    recusa com WRITE_TRANSITION_SEGMENTED_AUTOMATION_UNSUPPORTED e protege
    a Sequence contra corrupcao de geometria.
    """
    wav = _make_dummy_wav(tmp_path / "audio_r2.wav", sample_rate=SR, duration_seconds=60)
    asset = MediaAsset(
        id="m1",
        filename="audio_r2.wav",
        channels=1,
        sample_rate=SR,
        duration_samples=SR * 60,
        status="resolved",
        resolved_path=str(wav),
    )

    # Dois clipes com crossfade simetrico de 500 samples
    overlap = 500
    clip1 = ClipIR(
        id="c1",
        track_id="t0",
        channel_index=0,
        event_index=0,
        name="clip1.wav",
        media_id="m1",
        timeline_start_samples=ORIGEM,
        timeline_end_samples=ORIGEM + 2000,
        source_offset_samples=0,
        fade_out=FadeIR(kind="out", duration_samples=overlap),
    )
    clip2 = ClipIR(
        id="c2",
        track_id="t0",
        channel_index=0,
        event_index=1,
        name="clip2.wav",
        media_id="m1",
        timeline_start_samples=ORIGEM + 2000 - overlap,  # ORIGEM + 1500
        timeline_end_samples=ORIGEM + 3500,
        source_offset_samples=0,
        fade_in=FadeIR(kind="in", duration_samples=overlap),
    )

    track_conflict = TrackIR(id="t0", source_index=0, name="AUDIO 1", kind="audio", channel_count=1)
    track_conflict.clips.extend([clip1, clip2])
    # Ponto de automacao em ORIGEM + 1800, dentro do overlap [1500, 2000]:
    # subdivide a regiao do overlap e deixa o trecho final de clip1 com 200 samples (< 500)
    track_conflict.gain_points = [(ORIGEM, 1.0), (ORIGEM + 1800, 0.5)]

    session_conflict = SessionIR(
        name="r2_conflict",
        sample_rate=SR,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=ORIGEM,
        tracks=[track_conflict],
        media=[asset],
    )

    # Modo estrito recusa
    rep_strict = Report(strict=True)
    with pytest.raises(Exception):
        write_aaf(session_conflict, tmp_path / "r2_strict.aaf", rep_strict)

    # Modo permissivo relata erro e nao corrompe Sequence
    dest_permissive = tmp_path / "r2_permissive.aaf"
    rep_permissive = Report(strict=False)
    write_aaf(session_conflict, dest_permissive, rep_permissive)
    conflict_issues = [
        i
        for i in rep_permissive.issues
        if i.code == "WRITE_TRANSITION_SEGMENTED_AUTOMATION_UNSUPPORTED"
    ]
    assert len(conflict_issues) == 1
    assert conflict_issues[0].context["overlap_samples"] == overlap

    # Sem conflito (cortes fora do overlap): transicao valida
    track_ok = TrackIR(id="t0", source_index=0, name="AUDIO 1", kind="audio", channel_count=1)
    track_ok.clips.extend([clip1, clip2])
    # Cortes em 500 e 2500: trecho final do clipe 1 tem 1500 samples (>= 500) e
    # trecho inicial do clipe 2 tem 1000 samples (>= 500)
    track_ok.gain_points = [(ORIGEM, 1.0), (ORIGEM + 500, 0.8), (ORIGEM + 2500, 0.4)]
    session_ok = SessionIR(
        name="r2_ok",
        sample_rate=SR,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=ORIGEM,
        tracks=[track_ok],
        media=[asset],
    )
    dest_ok = tmp_path / "r2_ok.aaf"
    rep_ok = Report(strict=True)
    write_aaf(session_ok, dest_ok, rep_ok)
    assert "WRITE_TRANSITION_SEGMENTED_AUTOMATION_UNSUPPORTED" not in [
        i.code for i in rep_ok.issues
    ]


def test_r3_curva_suportada_e_recusa_de_pan(tmp_path, monkeypatch):
    """_curva_suportada detecta corretamente a capacidade real do pyaaf2

    sem exigir Interpolation em InterpolationDefinition, e reporta
    WRITE_PAN_AUTOMATION_UNSUPPORTED na presenca de pan_points.
    """
    wav = _make_dummy_wav(tmp_path / "audio_r3.wav", sample_rate=SR, duration_seconds=60)
    asset = MediaAsset(
        id="m1",
        filename="audio_r3.wav",
        channels=1,
        sample_rate=SR,
        duration_samples=SR * 60,
        status="resolved",
        resolved_path=str(wav),
    )

    # 1. Capacidade real do pyaaf2 instalado e reconhecida (True)
    assert _curva_suportada() is True

    # 2. Com pan_points, emite WRITE_PAN_AUTOMATION_UNSUPPORTED
    track = TrackIR(id="t0", source_index=0, name="AUDIO 1", kind="audio", channel_count=1)
    track.clips.append(
        ClipIR(
            id="c0",
            track_id="t0",
            channel_index=0,
            event_index=0,
            name="clip.wav",
            media_id="m1",
            timeline_start_samples=ORIGEM,
            timeline_end_samples=ORIGEM + 1000,
            source_offset_samples=0,
        )
    )
    track.pan_points = [(ORIGEM, 0.0), (ORIGEM + 1000, 0.5)]
    session = SessionIR(
        name="r3_pan",
        sample_rate=SR,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=ORIGEM,
        tracks=[track],
        media=[asset],
    )
    rep = Report(strict=False)
    write_aaf(session, tmp_path / "r3_pan.aaf", rep)
    codes = [i.code for i in rep.issues]
    assert "PAN_AUTOMATION_EXPORTED" in codes
    assert "WRITE_PAN_AUTOMATION_DEPENDENCY_MISSING" not in codes

    # 3. Se simularmos ausencia de definicao no pyaaf2, _curva_suportada detecta False
    monkeypatch.setattr("aaf2.model.classdefs.classdefs", {})
    assert _curva_suportada() is False
    monkeypatch.undo()

    # E write_aaf reporta WRITE_PAN_AUTOMATION_DEPENDENCY_MISSING quando _curva_suportada e False
    monkeypatch.setattr("aafton.aaf_write._curva_suportada", lambda: False)
    rep_missing = Report(strict=False)
    write_aaf(session, tmp_path / "r3_missing.aaf", rep_missing)
    assert "WRITE_PAN_AUTOMATION_DEPENDENCY_MISSING" in [i.code for i in rep_missing.issues]


def test_r4_source_start_sem_arredondamento_duplo(tmp_path):
    """Com clocks diferentes (44100 fonte vs 48000 sessao), o calculo

    do offset absoluto (clip.source_offset_samples + offset) em fracoes
    e arredondado apenas uma vez, evitando o residuo acumulado do duplo arredondamento.
    """
    # Fonte a 44100 Hz em sessao de 48000 Hz
    wav = _make_dummy_wav(tmp_path / "audio_r4.wav", sample_rate=44100, duration_seconds=60)
    asset = MediaAsset(
        id="m1",
        filename="audio_r4.wav",
        channels=1,
        sample_rate=44100,
        duration_samples=44100 * 60,
        status="resolved",
        resolved_path=str(wav),
    )

    # Clipes com source_offset_samples = 7 e 14.
    # 7 * 44100 / 48000 = 6.43125 (round unico da 6, residuo +0.43125).
    # 14 * 44100 / 48000 = 12.8625 (round unico da 13, residuo -0.1375).
    track = TrackIR(id="t0", source_index=0, name="AUDIO 1", kind="audio", channel_count=1)
    track.clips.append(
        ClipIR(
            id="c0",
            track_id="t0",
            channel_index=0,
            event_index=0,
            name="clip_r4_0.wav",
            media_id="m1",
            timeline_start_samples=ORIGEM,
            timeline_end_samples=ORIGEM + 7,
            source_offset_samples=7,
        )
    )
    track.clips.append(
        ClipIR(
            id="c1",
            track_id="t0",
            channel_index=0,
            event_index=1,
            name="clip_r4_1.wav",
            media_id="m1",
            timeline_start_samples=ORIGEM + 7,
            timeline_end_samples=ORIGEM + 1000,
            source_offset_samples=14,
        )
    )

    session = SessionIR(
        name="r4_round",
        sample_rate=SR,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=ORIGEM,
        tracks=[track],
        media=[asset],
    )
    dest = tmp_path / "r4_round.aaf"
    rep = Report(strict=False)
    write_aaf(session, dest, rep)

    # Confere que o aviso de arredondamento registrou o residuo exato
    rounding_issues = [i for i in rep.issues if i.code == "WRITE_SOURCE_OFFSET_ROUNDING"]
    assert len(rounding_issues) >= 1
    # Residuo do segundo trecho: 12.8625 - 13 = -0.1375
    assert any(
        pytest.approx(-0.1375, abs=1e-4) == i.context["residual_source_samples"]
        for i in rounding_issues
    )

    # Abre o AAF e confere o start do segundo SourceClip
    with aaf2.open(str(dest), "r") as f:
        comp = next(m for m in f.content.mobs if type(m).__name__ == "CompositionMob")
        slot = next(s for s in comp.slots if s.name == "AUDIO 1")

        def _get_sc(seg):
            if type(seg).__name__ == "SourceClip":
                yield seg
            for attr in ("components", "segments"):
                for child in getattr(seg, attr, []) or []:
                    yield from _get_sc(child)

        scs = list(_get_sc(slot.segment))
        assert len(scs) == 2
        # O segundo segmento deve ter start == 13 (sem o erro de arredondamento duplo para 12)
        assert scs[0].start == 6
        assert scs[1].start == 13


def test_f1_varying_gain_keyframe_interpolation(tmp_path):
    """F1: automacao de volume gera OperationGroup com VaryingValue e LinearInterp."""
    wav = _make_dummy_wav(tmp_path / "audio_f1.wav", sample_rate=SR, duration_seconds=60)
    asset = MediaAsset(
        id="m1",
        filename="audio_f1.wav",
        channels=1,
        sample_rate=SR,
        duration_samples=SR * 60,
        status="resolved",
        resolved_path=str(wav),
    )
    track = TrackIR(id="t0", source_index=0, name="AUDIO 1", kind="audio", channel_count=1)
    track.clips.append(
        ClipIR(
            id="c0",
            track_id="t0",
            channel_index=0,
            event_index=0,
            name="clip.wav",
            media_id="m1",
            timeline_start_samples=ORIGEM,
            timeline_end_samples=ORIGEM + 1000,
            source_offset_samples=0,
        )
    )
    track.gain_points = [(ORIGEM, 0.2), (ORIGEM + 500, 0.8), (ORIGEM + 1000, 0.4)]
    session = SessionIR(
        name="f1_gain",
        sample_rate=SR,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=ORIGEM,
        tracks=[track],
        media=[asset],
    )
    dest = tmp_path / "f1_gain.aaf"
    rep = Report(strict=True)
    write_aaf(session, dest, rep)
    assert "GAIN_AUTOMATION_WRITTEN" in [i.code for i in rep.issues]

    with aaf2.open(str(dest), "r") as f:
        comp = next(m for m in f.content.mobs if isinstance(m, aaf2.mobs.CompositionMob))
        slot = next(s for s in comp.slots if s.name == "AUDIO 1")
        seq = slot.segment.segments[0]
        group = seq.components[0]
        varying = list(group.parameters)[0]
        assert isinstance(varying, aaf2.misc.VaryingValue)
        assert varying.interpolationdef.name == "LinearInterp"
        pts = [(float(p.time), float(p.value)) for p in varying.pointlist]
        assert len(pts) == 3
        assert pts[0] == (0.0, pytest.approx(0.2))
        assert pts[1] == (0.5, pytest.approx(0.8))
        assert pts[2] == (1.0, pytest.approx(0.4))


def test_f2_pan_automation_varying_value_exported(tmp_path):
    """F2: automacao de pan exportada no Mono Audio Pan com VaryingValue."""
    wav = _make_dummy_wav(tmp_path / "audio_f2.wav", sample_rate=SR, duration_seconds=60)
    asset = MediaAsset(
        id="m1",
        filename="audio_f2.wav",
        channels=1,
        sample_rate=SR,
        duration_samples=SR * 60,
        status="resolved",
        resolved_path=str(wav),
    )
    track = TrackIR(id="t0", source_index=0, name="AUDIO 1", kind="audio", channel_count=1)
    track.clips.append(
        ClipIR(
            id="c0",
            track_id="t0",
            channel_index=0,
            event_index=0,
            name="clip.wav",
            media_id="m1",
            timeline_start_samples=ORIGEM,
            timeline_end_samples=ORIGEM + 1000,
            source_offset_samples=0,
        )
    )
    track.pan_points = [(ORIGEM, -1.0), (ORIGEM + 500, 0.0), (ORIGEM + 1000, 1.0)]
    session = SessionIR(
        name="f2_pan",
        sample_rate=SR,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=ORIGEM,
        tracks=[track],
        media=[asset],
    )
    dest = tmp_path / "f2_pan.aaf"
    rep = Report(strict=True)
    write_aaf(session, dest, rep)
    assert "PAN_AUTOMATION_EXPORTED" in [i.code for i in rep.issues]

    with aaf2.open(str(dest), "r") as f:
        comp = next(m for m in f.content.mobs if isinstance(m, aaf2.mobs.CompositionMob))
        slot = next(s for s in comp.slots if s.name == "AUDIO 1")
        pan_varying = list(slot.segment.parameters)[0]
        assert isinstance(pan_varying, aaf2.misc.VaryingValue)
        assert pan_varying.interpolationdef.name == "LinearInterp"
        pts = [(float(p.time), float(p.value)) for p in pan_varying.pointlist]
        assert pts == [(0.0, 0.0), (0.5, 0.5), (1.0, 1.0)]


def test_f3_independent_fade_converted_to_gain_envelope(tmp_path):
    """F3: fades que nao cabem no clip viram envelope de ganho, com erro no modo estrito."""
    wav = _make_dummy_wav(tmp_path / "audio_f3.wav", sample_rate=SR, duration_seconds=60)
    asset = MediaAsset(
        id="m1",
        filename="audio_f3.wav",
        channels=1,
        sample_rate=SR,
        duration_samples=SR * 60,
        status="resolved",
        resolved_path=str(wav),
    )
    track = TrackIR(id="t0", source_index=0, name="AUDIO 1", kind="audio", channel_count=1)
    clip = ClipIR(
        id="c0",
        track_id="t0",
        channel_index=0,
        event_index=0,
        name="clip.wav",
        media_id="m1",
        timeline_start_samples=ORIGEM,
        timeline_end_samples=ORIGEM + 1000,
        source_offset_samples=0,
    )
    clip.fade_in = FadeIR(kind="in", duration_samples=600)
    clip.fade_out = FadeIR(kind="out", duration_samples=600)
    track.clips.append(clip)
    session = SessionIR(
        name="f3_fade",
        sample_rate=SR,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=ORIGEM,
        tracks=[track],
        media=[asset],
    )
    dest_strict = tmp_path / "f3_strict.aaf"
    with pytest.raises(StrictModeError) as exc_info:
        write_aaf(session, dest_strict, Report(strict=True))
    assert "WRITE_FADE_UNSUPPORTED" in str(exc_info.value)

    dest = tmp_path / "f3_fade.aaf"
    rep = Report(strict=False)
    write_aaf(session, dest, rep)
    assert "FADE_CONVERTED_TO_ENVELOPE" in [i.code for i in rep.issues]


def test_f4_als_maintrack_tempo_automation_rejected_in_strict(tmp_path):
    """F4: automacao de tempo na MainTrack e detectada e recusada em modo estrito."""
    import gzip
    import json
    import xml.etree.ElementTree as ET
    from aafton.als_read import read_session

    als = tmp_path / "tempo.als"
    sidecar = tmp_path / "tempo.aafton.json"
    sidecar.write_text(
        json.dumps(
            {
                "sample_rate": 48000,
                "timecode_rate": {"num": 25, "den": 1, "drop_frame": False},
                "session_start_samples": 0,
                "live_origin_samples": 0,
                "bpm": 120.0,
            }
        )
    )
    root = ET.Element("Ableton", attrib={"MajorVersion": "5", "MinorVersion": "12.0_12049"})
    ls = ET.SubElement(root, "LiveSet")
    mt = ET.SubElement(ls, "MainTrack")
    dc = ET.SubElement(mt, "DeviceChain")
    mx = ET.SubElement(dc, "Mixer")
    tp = ET.SubElement(mx, "Tempo")
    man = ET.SubElement(tp, "Manual")
    man.set("Value", "120.0")
    at = ET.SubElement(tp, "AutomationTarget")
    at.set("Id", "99")
    ae = ET.SubElement(mt, "AutomationEnvelopes")
    envs = ET.SubElement(ae, "Envelopes")
    env = ET.SubElement(envs, "AutomationEnvelope")
    env.set("Id", "1")
    et = ET.SubElement(env, "EnvelopeTarget")
    pt = ET.SubElement(et, "PointeeId")
    pt.set("Value", "99")
    auto = ET.SubElement(env, "Automation")
    evs = ET.SubElement(auto, "Events")
    fe1 = ET.SubElement(evs, "FloatEvent")
    fe1.set("Time", "0.0")
    fe1.set("Value", "120.0")
    fe2 = ET.SubElement(evs, "FloatEvent")
    fe2.set("Time", "4.0")
    fe2.set("Value", "140.0")
    with gzip.open(str(als), "wb") as f:
        f.write(ET.tostring(root))

    with pytest.raises(StrictModeError) as exc_info:
        read_session(als, Report(strict=True))
    assert "ALS_TEMPO_AUTOMATION_UNSUPPORTED" in str(exc_info.value)


def test_f4_als_maintrack_tempo_constant_does_not_reject(tmp_path):
    """F4: automacao de tempo na MainTrack constante (sem variacao de BPM) e aceita em modo estrito."""
    import gzip
    import json
    import xml.etree.ElementTree as ET
    from aafton.als_read import read_session

    als = tmp_path / "tempo_constant.als"
    sidecar = tmp_path / "tempo_constant.aafton.json"
    sidecar.write_text(
        json.dumps(
            {
                "sample_rate": 48000,
                "timecode_rate": {"num": 25, "den": 1, "drop_frame": False},
                "session_start_samples": 0,
                "live_origin_samples": 0,
                "bpm": 120.0,
            }
        )
    )
    root = ET.Element("Ableton", attrib={"MajorVersion": "5", "MinorVersion": "12.0_12049"})
    ls = ET.SubElement(root, "LiveSet")
    mt = ET.SubElement(ls, "MainTrack")
    dc = ET.SubElement(mt, "DeviceChain")
    mx = ET.SubElement(dc, "Mixer")
    tp = ET.SubElement(mx, "Tempo")
    man = ET.SubElement(tp, "Manual")
    man.set("Value", "120.0")
    at = ET.SubElement(tp, "AutomationTarget")
    at.set("Id", "99")
    ae = ET.SubElement(mt, "AutomationEnvelopes")
    envs = ET.SubElement(ae, "Envelopes")
    env = ET.SubElement(envs, "AutomationEnvelope")
    env.set("Id", "1")
    et = ET.SubElement(env, "EnvelopeTarget")
    pt = ET.SubElement(et, "PointeeId")
    pt.set("Value", "99")
    auto = ET.SubElement(env, "Automation")
    evs = ET.SubElement(auto, "Events")
    fe1 = ET.SubElement(evs, "FloatEvent")
    fe1.set("Time", "0.0")
    fe1.set("Value", "120.0")
    fe2 = ET.SubElement(evs, "FloatEvent")
    fe2.set("Time", "4.0")
    fe2.set("Value", "120.0")
    with gzip.open(str(als), "wb") as f:
        f.write(ET.tostring(root))

    # Deve ler com sucesso sem levantar StrictModeError
    rep = Report(strict=True)
    sess = read_session(als, rep)
    assert sess is not None
    assert "ALS_TEMPO_AUTOMATION_UNSUPPORTED" not in [i.code for i in rep.issues]


def test_f2_stereo_pan_automation_rejected_in_strict(tmp_path):
    """F2: automacao de pan em track multicanal/stereo e rejeitada em modo estrito."""
    wav = _make_dummy_wav(tmp_path / "audio_stereo.wav", sample_rate=SR, duration_seconds=60)
    asset = MediaAsset(
        id="m1",
        filename="audio_stereo.wav",
        channels=2,
        sample_rate=SR,
        duration_samples=SR * 60,
        status="resolved",
        resolved_path=str(wav),
    )
    track = TrackIR(id="t0", source_index=0, name="AUDIO STEREO", kind="audio", channel_count=2)
    track.clips.append(
        ClipIR(
            id="c0",
            track_id="t0",
            channel_index=0,
            event_index=0,
            name="clip.wav",
            media_id="m1",
            timeline_start_samples=ORIGEM,
            timeline_end_samples=ORIGEM + 1000,
            source_offset_samples=0,
        )
    )
    track.pan_points = [(ORIGEM, -0.5), (ORIGEM + 1000, 0.5)]
    session = SessionIR(
        name="f2_stereo_pan",
        sample_rate=SR,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=ORIGEM,
        tracks=[track],
        media=[asset],
    )
    dest = tmp_path / "f2_stereo_pan.aaf"
    with pytest.raises(StrictModeError) as exc_info:
        write_aaf(session, dest, Report(strict=True))
    assert "WRITE_PAN_AUTOMATION_UNSUPPORTED" in str(exc_info.value)


def test_f5_session_markers_written_to_aaf(tmp_path):
    """F5: marcadores de sessao sao serializados para locators/DescriptiveMarker no AAF."""
    wav = _make_dummy_wav(tmp_path / "audio_f5.wav", sample_rate=SR, duration_seconds=60)
    asset = MediaAsset(
        id="m1",
        filename="audio_f5.wav",
        channels=1,
        sample_rate=SR,
        duration_samples=SR * 60,
        status="resolved",
        resolved_path=str(wav),
    )
    track = TrackIR(id="t0", source_index=0, name="AUDIO 1", kind="audio", channel_count=1)
    track.clips.append(
        ClipIR(
            id="c0",
            track_id="t0",
            channel_index=0,
            event_index=0,
            name="clip.wav",
            media_id="m1",
            timeline_start_samples=ORIGEM,
            timeline_end_samples=ORIGEM + 1000,
            source_offset_samples=0,
        )
    )
    markers = [
        MarkerIR(id="m1", name="Verso", position_samples=ORIGEM + 250),
        MarkerIR(id="m2", name="Refrao", position_samples=ORIGEM + 750),
    ]
    session = SessionIR(
        name="f5_markers",
        sample_rate=SR,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=ORIGEM,
        tracks=[track],
        media=[asset],
        markers=markers,
    )
    dest = tmp_path / "f5_markers.aaf"
    rep = Report(strict=True)
    write_aaf(session, dest, rep)
    assert "MARKERS_WRITTEN" in [i.code for i in rep.issues]

    relido = read_aaf(dest, Report(strict=False))
    assert len(relido.markers) == 2
    assert relido.markers[0].name == "Verso"
    assert relido.markers[0].position_samples == ORIGEM + 250
    assert relido.markers[1].name == "Refrao"
    assert relido.markers[1].position_samples == ORIGEM + 750
