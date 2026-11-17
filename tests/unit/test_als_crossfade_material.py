"""O material do arquivo e a forma nativa do crossfade.

A regra ``material_cobre`` segue servindo a leitura dos sets de
``format_version`` <= 2. Na forma medida no Live o clip que sai nao precisa de
material alem do AAF, entao a forma nativa sai com qualquer arquivo.
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


def _confere_o_par_canonico(primeiro, segundo):
    """A forma nativa medida: o clip que entra comeca no fim do que sai no AAF."""
    assert get_value(segundo, AUDIO_CLIP_FIELDS["crossfade_in_state"]) == "1"
    assert float(get_value(segundo, AUDIO_CLIP_FIELDS["fade_in_length"])) == 0.0
    assert float(get_value(primeiro, AUDIO_CLIP_FIELDS["current_end"])) == 12.0
    assert float(get_value(segundo, AUDIO_CLIP_FIELDS["current_start"])) == 12.0
    # O LoopStart do que entra avanca o crossfade (0,5 s) na fonte.
    assert float(get_value(segundo, AUDIO_CLIP_FIELDS["loop_start"])) == 0.5


def _confere_o_desvio_zero(conversao):
    """A volta fecha com desvio zero nas faixas de audio."""
    resultado = verify_conversion(conversao.aaf, conversao.als)
    linhas = [linha for linha in resultado.rows if linha.is_audio]
    assert len(linhas) == 2
    assert resultado.ok
    for linha in linhas:
        assert linha.delta_samples == 0
        # o clip que pede 1 sample alem do arquivo sai cortado no fim da midia
        if linha.status == "trimmed to media end":
            assert linha.delta_end_samples == -1
            continue
        assert linha.delta_end_samples == 0


@pytest.mark.parametrize("frames", [1, 95999, 96000, 48000 * 70])
def test_a_forma_nativa_nao_depende_do_material_do_clip_que_sai(tmp_path, frames):
    """A forma medida nao pede material alem do AAF ao clip que sai.

    O clip que sai fica com o trecho do AAF, e o que entra toca o crossfade
    com o material antes do LoopStart novo, que e o offset do AAF. Com o
    arquivo curto (1 ou 95999 samples), do tamanho do clip (96000) ou maior, a
    forma nativa entra e a volta fecha com o mesmo desvio da leitura do AAF.
    """
    assert DURACAO_DO_CLIP == 96000
    conversao = _prepara_o_caso(tmp_path, frames)

    clips = _clips_do_als(conversao.als)
    assert len(clips) == 2
    primeiro, segundo = clips
    _confere_o_par_canonico(primeiro, segundo)

    _confere_o_desvio_zero(conversao)
