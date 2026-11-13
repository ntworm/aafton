import aaf2
import pytest

from tests.aaf_factory import build_linked_aaf, wav_bytes


@pytest.fixture
def wav(tmp_path):
    caminho = tmp_path / "cena12_tk3.wav"
    caminho.write_bytes(wav_bytes(48000 * 60))
    return caminho


def test_wav_gerado_tem_cabecalho_valido(wav):
    import wave

    with wave.open(str(wav), "rb") as handle:
        assert handle.getframerate() == 48000
        assert handle.getnchannels() == 1


def test_aaf_gerado_abre_no_pyaaf2(tmp_path, wav):
    destino = build_linked_aaf(tmp_path / "cena.aaf", [(0, 96000, 0)], wav)
    with aaf2.open(str(destino), "r") as f:
        assert any(type(mob).__name__ == "CompositionMob" for mob in f.content.mobs)


def test_aaf_gerado_tem_a_quantidade_certa_de_source_clips(tmp_path, wav):
    destino = build_linked_aaf(tmp_path / "cena.aaf", [(0, 96000, 0), (192000, 48000, 24000)], wav)
    with aaf2.open(str(destino), "r") as f:
        composition = next(m for m in f.content.mobs if type(m).__name__ == "CompositionMob")
        slot = composition.slots[0]
        tipos = [type(c).__name__ for c in slot.segment.components]
    assert tipos.count("SourceClip") == 2
    assert tipos.count("Filler") == 1


def test_aaf_gerado_tem_timecode_slot(tmp_path, wav):
    destino = build_linked_aaf(tmp_path / "cena.aaf", [(0, 96000, 0)], wav)
    with aaf2.open(str(destino), "r") as f:
        composition = next(m for m in f.content.mobs if type(m).__name__ == "CompositionMob")
        timecodes = [
            slot.segment for slot in composition.slots if type(slot.segment).__name__ == "Timecode"
        ]
    assert len(timecodes) == 1
    assert timecodes[0].start == 90000
