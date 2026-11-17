"""Crossfade nativo do Live: o .als encosta os clips e o leitor refaz a geometria.

O defeito coberto aqui: o .als era escrito com os dois clips sobrepostos, e o
Live recorta o clip que sai no ponto da juncao e descarta o fade out dele. No
arquivo real medido o clip que saia perdia 24000 samples a cada Save As.

O conserto tem duas metades. O escritor escreve a forma nativa medida no Live
em 15/11/2026: os dois clips encostados no fim do clip que sai no AAF,
CrossfadeInState=1 e FadeInLength=0 no clip que entra, com o LoopStart dele
avancado o comprimento do crossfade. O leitor refaz a sobreposicao que o AAF
tem: num set de sidecar versao 3 pelo modelo medido (o clip que entra volta L
antes da juncao); num set de versao 2 pela leitura antiga, que exige tambem o
excesso de material entre OutMarker e LoopStart.

Cada teste aqui falha no codigo anterior: o escritor nao encostava os clips e o
leitor nao estendia a geometria. As guardas do que nao pode passar estao em
tests/unit/test_als_crossfade_guards.py.
"""

from __future__ import annotations

import json
import struct
import wave
from pathlib import Path

import pytest

from aafton.als_read import read_session
from aafton.als_write import WriteOptions, write_session
from aafton.als_xml import get_value, load_als, save_als
from aafton.ir import ClipIR, FadeIR, MediaAsset, SessionIR, TrackIR
from aafton.report import Report
from aafton.sidecar import sidecar_path_for
from aafton.timecode import TimecodeRate

TEMPLATE = Path("templates/live12.als")

pytestmark = pytest.mark.skipif(
    not TEMPLATE.exists(), reason="Portao B: templates/live12.als ainda nao foi autorado"
)

SR = 48000
BPM = 60.0
ORIGEM = 172800000
CROSSFADE = 24000
DURACAO = 96000
OFFSET_B = 48000


# --- Ajudantes --------------------------------------------------------------


def _wav_pcm(caminho: Path, sample_rate: int, frames: int) -> Path:
    """WAV mono de 16 bits com conteudo distinguivel: uma rampa periodica.

    O valor da amostra identifica a posicao no arquivo, entao dois clipes com
    offsets diferentes nao produzem o mesmo audio.
    """
    bloco = b"".join(struct.pack("<h", (i * 7) % 30000 - 15000) for i in range(1024))
    with wave.open(str(caminho), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes((bloco * (frames // 1024 + 1))[: frames * 2])
    return caminho


def _clip(indice: int, inicio: int, fim: int, offset: int, **fades) -> ClipIR:
    """Clip do IR no arranjo, com os fades opcionais."""
    return ClipIR(
        id=f"c{indice}",
        track_id="t0",
        channel_index=0,
        event_index=indice,
        name="fonte.wav",
        media_id="m1",
        timeline_start_samples=inicio,
        timeline_end_samples=fim,
        source_offset_samples=offset,
        **fades,
    )


def _sessao(wav: Path, clips: list[ClipIR], sample_rate: int = SR) -> SessionIR:
    """Sessao de uma track com os clips dados e um unico arquivo de audio."""
    asset = MediaAsset(
        id="m1",
        filename=wav.name,
        channels=1,
        sample_rate=sample_rate,
        duration_samples=sample_rate * 4,
        status="resolved",
        resolved_path=str(wav),
    )
    track = TrackIR(id="t0", source_index=0, name="CROSSFADE", kind="audio", channel_count=1)
    track.clips.extend(clips)
    return SessionIR(
        name="crossfade",
        sample_rate=sample_rate,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=ORIGEM,
        tracks=[track],
        media=[asset],
    )


def _sessao_do_par(wav: Path, sample_rate: int = SR) -> SessionIR:
    """Par que se sobrepoe como um crossfade: A sai e B entra no mesmo ponto."""
    return _sessao(
        wav,
        [
            _clip(
                0,
                ORIGEM,
                ORIGEM + DURACAO,
                0,
                fade_out=FadeIR(kind="out", duration_samples=CROSSFADE),
            ),
            _clip(
                1,
                ORIGEM + DURACAO - CROSSFADE,
                ORIGEM + DURACAO - CROSSFADE + DURACAO,
                OFFSET_B,
                fade_in=FadeIR(kind="in", duration_samples=CROSSFADE),
            ),
        ],
        sample_rate=sample_rate,
    )


def _sessao_da_cadeia(wav: Path) -> SessionIR:
    """Tres clips encadeados por dois crossfades."""
    meio = ORIGEM + DURACAO - CROSSFADE
    return _sessao(
        wav,
        [
            _clip(
                0,
                ORIGEM,
                ORIGEM + DURACAO,
                0,
                fade_out=FadeIR(kind="out", duration_samples=CROSSFADE),
            ),
            _clip(
                1,
                meio,
                meio + DURACAO,
                OFFSET_B,
                fade_in=FadeIR(kind="in", duration_samples=CROSSFADE),
                fade_out=FadeIR(kind="out", duration_samples=CROSSFADE),
            ),
            _clip(
                2,
                meio + DURACAO - CROSSFADE,
                meio + DURACAO - CROSSFADE + DURACAO,
                0,
                fade_in=FadeIR(kind="in", duration_samples=CROSSFADE),
            ),
        ],
    )


def _sessao_legada(wav: Path) -> SessionIR:
    """O modo legado: os clips ja encostados e o que entra traz a flag.

    Na versao 2 nao ha material sobrando no clip que sai, entao nao ha
    crossfade a provar. Na versao 3 o par e o crossfade que o Live toca.
    """
    clip_a = _clip(
        0,
        ORIGEM,
        ORIGEM + DURACAO,
        0,
        fade_out=FadeIR(kind="out", duration_samples=CROSSFADE),
    )
    clip_b = _clip(1, ORIGEM + DURACAO, ORIGEM + DURACAO + DURACAO, OFFSET_B)
    clip_b.crossfade_in_state = True
    return _sessao(wav, [clip_a, clip_b])


def _escrever(tmp_path: Path, sessao: SessionIR, sample_rate: int = SR, bpm: float = BPM) -> Path:
    """Grava o .als da sessao no template e devolve o caminho."""
    destino = tmp_path / "saida.als"
    write_session(
        sessao,
        TEMPLATE,
        destino,
        WriteOptions(sample_rate=sample_rate, bpm=bpm, live_origin_samples=ORIGEM),
        Report(),
    )
    return destino


def _clips_do_als(caminho: Path) -> list:
    """Os AudioClip do arranjo, na ordem em que o Live le."""
    root = load_als(caminho)
    return root.findall(
        ".//AudioTrack/DeviceChain/MainSequencer/Sample/ArrangerAutomation/Events/AudioClip"
    )


def _editar(caminho: Path, mudanca) -> None:
    """Muda o XML do .als e grava de volta."""
    root = load_als(caminho)
    mudanca(root)
    save_als(root, caminho)


def _set(clip, caminho: str, valor: str) -> None:
    """Escreve um valor de um campo do clip."""
    clip.find(caminho).set("Value", valor)


def _versao_do_sidecar(caminho: Path, versao: int) -> None:
    """Regrava o sidecar do .als com a format_version pedida."""
    sidecar = sidecar_path_for(caminho)
    dados = json.loads(sidecar.read_text(encoding="utf-8"))
    dados["format_version"] = versao
    sidecar.write_text(json.dumps(dados), encoding="utf-8")


def _forma_v2(caminho: Path) -> None:
    """Passa o par de ``_sessao_do_par`` (60 BPM) para a forma do escritor antigo.

    E o que o aafton gravava antes da forma medida, com sidecar de versao 2: A
    termina no inicio de B no AAF (1,5 beat), B comeca ali com o LoopStart do
    offset do AAF, e o OutMarker de A e o fim do trecho logico dele (2 beats).
    """

    def mudanca(root):
        clip_a, clip_b = root.findall(
            ".//AudioTrack/DeviceChain/MainSequencer/Sample/ArrangerAutomation/Events/AudioClip"
        )
        _set(clip_a, "CurrentEnd", "1.5")
        _set(clip_a, "Loop/OutMarker", "2")
        _set(clip_b, "CurrentStart", "1.5")
        clip_b.set("Time", "1.5")
        _set(clip_b, "Loop/LoopStart", "1")

    _editar(caminho, mudanca)
    _versao_do_sidecar(caminho, 2)


# --- O escritor: a forma nativa do Live -------------------------------------


def test_escritor_encosta_o_par_e_declara_o_crossfade(tmp_path):
    """O clip que sai termina onde o que entra comeca, e o crossfade e declarado.

    Antes do conserto o .als saia com os dois clips sobrepostos: o Live
    truncava o clip que sai e perdia o fade out inteiro.
    """
    wav = _wav_pcm(tmp_path / "fonte.wav", SR, SR * 4)
    sessao = _sessao_do_par(wav)
    destino = _escrever(tmp_path, sessao)

    clip_a, clip_b = _clips_do_als(destino)

    # A juncao e o fim de A no AAF (2 beats), no mesmo texto nos dois clips.
    assert float(get_value(clip_a, "CurrentEnd")) == 2.0
    assert get_value(clip_a, "CurrentEnd") == get_value(clip_b, "CurrentStart")
    # O atributo Time do clip e o que posiciona ele no arranjo.
    assert clip_a.get("Time") == get_value(clip_a, "CurrentStart")
    assert clip_b.get("Time") == get_value(clip_b, "CurrentStart")

    # O fade out inteiro fica no clip que sai; o que entra entra por estado.
    assert float(get_value(clip_a, "Fades/FadeOutLength")) == pytest.approx(0.5)
    assert get_value(clip_b, "Fades/CrossfadeInState") == "1"
    assert float(get_value(clip_b, "Fades/FadeInLength")) == 0.0
    # O que entra comeca L adiante na fonte: offset 1 s mais 0,5 s.
    assert float(get_value(clip_b, "Loop/LoopStart")) == 1.5

    # OutMarker e o fim do arquivo (4 s) em beats; HiddenLoopEnd o fim do trecho.
    assert float(get_value(clip_a, "Loop/OutMarker")) == pytest.approx(4.0)
    assert float(get_value(clip_a, "Loop/HiddenLoopEnd")) == pytest.approx(2.0)


def test_escritor_nao_mexe_no_ir_nem_no_sidecar(tmp_path):
    """A geometria logica continua no IR e no sidecar: so o XML muda de forma."""
    wav = _wav_pcm(tmp_path / "fonte.wav", SR, SR * 4)
    sessao = _sessao_do_par(wav)
    destino = _escrever(tmp_path, sessao)

    clip_a, clip_b = sessao.tracks[0].clips
    assert clip_a.timeline_end_samples == ORIGEM + DURACAO
    assert clip_b.timeline_start_samples == ORIGEM + DURACAO - CROSSFADE

    sidecar = json.loads(sidecar_path_for(destino).read_text(encoding="utf-8"))
    assert sidecar["clip_ranges"][0]["ranges"] == [
        [ORIGEM, ORIGEM + DURACAO],
        [ORIGEM + DURACAO - CROSSFADE, ORIGEM + DURACAO - CROSSFADE + DURACAO],
    ]


def test_escritor_canoniza_a_cadeia_inteira(tmp_path):
    """Todos os pares da cadeia viram crossfade nativo, um depois do outro."""
    wav = _wav_pcm(tmp_path / "fonte.wav", SR, SR * 4)
    destino = _escrever(tmp_path, _sessao_da_cadeia(wav))

    clip_a, clip_b, clip_c = _clips_do_als(destino)

    assert get_value(clip_a, "CurrentEnd") == get_value(clip_b, "CurrentStart")
    assert get_value(clip_b, "CurrentEnd") == get_value(clip_c, "CurrentStart")
    assert get_value(clip_b, "Fades/CrossfadeInState") == "1"
    assert get_value(clip_c, "Fades/CrossfadeInState") == "1"
    assert float(get_value(clip_a, "Fades/FadeOutLength")) == pytest.approx(0.5)
    assert float(get_value(clip_b, "Fades/FadeOutLength")) == pytest.approx(0.5)


def test_escritor_deixa_o_modo_legado_como_estava(tmp_path):
    """O modo legado, com os clips ja encostados e a flag no que entra, fica."""
    wav = _wav_pcm(tmp_path / "fonte.wav", SR, SR * 4)
    destino = _escrever(tmp_path, _sessao_legada(wav))

    clip_a, clip_b = _clips_do_als(destino)

    assert get_value(clip_a, "CurrentEnd") == get_value(clip_b, "CurrentStart")
    assert get_value(clip_b, "Fades/CrossfadeInState") == "1"
    # O fade out do clip que sai nao foi encurtado nemalongado.
    assert float(get_value(clip_a, "Fades/FadeOutLength")) == pytest.approx(0.5)


def test_escritor_ignora_sobreposicao_sem_fade_correspondente(tmp_path):
    """Sobreposicao que nao e o fade out e o fade in nao vira crossfade."""
    wav = _wav_pcm(tmp_path / "fonte.wav", SR, SR * 4)
    clip_a = _clip(
        0,
        ORIGEM,
        ORIGEM + DURACAO,
        0,
        fade_out=FadeIR(kind="out", duration_samples=CROSSFADE),
    )
    clip_b = _clip(
        1,
        ORIGEM + DURACAO - CROSSFADE,
        ORIGEM + DURACAO - CROSSFADE + DURACAO,
        OFFSET_B,
        fade_in=FadeIR(kind="in", duration_samples=CROSSFADE // 2),
    )
    destino = _escrever(tmp_path, _sessao(wav, [clip_a, clip_b]))

    clip_a_xml, clip_b_xml = _clips_do_als(destino)

    assert get_value(clip_a_xml, "CurrentEnd") != get_value(clip_b_xml, "CurrentStart")
    assert get_value(clip_b_xml, "Fades/CrossfadeInState") == "0"


# --- O leitor: a geometria logica de volta ----------------------------------


def test_leitor_refaz_a_sobreposicao_do_aaf(tmp_path):
    """O .als canonico volta a ser a geometria do AAF, amostra a amostra."""
    wav = _wav_pcm(tmp_path / "fonte.wav", SR, SR * 4)
    destino = _escrever(tmp_path, _sessao_do_par(wav))

    lido = read_session(destino, Report())
    clip_a, clip_b = lido.tracks[0].clips

    assert clip_a.timeline_start_samples == ORIGEM
    assert clip_a.timeline_end_samples == ORIGEM + DURACAO
    assert clip_b.timeline_start_samples == ORIGEM + DURACAO - CROSSFADE
    assert clip_b.timeline_end_samples == ORIGEM + DURACAO - CROSSFADE + DURACAO
    assert clip_a.fade_out is not None
    assert clip_a.fade_out.duration_samples == CROSSFADE
    assert clip_b.fade_in is not None
    assert clip_b.fade_in.duration_samples == CROSSFADE


def test_leitor_preserva_o_offset_da_fonte(tmp_path):
    """O offset da fonte nao some nem muda de lugar quando a geometria volta."""
    wav = _wav_pcm(tmp_path / "fonte.wav", SR, SR * 4)
    destino = _escrever(tmp_path, _sessao_do_par(wav))

    lido = read_session(destino, Report())
    clip_a, clip_b = lido.tracks[0].clips

    assert clip_a.source_offset_samples == 0
    assert clip_b.source_offset_samples == OFFSET_B


def test_leitor_refaz_a_cadeia_inteira(tmp_path):
    """Numa cadeia, cada clip volta com o seu crossfade, sem perder o vizinho."""
    wav = _wav_pcm(tmp_path / "fonte.wav", SR, SR * 4)
    sessao = _sessao_da_cadeia(wav)
    destino = _escrever(tmp_path, sessao)

    lido = read_session(destino, Report())
    clip_a, clip_b, clip_c = lido.tracks[0].clips

    esperado = [(c.timeline_start_samples, c.timeline_end_samples) for c in sessao.tracks[0].clips]
    assert [(c.timeline_start_samples, c.timeline_end_samples) for c in lido.tracks[0].clips] == (
        esperado
    )
    assert clip_a.fade_out is not None and clip_a.fade_out.duration_samples == CROSSFADE
    assert clip_b.fade_in is not None and clip_b.fade_in.duration_samples == CROSSFADE
    assert clip_b.fade_out is not None and clip_b.fade_out.duration_samples == CROSSFADE
    assert clip_c.fade_in is not None and clip_c.fade_in.duration_samples == CROSSFADE


@pytest.mark.parametrize(
    "sample_rate,bpm",
    [(48000, 60.0), (48000, 120.0), (44100, 60.0), (44100, 120.0)],
)
def test_ida_e_volta_em_taxas_e_tempos_diferentes(tmp_path, sample_rate, bpm):
    """A geometria volta inteira em 44.1 kHz e 48 kHz, a 60 e a 120 BPM.

    O que o teste prova e a coerencia do par com ele mesmo: o que o writer
    escreve e o que o reader le. Nao prova a unidade depois de um Save As do
    Live, que ninguem mediu aqui.
    """
    wav = _wav_pcm(tmp_path / "fonte.wav", sample_rate, sample_rate * 4)
    sessao = _sessao_do_par(wav, sample_rate=sample_rate)
    destino = _escrever(tmp_path, sessao, sample_rate=sample_rate, bpm=bpm)

    lido = read_session(destino, Report())
    clip_a, clip_b = lido.tracks[0].clips

    assert clip_a.timeline_end_samples == ORIGEM + DURACAO
    assert clip_b.timeline_start_samples == ORIGEM + DURACAO - CROSSFADE
    assert clip_a.fade_out is not None
    assert clip_a.fade_out.duration_samples == CROSSFADE
    assert clip_b.fade_in is not None
    assert clip_b.fade_in.duration_samples == CROSSFADE


# --- Os negativos: sem prova, a geometria antiga fica -----------------------


def test_sem_estado_de_crossfade_a_geometria_fica_recortada(tmp_path):
    """O mesmo XML sem CrossfadeInState=1 nao estende nada."""
    wav = _wav_pcm(tmp_path / "fonte.wav", SR, SR * 4)
    destino = _escrever(tmp_path, _sessao_do_par(wav))

    def sem_estado(root):
        clip_b = root.findall(
            ".//AudioTrack/DeviceChain/MainSequencer/Sample/ArrangerAutomation/Events/AudioClip"
        )[1]
        _set(clip_b, "Fades/CrossfadeInState", "0")

    _editar(destino, sem_estado)

    lido = read_session(destino, Report())
    clip_a, clip_b = lido.tracks[0].clips

    assert clip_a.timeline_end_samples == ORIGEM + DURACAO
    assert clip_b.timeline_start_samples == ORIGEM + DURACAO
    assert clip_b.fade_in is None


def test_sem_fade_out_vivo_nada_e_estendido(tmp_path):
    """O fade out do vizinho e a prova do comprimento: sem ele, nada cresce."""
    wav = _wav_pcm(tmp_path / "fonte.wav", SR, SR * 4)
    destino = _escrever(tmp_path, _sessao_do_par(wav))

    def sem_fade(root):
        clip_a = root.findall(
            ".//AudioTrack/DeviceChain/MainSequencer/Sample/ArrangerAutomation/Events/AudioClip"
        )[0]
        _set(clip_a, "Fades/FadeOutLength", "0")

    _editar(destino, sem_fade)

    lido = read_session(destino, Report())
    clip_a, clip_b = lido.tracks[0].clips

    assert clip_a.timeline_end_samples == ORIGEM + DURACAO
    assert clip_b.timeline_start_samples == ORIGEM + DURACAO
    assert clip_b.fade_in is None


# --- Os sets de format_version 2 leem como antes ----------------------------


def test_set_da_versao_2_volta_a_mesma_geometria(tmp_path):
    """Um set gravado na forma antiga, com sidecar de versao 2, le como antes."""
    wav = _wav_pcm(tmp_path / "fonte.wav", SR, SR * 4)
    destino = _escrever(tmp_path, _sessao_do_par(wav))
    _forma_v2(destino)

    lido = read_session(destino, Report())
    clip_a, clip_b = lido.tracks[0].clips

    assert (clip_a.timeline_start_samples, clip_a.timeline_end_samples) == (
        ORIGEM,
        ORIGEM + DURACAO,
    )
    assert (clip_b.timeline_start_samples, clip_b.timeline_end_samples) == (
        ORIGEM + DURACAO - CROSSFADE,
        ORIGEM + DURACAO - CROSSFADE + DURACAO,
    )
    assert (clip_a.source_offset_samples, clip_b.source_offset_samples) == (0, OFFSET_B)
    assert clip_a.fade_out is not None and clip_a.fade_out.duration_samples == CROSSFADE
    assert clip_b.fade_in is not None and clip_b.fade_in.duration_samples == CROSSFADE


def test_set_da_versao_2_nao_usa_o_modelo_medido(tmp_path):
    """O mesmo XML da forma nova com sidecar de versao 2 nao monta o par medido.

    Na versao 2 so o excesso entre OutMarker e LoopStart prova o crossfade, e
    o OutMarker da forma nova e o fim do arquivo: o par fica como esta.
    """
    wav = _wav_pcm(tmp_path / "fonte.wav", SR, SR * 4)
    destino = _escrever(tmp_path, _sessao_do_par(wav))
    _versao_do_sidecar(destino, 2)

    lido = read_session(destino, Report())
    clip_a, clip_b = lido.tracks[0].clips

    assert clip_a.timeline_end_samples == ORIGEM + DURACAO
    assert clip_b.timeline_start_samples == ORIGEM + DURACAO
    assert clip_b.fade_in is None


def test_out_marker_sem_excesso_nao_extende(tmp_path):
    """O material que sobra e a prova da versao 2: sem ele o fade fica como era."""
    wav = _wav_pcm(tmp_path / "fonte.wav", SR, SR * 4)
    destino = _escrever(tmp_path, _sessao_do_par(wav))
    _forma_v2(destino)

    def sem_excesso(root):
        clip_a = root.findall(
            ".//AudioTrack/DeviceChain/MainSequencer/Sample/ArrangerAutomation/Events/AudioClip"
        )[0]
        # O OutMarker volta para a duracao visivel: nao sobra material.
        _set(clip_a, "Loop/OutMarker", get_value(clip_a, "CurrentEnd"))
        _set(clip_a, "Loop/HiddenLoopEnd", get_value(clip_a, "CurrentEnd"))

    _editar(destino, sem_excesso)

    lido = read_session(destino, Report())
    clip_a, clip_b = lido.tracks[0].clips

    assert clip_a.timeline_end_samples == ORIGEM + DURACAO - CROSSFADE
    assert clip_b.fade_in is None


def test_par_encostado_com_flag_1_le_pelo_modelo_medido(tmp_path):
    """Encostado e com a flag, o par e o crossfade que o Live toca.

    O Live toca o crossfade antes da juncao, com o material de B antes do
    LoopStart (1 s aqui): B volta L antes, com offset 1 s - L e fade in L.
    """
    wav = _wav_pcm(tmp_path / "fonte.wav", SR, SR * 4)
    destino = _escrever(tmp_path, _sessao_legada(wav))

    lido = read_session(destino, Report())
    clip_a, clip_b = lido.tracks[0].clips

    assert clip_a.timeline_end_samples == ORIGEM + DURACAO
    assert clip_a.fade_out is not None and clip_a.fade_out.duration_samples == CROSSFADE
    assert clip_b.timeline_start_samples == ORIGEM + DURACAO - CROSSFADE
    assert clip_b.timeline_end_samples == ORIGEM + DURACAO + DURACAO
    assert clip_b.source_offset_samples == OFFSET_B - CROSSFADE
    assert clip_b.fade_in is not None and clip_b.fade_in.duration_samples == CROSSFADE


def test_fade_de_render_legado_com_flag_1_fica_como_era_na_versao_2(tmp_path):
    """Na versao 2 o modo legado, encostado e com a flag, fica como era."""
    wav = _wav_pcm(tmp_path / "fonte.wav", SR, SR * 4)
    destino = _escrever(tmp_path, _sessao_legada(wav))
    _versao_do_sidecar(destino, 2)

    lido = read_session(destino, Report())
    clip_a, clip_b = lido.tracks[0].clips

    assert clip_a.timeline_end_samples == ORIGEM + DURACAO
    assert clip_b.timeline_start_samples == ORIGEM + DURACAO
    assert clip_b.fade_in is None
    assert clip_b.crossfade_in_state is True


def test_crossfade_nao_provado_leva_track_e_track_id_no_erro(tmp_path):
    """Sem sidecar, o erro de crossfade nao provado identifica track e track_id."""
    from aafton.als_ancora import start_padrao
    from tests.unit.test_als_sem_sidecar import clip, escrever_wav, montar_set

    wav = escrever_wav(tmp_path, "dx.wav")
    a = clip(wav, 0.0, 4.0, nome="a", warp=[(0.0, 0.0), (4.0, 4.0)], fade_out=1.0)
    b = clip(wav, 4.0, 8.0, nome="b", crossfade=True, loop_start=2.0)
    als = montar_set(tmp_path, [("LIMPA", [clip(wav, 0.0, 4.0)]), ("XF", [a, b])], bpm=60.0)
    report = Report(strict=False)

    read_session(als, report, start=start_padrao())

    erros = [i for i in report.issues if i.code == "ALS_CROSSFADE_NOT_PROVEN"]
    assert len(erros) == 1
    assert erros[0].context["track"] == "XF"
    assert erros[0].context["track_id"] == "t1"
