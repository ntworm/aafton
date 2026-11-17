"""Leitura de um set do Live sem o registro do aafton (P2 da task do Live -> AAF).

Os sets sao montados aqui, no XML minimo que o leitor usa: tracks de audio
com clips do arranjo, a MainTrack com o tempo e, quando o teste pede, tracks
MIDI, o locator TC do aafton e automacao. Nenhum tem ``.aafton.json``.
"""

from __future__ import annotations

import gzip
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

import pytest

from aafton.als_ancora import StartSpec, start_padrao
from aafton.als_read import read_session
from aafton.als_render import MISTA, RenderInLiveError
from aafton.pipeline import export_session
from aafton.report import Report, StrictModeError
from aafton.sidecar import SidecarMissingError
from aafton.timecode import TimecodeRate
from tests.aaf_factory import wav_bytes

UMA_HORA_24 = 3600 * 48000
# Marcadores de um clip consolidado a 120 BPM num set a 104 BPM: razao 0,866667.
ESTICADO_104 = [(0.0, 0.0), (8.0, 4.0)]
# Marcadores 1:1 a 120 BPM: 0,5 s por beat.
UM_PARA_UM_120 = [(0.0, 0.0), (16.0, 8.0)]


def escrever_wav(pasta: Path, nome: str, taxa: int = 48000, canais: int = 1) -> Path:
    """Um WAV de silencio de 20 segundos."""
    caminho = pasta / nome
    caminho.write_bytes(wav_bytes(taxa * 20, canais, taxa))
    return caminho


def clip(caminho: Path | str, inicio: float, fim: float, **campos) -> dict:
    """Os campos de um AudioClip; os que faltam ficam no clip reto do Live."""
    dados = {
        "caminho": str(caminho),
        "inicio": inicio,
        "fim": fim,
        "nome": Path(str(caminho)).stem,
        "loop_start": 0.0,
        "warp": None,
        "modo": "4",
        "crossfade": False,
        "fade_in": 0.0,
        "fade_out": 0.0,
        "loop_on": False,
        "pitch_coarse": 0,
        "pitch_fine": 0,
        "envelope": False,
    }
    dados.update(campos)
    return dados


def _sub(pai: ET.Element, caminho: str, **atributos) -> ET.Element:
    """Cria os elementos do caminho; o ultimo recebe os atributos."""
    node = pai
    partes = caminho.split("/")
    for parte in partes[:-1]:
        filho = node.find(parte)
        node = filho if filho is not None else ET.SubElement(node, parte)
    return ET.SubElement(node, partes[-1], **atributos)


def _valor(pai: ET.Element, caminho: str, valor) -> None:
    _sub(pai, caminho, Value=str(valor))


def _warp(node: ET.Element, dados: dict) -> None:
    _valor(node, "IsWarped", "true" if dados["warp"] else "false")
    _valor(node, "WarpMode", dados["modo"])
    marcadores = ET.SubElement(node, "WarpMarkers")
    for indice, (beat, segundo) in enumerate(dados["warp"] or []):
        ET.SubElement(
            marcadores, "WarpMarker", Id=str(indice), SecTime=str(segundo), BeatTime=str(beat)
        )


def no_do_clip(dados: dict, pai: ET.Element | None = None, indice: int = 0) -> ET.Element:
    """O AudioClip do XML a partir dos campos de ``clip``."""
    atributos = {"Id": str(indice), "Time": str(dados["inicio"])}
    if pai is None:
        node = ET.Element("AudioClip", **atributos)
    else:
        node = ET.SubElement(pai, "AudioClip", **atributos)
    _valor(node, "CurrentStart", dados["inicio"])
    _valor(node, "CurrentEnd", dados["fim"])
    _valor(node, "Name", dados["nome"])
    _valor(node, "SampleRef/FileRef/Path", dados["caminho"])
    _valor(node, "Loop/LoopStart", dados["loop_start"])
    _valor(node, "Loop/LoopEnd", dados["loop_start"] + dados["fim"] - dados["inicio"])
    _valor(node, "Loop/LoopOn", "true" if dados["loop_on"] else "false")
    _valor(node, "Loop/StartRelative", 0)
    _warp(node, dados)
    _valor(node, "PitchCoarse", dados["pitch_coarse"])
    _valor(node, "PitchFine", dados["pitch_fine"])
    _valor(node, "Fades/FadeInLength", dados["fade_in"])
    _valor(node, "Fades/FadeOutLength", dados["fade_out"])
    _valor(node, "Fades/CrossfadeInState", 1 if dados["crossfade"] else 0)
    if dados["envelope"]:
        evento = _sub(node, "Envelopes/Envelopes/ClipEnvelope/Automation/Events/FloatEvent")
        evento.set("Time", "0")
        evento.set("Value", "0.5")
    return node


def _automacao_sem_destino(track: ET.Element) -> None:
    envelope = _sub(track, "AutomationEnvelopes/Envelopes/AutomationEnvelope")
    _valor(envelope, "EnvelopeTarget/PointeeId", 777)
    _sub(envelope, "Automation/Events/FloatEvent", Time="0", Value="0.5")


def _main_track(live: ET.Element, bpm: float, tempo_variando: bool) -> None:
    main = ET.SubElement(live, "MainTrack")
    _valor(main, "DeviceChain/Mixer/Tempo/Manual", bpm)
    _sub(main, "DeviceChain/Mixer/Tempo/AutomationTarget", Id="900")
    if not tempo_variando:
        return
    envelope = _sub(main, "AutomationEnvelopes/Envelopes/AutomationEnvelope")
    _valor(envelope, "EnvelopeTarget/PointeeId", 900)
    eventos = _sub(envelope, "Automation/Events")
    ET.SubElement(eventos, "FloatEvent", Time="0", Value=str(bpm))
    ET.SubElement(eventos, "FloatEvent", Time="8", Value=str(bpm + 10))


def montar_set(
    pasta: Path,
    tracks: list[tuple[str, list[dict]]],
    *,
    bpm: float = 120.0,
    midi: tuple[str, ...] = (),
    midi_clips: tuple[str, ...] = (),
    locator: str | None = None,
    tempo_variando: bool = False,
    automacao_em: str | None = None,
    nome: str = "set.als",
) -> Path:
    """Um .als sem sidecar com as tracks de audio dadas."""
    root = ET.Element("Ableton", Creator="Ableton Live 12.3.5")
    live = ET.SubElement(root, "LiveSet")
    lista = ET.SubElement(live, "Tracks")
    for indice, (nome_track, clips) in enumerate(tracks):
        track = ET.SubElement(lista, "AudioTrack", Id=str(10 + indice))
        _valor(track, "Name/EffectiveName", nome_track)
        if nome_track == automacao_em:
            _automacao_sem_destino(track)
        eventos = _sub(track, "DeviceChain/MainSequencer/Sample/ArrangerAutomation/Events")
        for evento, dados in enumerate(clips):
            no_do_clip(dados, eventos, evento)
    for indice, nome_midi in enumerate(midi):
        track = ET.SubElement(lista, "MidiTrack", Id=str(50 + indice))
        _valor(track, "Name/EffectiveName", nome_midi)
        if nome_midi in midi_clips:
            mc = _sub(
                track,
                "DeviceChain/MainSequencer/ClipTimeable/ArrangerAutomation/Events/MidiClip",
            )
            _valor(mc, "CurrentStart", 0)
            _valor(mc, "CurrentEnd", 4)
    _main_track(live, bpm, tempo_variando)
    if locator:
        _valor(live, "Locators/Locators/Locator/Name", locator)
    caminho = pasta / nome
    with gzip.open(caminho, "wb") as handle:
        handle.write(ET.tostring(root, encoding="utf-8"))
    return caminho


def _codigos(report: Report) -> list[str]:
    return [issue.code for issue in report.issues]


def _de(report: Report, codigo: str) -> list:
    return [issue for issue in report.issues if issue.code == codigo]


def _set_simples(tmp_path: Path, **opcoes) -> Path:
    wav = escrever_wav(tmp_path, "dialogo.wav")
    return montar_set(tmp_path, [("DX", [clip(wav, 0.0, 4.0)])], **opcoes)


# --- Ancora ------------------------------------------------------------------


def test_sem_inicio_o_compasso_1_cai_em_01h_a_24_fps(tmp_path):
    report = Report()
    session = read_session(_set_simples(tmp_path), report, start=start_padrao())

    assert session.start_timecode_samples == UMA_HORA_24
    assert session.timecode_rate == TimecodeRate(24, 1)
    assert session.sample_rate == 48000
    clip_lido = session.tracks[0].clips[0]
    assert clip_lido.timeline_start_samples == UMA_HORA_24
    assert clip_lido.timeline_end_samples == UMA_HORA_24 + 96000
    assert "ALS_START_TC_ASSUMED" in _codigos(report)


def test_sem_inicio_e_sem_locator_a_leitura_sem_start_recusa(tmp_path):
    with pytest.raises(SidecarMissingError, match="--start-tc"):
        read_session(_set_simples(tmp_path), Report())


def test_start_tc_e_frame_rate_mudam_o_inicio(tmp_path):
    report = Report()
    start = StartSpec("10:00:00:00", Fraction(25))
    session = read_session(_set_simples(tmp_path), report, start=start)

    assert session.start_timecode_samples == 10 * UMA_HORA_24
    assert session.timecode_rate == TimecodeRate(25, 1)
    assert "ALS_START_TC_ASSUMED" not in _codigos(report)


def test_o_locator_tc_preenche_o_inicio_padrao(tmp_path):
    caminho = _set_simples(tmp_path, locator="TC 02:00:00:00 @ 48000")
    report = Report()
    session = read_session(caminho, report, start=start_padrao())

    assert session.start_timecode_samples == 2 * UMA_HORA_24
    assert "SIDECAR_RECOVERED" in _codigos(report)
    assert "ALS_START_TC_ASSUMED" not in _codigos(report)


def test_o_inicio_dado_vence_o_locator_com_aviso(tmp_path):
    caminho = _set_simples(tmp_path, locator="TC 02:00:00:00 @ 48000")
    report = Report()
    session = read_session(caminho, report, start=StartSpec("10:00:00:00"))

    assert session.start_timecode_samples == 10 * UMA_HORA_24
    assert "ALS_START_TC_OVERRIDES_LOCATOR" in _codigos(report)


def test_a_taxa_da_sessao_e_a_da_maioria_dos_clips(tmp_path):
    a = escrever_wav(tmp_path, "a.wav", 44100)
    b = escrever_wav(tmp_path, "b.wav", 44100)
    c = escrever_wav(tmp_path, "c.wav", 48000)
    clips = [clip(a, 0.0, 2.0), clip(b, 2.0, 4.0), clip(c, 4.0, 6.0)]
    caminho = montar_set(tmp_path, [("DX", clips)])

    session = read_session(caminho, Report(), start=start_padrao())

    assert session.sample_rate == 44100


def test_empate_de_taxas_fica_em_48k(tmp_path):
    a = escrever_wav(tmp_path, "a.wav", 44100)
    b = escrever_wav(tmp_path, "b.wav", 96000)
    caminho = montar_set(tmp_path, [("DX", [clip(a, 0.0, 2.0), clip(b, 2.0, 4.0)])])

    session = read_session(caminho, Report(), start=start_padrao())

    assert session.sample_rate == 48000


def test_arquivo_estereo_fica_um_clip_estereo(tmp_path):
    wav = escrever_wav(tmp_path, "musica.wav", canais=2)
    caminho = montar_set(tmp_path, [("MX", [clip(wav, 0.0, 4.0)])])

    session = read_session(caminho, Report(), start=start_padrao())

    assert len(session.tracks[0].clips) == 1
    assert session.tracks[0].channel_count == 2


# --- Lista de render ---------------------------------------------------------


def _set_esticado(tmp_path: Path, nomes=("3-Audio", "5-2"), **opcoes) -> Path:
    wav = escrever_wav(tmp_path, "stem.wav")
    tracks = [(nome, [clip(wav, 0.0, 8.0, warp=ESTICADO_104)]) for nome in nomes]
    return montar_set(tmp_path, tracks, bpm=104.0, **opcoes)


def test_o_estrito_para_com_um_unico_erro_que_cita_todas_as_tracks(tmp_path):
    caminho = _set_esticado(tmp_path)

    with pytest.raises(StrictModeError) as erro:
        read_session(caminho, Report(), start=start_padrao())

    mensagem = str(erro.value)
    assert "ALS_RENDER_IN_LIVE" in mensagem
    assert "3-Audio: 1 clip with warp" in mensagem
    assert "5-2: 1 clip with warp" in mensagem
    assert "--leave-out-render" in mensagem
    report = Report(strict=False)
    read_session(caminho, report, start=start_padrao())
    erros = _de(report, "ALS_RENDER_IN_LIVE")
    assert len(erros) == 1
    assert [t["track_name"] for t in erros[0].context["tracks"]] == ["3-Audio", "5-2"]


# Complex (4) e Complex Pro (6) leem uma amostra depois (medido no kit 3).
@pytest.mark.parametrize("modo,deslocamento", [("3", 0), ("4", 1), ("6", 1)])
def test_warp_1_1_nos_modos_medidos_entra_com_o_offset_interpolado(tmp_path, modo, deslocamento):
    wav = escrever_wav(tmp_path, "stem.wav")
    dados = clip(wav, 0.0, 4.0, warp=UM_PARA_UM_120, modo=modo, loop_start=2.0)
    caminho = montar_set(tmp_path, [("ST", [dados])])

    session = read_session(caminho, Report(), start=start_padrao())

    assert session.tracks[0].clips[0].source_offset_samples == 48000 + deslocamento


def test_warp_1_1_fora_dos_marcadores_e_extrapolado(tmp_path):
    wav = escrever_wav(tmp_path, "stem.wav")
    dados = clip(wav, 0.0, 4.0, warp=[(2.0, 1.0), (10.0, 5.0)], loop_start=1.0)
    caminho = montar_set(tmp_path, [("ST", [dados])])

    session = read_session(caminho, Report(), start=start_padrao())

    # Modo padrao do helper e Complex (4): uma amostra depois.
    assert session.tracks[0].clips[0].source_offset_samples == 24001


@pytest.mark.parametrize(
    "campos,causa",
    [
        ({"warp": UM_PARA_UM_120, "modo": "0"}, "beats mode"),
        ({"warp": UM_PARA_UM_120, "modo": "1"}, "warp mode"),
        ({"warp": UM_PARA_UM_120, "modo": "2"}, "warp mode"),
        ({"warp": UM_PARA_UM_120, "pitch_coarse": 2}, "transpose"),
        ({"pitch_fine": 10}, "transpose"),
        ({"loop_on": True}, "loop"),
    ],
)
def test_as_outras_causas_de_render_sao_recusadas(tmp_path, campos, causa):
    wav = escrever_wav(tmp_path, "stem.wav")
    caminho = montar_set(tmp_path, [("ST", [clip(wav, 0.0, 4.0, **campos)])])
    report = Report(strict=False)

    read_session(caminho, report, start=start_padrao())

    erro = _de(report, "ALS_RENDER_IN_LIVE")[0]
    assert erro.context["tracks"][0]["clips"][0]["causes"] == [causa]
    assert f"1 clip with {causa}" in erro.message


# --- leave_out_render --------------------------------------------------------


def test_leave_out_render_tira_o_crossfade_do_vizinho_com_a_nota(tmp_path):
    wav = escrever_wav(tmp_path, "dx.wav")
    esticado = clip(wav, 0.0, 4.0, nome="esticado", warp=[(0.0, 0.0), (8.0, 4.6)])
    exato = clip(wav, 4.0, 8.0, nome="exato", crossfade=True, loop_start=3.0)
    caminho = montar_set(tmp_path, [("DX", [esticado, exato])])
    report = Report()

    session = read_session(caminho, report, start=start_padrao(), leave_out_render=True)

    clips = session.tracks[0].clips
    assert [c.name for c in clips] == ["exato"]
    assert clips[0].crossfade_in_state is False
    nota = _de(report, "ALS_CLIPS_LEFT_OUT")[0]
    assert nota.context["crossfade_removed"] == ["exato"]
    assert "crossfade removed on 'exato'" in nota.message
    assert "ALS_CROSSFADE_NOT_PROVEN" not in _codigos(report)
    assert "ALS_RENDER_IN_LIVE" not in _codigos(report)


def test_track_esvaziada_sai_com_a_automacao(tmp_path):
    wav = escrever_wav(tmp_path, "dx.wav")
    tracks = [
        ("A", [clip(wav, 0.0, 4.0)]),
        ("B", [clip(wav, 0.0, 8.0, warp=ESTICADO_104)]),
    ]
    caminho = montar_set(tmp_path, tracks, bpm=104.0, automacao_em="B")
    report = Report()

    session = read_session(caminho, report, start=start_padrao(), leave_out_render=True)

    assert [t.name for t in session.tracks] == ["A"]
    assert not any("'B'" in feature for feature in session.unsupported_export_features)
    assert not _de(report, "ALS_AUTOMATION_UNSUPPORTED")
    nota = _de(report, "ALS_CLIPS_LEFT_OUT")[0]
    assert nota.context["track_removed"] is True
    usadas = {c.media_id for t in session.tracks for c in t.clips}
    assert {asset.id for asset in session.media} == usadas


def test_tempo_que_varia_continua_recusado_com_leave_out_render(tmp_path):
    caminho = _set_esticado(tmp_path, nomes=("3-Audio",), tempo_variando=True)

    with pytest.raises(StrictModeError, match="ALS_TEMPO_AUTOMATION_UNSUPPORTED"):
        read_session(caminho, Report(), start=start_padrao(), leave_out_render=True)


def test_nada_a_exportar_quando_todos_os_clips_saem(tmp_path):
    caminho = _set_esticado(tmp_path)

    with pytest.raises(StrictModeError, match="nothing left to export"):
        read_session(caminho, Report(), start=start_padrao(), leave_out_render=True)


def test_envelope_de_clip_sai_pela_identidade_do_no(tmp_path):
    """Dois clips de mesmo nome: so o envelope do exato fica como feature."""
    wav = escrever_wav(tmp_path, "dx.wav")
    esticado = clip(wav, 0.0, 4.0, nome="igual", warp=ESTICADO_104, envelope=True)
    exato = clip(wav, 8.0, 12.0, nome="igual", envelope=True)
    caminho = montar_set(tmp_path, [("DX", [esticado, exato])], bpm=104.0)

    session = read_session(
        caminho, Report(strict=False), start=start_padrao(), leave_out_render=True
    )

    envelopes = [f for f in session.unsupported_export_features if "of the clip 'igual'" in f]
    assert len(envelopes) == 1


# --- Track mista, crossfade, video e MIDI -------------------------------------


def test_track_com_mono_e_estereo_e_recusada_com_a_causa(tmp_path):
    mono = escrever_wav(tmp_path, "mono.wav")
    estereo = escrever_wav(tmp_path, "estereo.wav", canais=2)
    tracks = [("MX", [clip(mono, 0.0, 4.0), clip(estereo, 8.0, 12.0)])]
    caminho = montar_set(tmp_path, tracks)

    for leave_out in (False, True):
        with pytest.raises(StrictModeError, match="ALS_RENDER_IN_LIVE") as erro:
            read_session(caminho, Report(), start=start_padrao(), leave_out_render=leave_out)
        assert f"MX: {MISTA}" in str(erro.value)


def _par_a_59(tmp_path: Path, a_com_warp: bool) -> Path:
    wav = escrever_wav(tmp_path, "dx.wav")
    warp = [(0.0, 0.0), (4.0, 240 / 59)] if a_com_warp else None
    a = clip(wav, 0.0, 4.0, nome="a", warp=warp, fade_out=1.0)
    b = clip(wav, 4.0, 8.0, nome="b", crossfade=True, loop_start=2.0)
    return montar_set(tmp_path, [("DX", [a, b])], bpm=59.0)


def test_crossfade_ao_lado_de_clip_1_1_com_warp_nao_e_provado(tmp_path):
    with pytest.raises(StrictModeError, match="ALS_CROSSFADE_NOT_PROVEN"):
        read_session(_par_a_59(tmp_path, True), Report(), start=start_padrao())


def test_crossfade_entre_clips_sem_warp_e_montado(tmp_path):
    report = Report()
    session = read_session(_par_a_59(tmp_path, False), report, start=start_padrao())

    assert "ALS_CROSSFADE_NOT_PROVEN" not in _codigos(report)
    assert session.tracks[0].clips[1].fade_in is not None


def test_o_video_nunca_entra_na_midia(tmp_path):
    wav = escrever_wav(tmp_path, "dx.wav")
    video = tmp_path / "roughcut 2.mov"
    tracks = [
        ("VIDEO", [clip(video, 0.0, 8.0)]),
        ("DX", [clip(video, 0.0, 4.0, nome="corte"), clip(wav, 4.0, 8.0)]),
    ]
    caminho = montar_set(tmp_path, tracks)
    report = Report()

    session = read_session(caminho, report, start=start_padrao())

    assert all(not asset.filename.endswith(".mov") for asset in session.media)
    assert session.tracks[0].is_video is True
    assert session.tracks[1].is_video is False
    assert [c.name for c in session.tracks[1].clips] == ["dx"]
    assert len(_de(report, "ALS_VIDEO_LEFT_OUT")) == 2


def test_tracks_midi_ficam_de_fora_com_aviso(tmp_path):
    report = Report()
    read_session(
        _set_simples(tmp_path, midi=("Piano",), midi_clips=("Piano",)),
        report,
        start=start_padrao(),
    )

    aviso = _de(report, "ALS_MIDI_TRACKS_LEFT_OUT")[0]
    assert aviso.severity == "warning"
    assert "'Piano'" in aviso.message


def test_track_midi_vazia_nao_emite_aviso_nem_conta_no_nada(tmp_path):
    report_vazia = Report()
    als_vazia = _set_simples(tmp_path, midi=("Vazia", "Piano"), midi_clips=("Piano",))
    read_session(als_vazia, report_vazia, start=start_padrao())

    aviso = _de(report_vazia, "ALS_MIDI_TRACKS_LEFT_OUT")
    assert len(aviso) == 1
    assert aviso[0].context["tracks"] == ["Piano"]

    report_so_vazia = Report()
    read_session(_set_simples(tmp_path, midi=("Vazia",)), report_so_vazia, start=start_padrao())
    assert _de(report_so_vazia, "ALS_MIDI_TRACKS_LEFT_OUT") == []

    wav = escrever_wav(tmp_path, "esticado.wav")
    als_nada = montar_set(
        tmp_path,
        [("3-Audio", [clip(wav, 0.0, 8.0, warp=ESTICADO_104)])],
        bpm=104.0,
        midi=("Vazia",),
        nome="nada.als",
    )
    report_nada = Report(strict=False)
    read_session(als_nada, report_nada, start=start_padrao(), leave_out_render=True)

    nada = _de(report_nada, "ALS_NOTHING_TO_EXPORT")[0]
    assert nada.context["midi_tracks"] == 0
    assert "MIDI" not in nada.message


def test_export_com_selecao_ignora_render_e_mista_fora_da_selecao(tmp_path):
    mono = escrever_wav(tmp_path, "mono.wav", canais=1)
    estereo = escrever_wav(tmp_path, "estereo.wav", canais=2)
    tracks = [
        ("DX", [clip(mono, 0.0, 4.0)]),
        ("DX", [clip(mono, 0.0, 8.0, warp=ESTICADO_104)]),
        ("MX", [clip(mono, 0.0, 4.0), clip(estereo, 4.0, 8.0)]),
    ]
    caminho = montar_set(tmp_path, tracks, bpm=104.0)

    saida_ok = tmp_path / "ok.aaf"
    report_ok = Report(strict=True)
    sessao = export_session(caminho, saida_ok, report_ok, tracks=[0], start=start_padrao())

    assert saida_ok.exists()
    assert [t.id for t in sessao.tracks] == ["t0"]
    assert _de(report_ok, "ALS_RENDER_IN_LIVE") == []

    saida_parcial = tmp_path / "parcial.aaf"
    report_parcial = Report(strict=False)
    with pytest.raises(RenderInLiveError):
        export_session(caminho, saida_parcial, report_parcial, tracks=[0, 1], start=start_padrao())
    erro = _de(report_parcial, "ALS_RENDER_IN_LIVE")[0]
    assert [e["track_id"] for e in erro.context["tracks"]] == ["t1"]
    assert "MX" not in erro.message


def test_export_subconjunto_ignora_crossfade_nao_provado_fora_e_recusa_dentro(tmp_path):
    from aafton.verify import verify_conversion

    wav = escrever_wav(tmp_path, "dx.wav")
    a = clip(wav, 0.0, 4.0, nome="a", warp=[(0.0, 0.0), (4.0, 4.0)], fade_out=1.0)
    b = clip(wav, 4.0, 8.0, nome="b", crossfade=True, loop_start=2.0)
    caminho = montar_set(tmp_path, [("LIMPA", [clip(wav, 0.0, 4.0)]), ("XF", [a, b])], bpm=60.0)

    saida_ok = tmp_path / "limpa.aaf"
    sessao = export_session(
        caminho, saida_ok, Report(strict=True), tracks=[0], start=start_padrao()
    )

    assert saida_ok.exists()
    assert [t.name for t in sessao.tracks] == ["LIMPA"]
    assert verify_conversion(saida_ok, caminho, tracks=[0]).ok is True

    saida_erro = tmp_path / "com_xf.aaf"
    with pytest.raises(StrictModeError, match="ALS_CROSSFADE_NOT_PROVEN"):
        export_session(caminho, saida_erro, Report(strict=True), tracks=[1], start=start_padrao())
    assert not saida_erro.exists()
