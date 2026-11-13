"""Cobertura do verify: evento comparado nao e slot mapeado.

O verify contava os slots do sidecar e chamava isso de cobertura: um .als com a
segunda faixa vazia ainda saia como "2 de 2 slots comparados". A cobertura agora
conta os eventos comparados depois da comparacao; o mapeamento de slots aparece
separado, e sem mapa nao ha identidade a inventar.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from aafton.aaf_write import write_aaf
from aafton.als_write import WriteOptions, write_session
from aafton.ir import TrackIR
from aafton.report import Report
from aafton.sidecar import sidecar_path_for
from aafton.verify import VerifyResult, verify_conversion
from aafton.verify_coverage import (
    formatar_cobertura,
    mapeamento_de_slots,
    universo_de_slots,
)
from tests.aaf_factory import wav_bytes
from tests.unit.test_verify_integrity import UMA_HORA, _sessao_minima

TEMPLATE = Path("templates/live12.als")

pytestmark = pytest.mark.skipif(
    not TEMPLATE.exists(), reason="Portao B: templates/live12.als ainda nao foi autorado"
)


@pytest.fixture
def wav(tmp_path):
    caminho = tmp_path / "som.wav"
    caminho.write_bytes(wav_bytes(48000 * 10))
    return caminho


def _origem(wav, com_video=False):
    """Duas faixas de audio com um clip cada; com_video adiciona a faixa de video."""
    sessao = _sessao_minima(wav, num_clips=1)
    segunda = copy.deepcopy(sessao.tracks[0])
    segunda.id = "t1"
    segunda.source_index = 1
    segunda.original_source_indices = [1]
    segunda.name = "AUDIO2"
    for indice, clip in enumerate(segunda.clips):
        clip.id = f"d{indice}"
        clip.track_id = "t1"
    sessao.tracks.append(segunda)
    if com_video:
        video = TrackIR(
            id="tv", source_index=7, name="VIDEO", kind="audio", channel_count=1, clips=[]
        )
        video.original_source_indices = [7]
        video.is_video = True
        sessao.tracks.insert(0, video)
    return sessao


def _par(tmp_path, wav, segunda_vazia=False):
    """AAF com dois slots de audio e o .als correspondente."""
    aaf = tmp_path / "origem.aaf"
    als = tmp_path / "destino.als"
    origem = _origem(wav)
    write_aaf(origem, aaf, Report(strict=True))
    destino = copy.deepcopy(origem)
    if segunda_vazia:
        destino.tracks[1].clips = []
    write_session(
        destino,
        TEMPLATE,
        als,
        WriteOptions(sample_rate=48000, live_origin_samples=UMA_HORA),
        Report(strict=True),
    )
    return aaf, als


def test_faixa_de_destino_vazia_nao_vira_cobertura_total(tmp_path, wav):
    """Duas faixas mapeadas e a segunda sem clip: 1 de 2 eventos, nao 2 de 2."""
    aaf, als = _par(tmp_path, wav, segunda_vazia=True)

    resultado = verify_conversion(aaf, als, override_tolerance_samples=0)

    assert resultado.audio_expected == 2
    assert resultado.audio_compared == 1
    assert resultado.audio_missing == 1
    assert resultado.audio_extra == 0
    assert resultado.ok is False
    assert not any(linha.status == "extra in the ALS" for linha in resultado.rows)
    assert resultado.coverage_msg.startswith("Coverage: 1 of 2 audio events compared")
    assert "(1 missing, 0 extra)" in resultado.coverage_msg
    assert "slots compared" not in resultado.coverage_msg
    assert "Mapping: 2 of 2 slots mapped" in resultado.coverage_msg


def test_sem_mapa_a_cobertura_conta_eventos_sem_inventar_identidade(tmp_path, wav):
    """Sem mapa de identidade a cobertura mede eventos e nao inventa mapeamento."""
    aaf, als = _par(tmp_path, wav)
    sidecar = sidecar_path_for(als)
    dados = json.loads(sidecar.read_text(encoding="utf-8"))
    del dados["audio_source_indices"]
    sidecar.write_text(json.dumps(dados), encoding="utf-8")

    resultado = verify_conversion(aaf, als, override_tolerance_samples=0)

    assert resultado.ok is True
    assert resultado.audio_expected == 2
    assert resultado.audio_compared == 2
    assert resultado.coverage_msg == "Coverage: 2 of 2 audio events compared (0 missing, 0 extra)"
    assert "Mapping" not in resultado.coverage_msg


def test_o_universo_de_slots_exclui_video(wav):
    """O indice do video nao entra no universo de slots de audio."""
    origem = _origem(wav, com_video=True)

    assert universo_de_slots(origem) == {0, 1}
    completo = mapeamento_de_slots(origem, [[0, 1], [7]])
    assert completo.slots_origem == 2
    assert completo.slots_mapeados == 2
    assert completo.nao_mapeados == ()


def test_o_mapeamento_aponta_o_slot_de_audio_sem_mapa(wav):
    """Slot de audio sem mapeamento aparece na linha com o indice que faltou."""
    parcial = mapeamento_de_slots(_origem(wav, com_video=True), [[0], [7]])

    assert parcial.slots_mapeados == 1
    assert parcial.nao_mapeados == (1,)
    texto = formatar_cobertura(
        VerifyResult(audio_compared=1, audio_expected=2, audio_missing=1), parcial
    )
    assert texto.startswith("Coverage: 1 of 2 audio events compared")
    assert "Mapping: 1 of 2 slots mapped" in texto
    assert "1 not mapped: 1" in texto
    assert "slots compared" not in texto
