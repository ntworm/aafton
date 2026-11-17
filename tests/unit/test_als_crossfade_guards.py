"""Guardas do crossfade: o que o escritor e o leitor nao podem aceitar.

O crossfade nativo do Live so vale quando o par inteiro se sustenta: o clip
que entra comeca depois do que sai, a sobreposicao cabe no clip que entra, os
fades escritos no XML valem a sobreposicao e o leitor encontra todas as provas.

As guardas aqui passam pelo pipeline inteiro, de escrita a leitura, e cobrem
os casos em que um par invalido seria aceito por um lado e recusado pelo
outro. Nenhum deles pode inventar extensao nem clip vazio.

O teste do pipeline com AAF de origem tambem mora aqui: o arquivo de
crossfade continua em tests/unit/test_als_crossfade.py, que tem a forma
nativa e a volta da geometria.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from aafton.aaf_read import read_aaf
from aafton.als_read import read_session
from aafton.als_xml import get_value
from aafton.cli import main
from aafton.ir import FadeIR
from aafton.report import Report
from tests.aaf_factory import build_aaf_with_transition
from tests.unit.test_als_crossfade import (
    CROSSFADE,
    DURACAO,
    OFFSET_B,
    ORIGEM,
    SR,
    TEMPLATE,
    _clip,
    _clips_do_als,
    _editar,
    _escrever,
    _forma_v2,
    _sessao,
    _sessao_da_cadeia,
    _sessao_do_par,
    _set,
    _wav_pcm,
)

pytestmark = pytest.mark.skipif(
    not TEMPLATE.exists(), reason="Portao B: templates/live12.als ainda nao foi autorado"
)


# --- Ajudantes --------------------------------------------------------------


def _segundo_clip(root):
    """O segundo AudioClip do arranjo, como o Live le."""
    return root.findall(
        ".//AudioTrack/DeviceChain/MainSequencer/Sample/ArrangerAutomation/Events/AudioClip"
    )[1]


def _sem_fade_in(clip) -> None:
    """Tira o elemento FadeInLength do clip."""
    fades = clip.find("Fades")
    fades.remove(fades.find("FadeInLength"))


def _fades_desligados(clip) -> None:
    """Desliga os fades do clip, como o elemento Fade do Live faz."""
    fade = clip.find("Fade")
    if fade is None:
        fade = ET.SubElement(clip, "Fade")
    fade.set("Value", "false")


def _geometria(lido):
    """O par de samples de cada clip, na ordem do tempo."""
    clips = lido.tracks[0].clips
    return sorted((clip.timeline_start_samples, clip.timeline_end_samples) for clip in clips)


def _sem_extensao(lido, esperado: int) -> None:
    """A geometria do clip que sai fica como estava, sem extensao nem clip vazio."""
    clip_a = lido.tracks[0].clips[0]
    assert clip_a.timeline_end_samples == esperado
    for clip in lido.tracks[0].clips:
        assert clip.timeline_end_samples > clip.timeline_start_samples


def _clips_do_aaf(caminho: Path) -> list:
    """Os clips de audio do AAF, como o leitor do produto os le."""
    return read_aaf(caminho, Report(strict=False)).tracks[0].clips


def _mesma_geometria(exportado: list, origem: list) -> None:
    """O AAF exportado tem a geometria e os offsets do AAF de origem."""
    for destino, fonte in zip(exportado, origem, strict=True):
        assert destino.timeline_start_samples == fonte.timeline_start_samples
        assert destino.timeline_end_samples == fonte.timeline_end_samples
        assert destino.source_offset_samples == fonte.source_offset_samples


def _mesmo_fade(exportado, origem) -> None:
    """O fade exportado e o do AAF de origem: presente e do mesmo tamanho.

    Um ``None`` dos dois lados nao prova que o fade sobreviveu ao export: o
    fade tem de existir dos dois lados para a comparacao valer.
    """
    assert exportado is not None and origem is not None
    assert exportado.duration_samples == origem.duration_samples


def _importar_com_transition(tmp_path: Path) -> tuple[Path, Path]:
    """O AAF de origem com Transition e o .als que o import gerou."""
    wav = _wav_pcm(tmp_path / "fonte.wav", SR, SR * 4)
    origem = build_aaf_with_transition(
        tmp_path / "origem.aaf",
        wav,
        first_duration=DURACAO,
        second_duration=DURACAO,
        transition_length=CROSSFADE,
    )
    als = tmp_path / "saida.als"
    codigo = main(
        [
            "import",
            str(origem),
            str(als),
            "--media",
            "link",
            "--audio-dir",
            str(tmp_path),
            "--template",
            str(TEMPLATE),
        ]
    )
    assert codigo == 0
    return origem, als


# --- O par tem de ser de verdade --------------------------------------------


def test_starts_iguais_nao_viram_crossfade(tmp_path):
    """Dois clips que comecam no mesmo sample nao sao um crossfade.

    A sobreposicao seria o clip inteiro, e o que entra nao comecaria depois
    do que sai: nao ha crossfade a provar.
    """
    wav = _wav_pcm(tmp_path / "fonte.wav", SR, SR * 4)
    clip_a = _clip(
        0, ORIGEM, ORIGEM + DURACAO, 0, fade_out=FadeIR(kind="out", duration_samples=CROSSFADE)
    )
    clip_b = _clip(
        1,
        ORIGEM,
        ORIGEM + DURACAO,
        OFFSET_B,
        fade_in=FadeIR(kind="in", duration_samples=CROSSFADE),
    )
    destino = _escrever(tmp_path, _sessao(wav, [clip_a, clip_b]))

    node_a, node_b = _clips_do_als(destino)
    assert get_value(node_b, "Fades/CrossfadeInState") == "0"
    assert get_value(node_a, "CurrentEnd") != get_value(node_b, "CurrentStart")

    lido = read_session(destino, Report())
    assert lido.tracks[0].clips[0].timeline_end_samples == ORIGEM + DURACAO
    # O fade continua sendo o fade: o que nao existe e a geometria do crossfade.
    fade_in = lido.tracks[0].clips[1].fade_in
    assert fade_in is not None and fade_in.duration_samples == CROSSFADE


def test_sobreposicao_maior_que_o_clip_que_entra_nao_vira_crossfade(tmp_path):
    """A sobreposicao precisa caber inteira no clip que entra."""
    wav = _wav_pcm(tmp_path / "fonte.wav", SR, SR * 4)
    clip_a = _clip(
        0, ORIGEM, ORIGEM + DURACAO, 0, fade_out=FadeIR(kind="out", duration_samples=84000)
    )
    clip_b = _clip(
        1,
        ORIGEM + 12000,
        ORIGEM + 24000,
        OFFSET_B,
        fade_in=FadeIR(kind="in", duration_samples=84000),
    )
    destino = _escrever(tmp_path, _sessao(wav, [clip_a, clip_b]))

    node_a, node_b = _clips_do_als(destino)
    assert get_value(node_b, "Fades/CrossfadeInState") == "0"
    assert get_value(node_a, "CurrentEnd") != get_value(node_b, "CurrentStart")

    lido = read_session(destino, Report())
    _sem_extensao(lido, ORIGEM + DURACAO)


def test_fade_escalado_nao_reimpoe_o_comprimento_do_ir(tmp_path):
    """Fade que o escritor escalou nao pode voltar a valer o valor do IR.

    O fade in e o fade out somam mais que o clip: o escritor escala os dois
    para caber, e o fade out escrito passa a valer 22154 samples em vez de
    24000. O crossfade tem de valer o que esta escrito, nao o que o IR pedia.
    """
    wav = _wav_pcm(tmp_path / "fonte.wav", SR, SR * 4)
    clip_a = _clip(
        0,
        ORIGEM,
        ORIGEM + DURACAO,
        0,
        fade_in=FadeIR(kind="in", duration_samples=80000),
        fade_out=FadeIR(kind="out", duration_samples=CROSSFADE),
    )
    clip_b = _clip(
        1,
        ORIGEM + DURACAO - CROSSFADE,
        ORIGEM + DURACAO - CROSSFADE + DURACAO,
        OFFSET_B,
        fade_in=FadeIR(kind="in", duration_samples=CROSSFADE),
    )
    destino = _escrever(tmp_path, _sessao(wav, [clip_a, clip_b]))

    node_a, node_b = _clips_do_als(destino)
    # O fade out escrito e o escalado, nao o do IR.
    assert float(get_value(node_a, "Fades/FadeOutLength")) != pytest.approx(0.5)
    assert get_value(node_b, "Fades/CrossfadeInState") == "0"
    assert get_value(node_a, "CurrentEnd") != get_value(node_b, "CurrentStart")

    lido = read_session(destino, Report())
    _sem_extensao(lido, ORIGEM + DURACAO)


def test_fade_in_que_cabe_no_clip_que_sai_vira_crossfade(tmp_path):
    """O fade in e o fade out cabem no clip do IR, e o clip que sai nao e aparado.

    Na forma medida o clip que sai fica com o fim do AAF: o fade in de 60000 e
    o crossfade de 24000 cabem nos 96000 dele, e a volta e a do AAF.
    """
    wav = _wav_pcm(tmp_path / "fonte.wav", SR, SR * 4)
    clip_a = _clip(
        0,
        ORIGEM,
        ORIGEM + DURACAO,
        0,
        fade_in=FadeIR(kind="in", duration_samples=60000),
        fade_out=FadeIR(kind="out", duration_samples=CROSSFADE),
    )
    clip_b = _clip(
        1,
        ORIGEM + DURACAO - CROSSFADE,
        ORIGEM + DURACAO - CROSSFADE + DURACAO,
        OFFSET_B,
        fade_in=FadeIR(kind="in", duration_samples=CROSSFADE),
    )
    destino = _escrever(tmp_path, _sessao(wav, [clip_a, clip_b]))

    node_a, node_b = _clips_do_als(destino)
    # Os fades nao foram escalados: o IR comportava os dois.
    assert float(get_value(node_a, "Fades/FadeOutLength")) == pytest.approx(0.5)
    assert get_value(node_b, "Fades/CrossfadeInState") == "1"
    assert get_value(node_a, "CurrentEnd") == get_value(node_b, "CurrentStart")

    lido = read_session(destino, Report())
    assert _geometria(lido) == [
        (ORIGEM, ORIGEM + DURACAO),
        (ORIGEM + DURACAO - CROSSFADE, ORIGEM + DURACAO - CROSSFADE + DURACAO),
    ]
    clip_a = lido.tracks[0].clips[0]
    assert clip_a.fade_in is not None and clip_a.fade_in.duration_samples == 60000


# --- O leitor so estende com todas as provas --------------------------------


@pytest.mark.parametrize(
    "valor",
    ["ausente", "abc", "nan", "inf", "0.000000001", "1e-1000"],
)
def test_fade_in_do_que_entra_nao_zerado_nao_estende_a_geometria(tmp_path, valor):
    """O fade in do que entra tem de estar presente e valer zero.

    O zero e o numero do XML: um valor positivo que arredonda para zero nao
    serve, e um valor ausente, invalido ou infinito tambem nao. O "1e-1000" e o
    caso que o ``float`` apagaria: em ``Decimal`` ele continua positivo.
    """
    wav = _wav_pcm(tmp_path / "fonte.wav", SR, SR * 4)
    destino = _escrever(tmp_path, _sessao_do_par(wav))

    def mexer(root):
        clip_b = _segundo_clip(root)
        if valor == "ausente":
            _sem_fade_in(clip_b)
        else:
            _set(clip_b, "Fades/FadeInLength", valor)

    _editar(destino, mexer)

    lido = read_session(destino, Report())
    _sem_extensao(lido, ORIGEM + DURACAO)
    assert lido.tracks[0].clips[1].timeline_start_samples == ORIGEM + DURACAO
    assert lido.tracks[0].clips[1].fade_in is None


def test_fades_desligados_no_que_entra_nao_estendem_a_geometria(tmp_path):
    """Com o elemento Fade em false, o clip que entra nao tem fade nenhum.

    O fade in ausente e a prova de que nao ha crossfade, mas so quando os
    fades do clip estao ligadas: desligados, o leitor nao pode inventar a
    geometria a partir de um campo que nao vale.
    """
    wav = _wav_pcm(tmp_path / "fonte.wav", SR, SR * 4)
    destino = _escrever(tmp_path, _sessao_do_par(wav))

    def mexer(root):
        _fades_desligados(_segundo_clip(root))

    _editar(destino, mexer)

    lido = read_session(destino, Report())
    _sem_extensao(lido, ORIGEM + DURACAO)
    assert lido.tracks[0].clips[1].timeline_start_samples == ORIGEM + DURACAO
    assert lido.tracks[0].clips[1].fade_in is None


@pytest.mark.parametrize("marcador", ["inf", "-inf", "nan", "1e308", "abc"])
def test_out_marker_invalido_nao_extende_a_geometria(tmp_path, marcador):
    """Um OutMarker que nao e numero nao prova material nenhum na versao 2.

    Infinito, NaN e um numero tao grande que estoura a conversao abortavam a
    leitura ou inventavam geometria: o leitor recusa o par e a geometria antiga
    fica como esta.
    """
    wav = _wav_pcm(tmp_path / "fonte.wav", SR, SR * 4)
    destino = _escrever(tmp_path, _sessao_do_par(wav))
    _forma_v2(destino)

    def mexer(root):
        clip_a = root.findall(
            ".//AudioTrack/DeviceChain/MainSequencer/Sample/ArrangerAutomation/Events/AudioClip"
        )[0]
        _set(clip_a, "Loop/OutMarker", marcador)

    _editar(destino, mexer)

    lido = read_session(destino, Report())
    _sem_extensao(lido, ORIGEM + DURACAO - CROSSFADE)
    assert lido.tracks[0].clips[1].fade_in is None


def test_duracao_visual_zero_nao_vira_clip_inventado(tmp_path):
    """O clip que so aparece quando o leitor o estende nao estava na cena.

    O excesso de material e a prova do crossfade, mas um clip de duracao
    visual zero nao tem forma a devolver: estende-lo inventaria um clip que o
    arquivo nao tem. O clip continua la, do jeito que estava.
    """
    wav = _wav_pcm(tmp_path / "fonte.wav", SR, SR * 4)
    destino = _escrever(tmp_path, _sessao_do_par(wav))

    def mexer(root):
        clip_a, clip_b = root.findall(
            ".//AudioTrack/DeviceChain/MainSequencer/Sample/ArrangerAutomation/Events/AudioClip"
        )
        inicio = clip_a.find("CurrentStart").get("Value")
        clip_a.find("CurrentEnd").set("Value", inicio)
        clip_b.find("CurrentStart").set("Value", inicio)
        # O material que sobra passa a valer exatamente o crossfade.
        _set(clip_a, "Loop/OutMarker", "0.5")

    _editar(destino, mexer)

    lido = read_session(destino, Report())
    assert len(lido.tracks[0].clips) == 2
    clip_a = lido.tracks[0].clips[0]
    assert clip_a.timeline_end_samples == clip_a.timeline_start_samples
    assert lido.tracks[0].clips[1].fade_in is None


def test_par_so_simetrico_apos_clamping_nao_e_canonizado(tmp_path):
    """Um par que so ficou simetrico depois do clamping nao e o crossfade de L.

    O fade in e o fade out do clip que entra disputam o mesmo espaco, e o
    escritor reparte os dois: o XML fica simetrico, mas o IR do clip que entra
    ainda pede 2L de fade in. Canonizar ali trocaria o fade de 2L por um de L.
    """
    wav = _wav_pcm(tmp_path / "fonte.wav", SR, SR * 4)
    clip_a = _clip(
        0, ORIGEM, ORIGEM + DURACAO, 0, fade_out=FadeIR(kind="out", duration_samples=CROSSFADE)
    )
    clip_b = _clip(
        1,
        ORIGEM + DURACAO - CROSSFADE,
        ORIGEM + DURACAO - CROSSFADE + 2 * CROSSFADE,
        OFFSET_B,
        fade_in=FadeIR(kind="in", duration_samples=2 * CROSSFADE),
        fade_out=FadeIR(kind="out", duration_samples=2 * CROSSFADE),
    )
    destino = _escrever(tmp_path, _sessao(wav, [clip_a, clip_b]))

    node_a, node_b = _clips_do_als(destino)
    assert get_value(node_b, "Fades/CrossfadeInState") == "0"
    assert get_value(node_a, "CurrentEnd") != get_value(node_b, "CurrentStart")

    lido = read_session(destino, Report())
    assert lido.tracks[0].clips[0].timeline_end_samples == ORIGEM + DURACAO


# --- A ordem dos clips nao decide -------------------------------------------


def test_par_reverso_da_a_mesma_geometria(tmp_path):
    """Com o par na ordem inversa, o crossfade sai igual.

    A lista auxiliar e ordenada pelo tempo antes de procurar o vizinho: nem
    o IR nem a ordem gravada no XML mudam.
    """
    wav = _wav_pcm(tmp_path / "fonte.wav", SR, SR * 4)
    sessao = _sessao_do_par(wav)
    clip_a, clip_b = sessao.tracks[0].clips
    sessao.tracks[0].clips = [clip_b, clip_a]
    destino = _escrever(tmp_path, sessao)

    nodes = _clips_do_als(destino)
    node_a, node_b = sorted(nodes, key=lambda node: float(get_value(node, "CurrentStart")))
    assert get_value(node_a, "CurrentEnd") == get_value(node_b, "CurrentStart")
    assert get_value(node_b, "Fades/CrossfadeInState") == "1"

    lido = read_session(destino, Report())
    assert _geometria(lido) == [
        (ORIGEM, ORIGEM + DURACAO),
        (ORIGEM + DURACAO - CROSSFADE, ORIGEM + DURACAO - CROSSFADE + DURACAO),
    ]
    por_inicio = sorted(lido.tracks[0].clips, key=lambda clip: clip.timeline_start_samples)
    assert por_inicio[1].fade_in is not None
    assert por_inicio[1].fade_in.duration_samples == CROSSFADE
    # A ordem do IR continua a que estava, e o indice do evento segue a ordem
    # gravada: ordenar a lista auxiliar nao mexe em nenhum dos dois.
    assert [clip.timeline_start_samples for clip in lido.tracks[0].clips] == [
        ORIGEM + DURACAO - CROSSFADE,
        ORIGEM,
    ]
    assert [clip.event_index for clip in lido.tracks[0].clips] == [0, 1]


def test_cadeia_embaralhada_da_a_mesma_geometria(tmp_path):
    """Com a cadeia fora de ordem, os dois crossfades saem iguais."""
    wav = _wav_pcm(tmp_path / "fonte.wav", SR, SR * 4)
    sessao = _sessao_da_cadeia(wav)
    primeiro, segundo, terceiro = sessao.tracks[0].clips
    sessao.tracks[0].clips = [terceiro, primeiro, segundo]
    destino = _escrever(tmp_path, sessao)

    lido = read_session(destino, Report())
    esperado = sorted(
        (clip.timeline_start_samples, clip.timeline_end_samples)
        for clip in (primeiro, segundo, terceiro)
    )
    assert _geometria(lido) == esperado
    por_inicio = sorted(lido.tracks[0].clips, key=lambda clip: clip.timeline_start_samples)
    for clip in por_inicio[1:]:
        assert clip.fade_in is not None
        assert clip.fade_in.duration_samples == CROSSFADE


# --- O pipeline inteiro -----------------------------------------------------


def test_pipeline_cli_do_aaf_com_transition(tmp_path):
    """AAF com Transition entra, fica verificado e volta a AAF com a mesma forma.

    O AAF de origem traz um clip, uma Transition e outro clip. O .als
    gerado tem os dois clips encostados, o verify aprova com desvio zero e o
    AAF exportado tem a mesma geometria, os mesmos offsets e os mesmos
    fades do AAF de origem.
    """
    origem, als = _importar_com_transition(tmp_path)

    clip_a, clip_b = _clips_do_als(als)
    assert get_value(clip_a, "CurrentEnd") == get_value(clip_b, "CurrentStart")
    assert get_value(clip_b, "Fades/CrossfadeInState") == "1"
    assert float(get_value(clip_b, "Fades/FadeInLength")) == 0.0

    assert main(["verify", str(origem), str(als)]) == 0

    exportado = tmp_path / "exportado.aaf"
    assert main(["export", str(als), str(exportado)]) == 0

    clips_origem = _clips_do_aaf(origem)
    clips_exportado = _clips_do_aaf(exportado)
    _mesma_geometria(clips_exportado, clips_origem)
    _mesmo_fade(clips_exportado[0].fade_out, clips_origem[0].fade_out)
    _mesmo_fade(clips_exportado[1].fade_in, clips_origem[1].fade_in)
