import wave


from aafton.media_extract import parse_bext, write_wav_with_bext
from tests.aaf_factory import wav_bytes


def test_wav_escrito_e_valido_e_tem_bext(tmp_path):
    destino = tmp_path / "saida.wav"
    write_wav_with_bext(
        destino,
        pcm=b"\x00\x00" * 48000,
        channels=1,
        sample_rate=48000,
        bits_per_sample=16,
        time_reference=172800000,
        description="cena12 tk3",
    )
    with wave.open(str(destino), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getframerate() == 48000
        assert handle.getnframes() == 48000
    assert parse_bext(destino)["time_reference"] == 172800000


def test_time_reference_de_uma_hora_sobrevive(tmp_path):
    destino = tmp_path / "saida.wav"
    uma_hora = 172800000
    write_wav_with_bext(
        destino,
        pcm=b"\x00\x00" * 1000,
        channels=1,
        sample_rate=48000,
        bits_per_sample=16,
        time_reference=uma_hora,
    )
    assert parse_bext(destino)["time_reference"] == uma_hora


def test_time_reference_acima_de_32_bits_sobrevive(tmp_path):
    """TimeReference e 64 bits; usar 32 estoura em pouco mais de 12 horas a 48 kHz."""
    destino = tmp_path / "saida.wav"
    grande = 5_000_000_000
    write_wav_with_bext(
        destino,
        pcm=b"\x00\x00" * 10,
        channels=1,
        sample_rate=48000,
        bits_per_sample=16,
        time_reference=grande,
    )
    assert parse_bext(destino)["time_reference"] == grande


def test_descricao_chega_no_bext(tmp_path):
    destino = tmp_path / "saida.wav"
    write_wav_with_bext(
        destino,
        pcm=b"\x00\x00" * 10,
        channels=1,
        sample_rate=48000,
        bits_per_sample=16,
        time_reference=0,
        description="cena12 tk3",
    )
    assert parse_bext(destino)["description"].startswith("cena12 tk3")


def test_estereo_interleaved_conta_frames_certo(tmp_path):
    destino = tmp_path / "saida.wav"
    write_wav_with_bext(
        destino,
        pcm=b"\x00\x00\x00\x00" * 24000,
        channels=2,
        sample_rate=48000,
        bits_per_sample=16,
        time_reference=0,
    )
    with wave.open(str(destino), "rb") as handle:
        assert handle.getnframes() == 24000


def test_wav_sem_bext_devolve_none(tmp_path):
    destino = tmp_path / "sem_bext.wav"
    destino.write_bytes(wav_bytes(1000))
    assert parse_bext(destino) is None


def test_read_aaf_com_extract_dir_nao_quebra_em_aaf_linkado(tmp_path):
    from aafton.aaf_read import read_aaf
    from aafton.report import Report
    from tests.aaf_factory import build_linked_aaf, wav_bytes

    wav = tmp_path / "cena12_tk3.wav"
    wav.write_bytes(wav_bytes(48000))
    aaf = build_linked_aaf(tmp_path / "cena.aaf", [(0, 24000, 0)], wav)
    session = read_aaf(aaf, Report(strict=False), extract_dir=tmp_path / "extraidos")
    assert session.tracks[0].clips
