"""O material do arquivo decide se o escritor pode usar a forma nativa.

O leitor so devolve a geometria logica do crossfade quando o material do
arquivo cobre o clip que sai. O escritor precisa da mesma prova: publicar a
forma nativa sem ela gera um .als que volta truncado, porque o leitor recusa
estender o clip que sai sem material.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aafton.als_crossfade import material_cobre
from aafton.als_paths import AUDIO_CLIP_FIELDS
from aafton.als_xml import get_value, load_als
from aafton.live_compat import importar
from aafton.verify import verify_conversion
from tests import aaf_factory

SR = 48000
DURACAO_DO_CLIP = 96000


# --- A regra do material ----------------------------------------------------


@pytest.mark.parametrize(
    ("duracao", "taxa", "taxa_da_sessao", "usado", "esperado"),
    [
        (None, 48000, 48000, 10, True),
        (100, 44100, 48000, 1000, True),
        (96000, 48000, 48000, 96000, True),
        (95999, 48000, 48000, 96000, False),
    ],
)
def test_material_cobre(duracao, taxa, taxa_da_sessao, usado, esperado):
    """Sem duracao ou com taxa outra, nao ha prova contra o trecho."""
    assert material_cobre(duracao, taxa, taxa_da_sessao, usado) is esperado


# --- A ida e a volta com o arquivo que responde ----------------------------


def _clips_do_als(caminho: Path) -> list:
    """Os clips de audio do .als, do mais antigo para o mais novo."""
    raiz = load_als(caminho)
    clips = raiz.findall(
        ".//AudioTrack/DeviceChain/MainSequencer/Sample/ArrangerAutomation/Events/AudioClip"
    )
    return sorted(
        clips,
        key=lambda no: float(get_value(no, AUDIO_CLIP_FIELDS["current_start"]) or 0),
    )


def _prepara_o_caso(tmp_path, frames):
    """Monta o material do arquivo e importa o AAF, devolvendo a conversao."""
    pasta = tmp_path / "caso"
    midia = pasta / "midia"
    midia.mkdir(parents=True)
    wav = midia / "fonte.wav"
    wav.write_bytes(aaf_factory.wav_bytes(frames, channels=1, sample_rate=SR))
    aaf = pasta / "fonte.aaf"
    aaf_factory.build_aaf_with_transition(aaf, wav)
    conversao = importar(aaf, pasta / "caso.als", midia)
    assert conversao.codigo == 0
    return conversao


def _confere_a_sobreposicao_antiga(primeiro, segundo):
    """Sem material que cubra o clip, o par fica com a sobreposicao antiga."""
    assert get_value(segundo, AUDIO_CLIP_FIELDS["crossfade_in_state"]) == "0"
    assert float(get_value(primeiro, AUDIO_CLIP_FIELDS["current_end"])) == 12.0


def _confere_o_par_canonico(primeiro, segundo):
    """Com material que cobre o clip, a forma nativa entra e o par canonico fecha."""
    assert get_value(segundo, AUDIO_CLIP_FIELDS["crossfade_in_state"]) == "1"
    assert float(get_value(segundo, AUDIO_CLIP_FIELDS["fade_in_length"])) == 0.0
    assert float(get_value(primeiro, AUDIO_CLIP_FIELDS["current_end"])) == 11.5
    assert float(get_value(segundo, AUDIO_CLIP_FIELDS["current_start"])) == 11.5


def _confere_o_desvio_zero(conversao):
    """A volta fecha com desvio zero nas faixas de audio."""
    resultado = verify_conversion(conversao.aaf, conversao.als)
    linhas = [linha for linha in resultado.rows if linha.is_audio]
    assert len(linhas) == 2
    for linha in linhas:
        assert linha.delta_samples == 0
        assert linha.delta_end_samples == 0


@pytest.mark.parametrize("frames", [1, 95999, 96000, 48000 * 70])
def test_a_forma_nativa_so_sai_quando_o_leitor_consegue_provar_a_volta(tmp_path, frames):
    """O .als canonico so sai quando o material do arquivo cobre o clip que sai.

    Com o arquivo curto (1 ou 95999 samples) o par fica com a sobreposicao
    antiga: o leitor nao conseguiria devolver a geometria, e o .als canonico
    voltaria truncado. Com o arquivo do tamanho do clip (96000 samples) ou
    maior, a forma nativa entra e a volta fecha com desvio zero.
    """
    conversao = _prepara_o_caso(tmp_path, frames)

    clips = _clips_do_als(conversao.als)
    assert len(clips) == 2
    primeiro, segundo = clips
    if frames < DURACAO_DO_CLIP:
        _confere_a_sobreposicao_antiga(primeiro, segundo)
    else:
        _confere_o_par_canonico(primeiro, segundo)

    _confere_o_desvio_zero(conversao)
