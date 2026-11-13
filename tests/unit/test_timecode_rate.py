import pytest
from aafton.timecode import TimecodeRate


def test_nominal_fps_de_taxas_inteiras():
    assert TimecodeRate(24, 1).nominal_fps == 24
    assert TimecodeRate(25, 1).nominal_fps == 25
    assert TimecodeRate(30, 1).nominal_fps == 30


def test_nominal_fps_de_taxas_fracionarias():
    assert TimecodeRate(24000, 1001).nominal_fps == 24
    assert TimecodeRate(30000, 1001).nominal_fps == 30
    assert TimecodeRate(60000, 1001).nominal_fps == 60


def test_frames_pulados_por_minuto_derivado_da_taxa():
    assert TimecodeRate(30000, 1001, drop_frame=True).dropped_per_minute == 2
    assert TimecodeRate(60000, 1001, drop_frame=True).dropped_per_minute == 4


def test_taxa_sem_drop_frame_nao_pula_nada():
    assert TimecodeRate(25, 1).dropped_per_minute == 0


def test_denominador_invalido_recusado():
    with pytest.raises(ValueError, match="denominator"):
        TimecodeRate(24, 1000)


def test_drop_frame_recusado_em_taxa_que_nao_suporta():
    with pytest.raises(ValueError, match="drop frame"):
        TimecodeRate(25, 1, drop_frame=True)
    with pytest.raises(ValueError, match="drop frame"):
        TimecodeRate(24000, 1001, drop_frame=True)
