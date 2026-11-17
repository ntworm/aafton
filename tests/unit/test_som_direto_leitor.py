"""Leitor de som direto: takes, entradas, nomes, datas e blocos, com WAVs sinteticos."""

import random
import struct
from pathlib import Path

import pytest

from aafton.ir import PRODUCTION_LAYOUT
from aafton.report import Report
from aafton.som_direto import read_production

RATE = 48000
HORA = 3600 * RATE
SEP = "·"


def _chunk(chunk_id: bytes, payload: bytes) -> bytes:
    return chunk_id + struct.pack("<I", len(payload)) + payload + b"\x00" * (len(payload) % 2)


def bext(time_reference: int, data: str = "") -> bytes:
    """bext com OriginationDate e TimeReference, ate o campo Version."""
    cabeca = b"MixPre".ljust(256, b"\x00") + b"SoundDevices".ljust(32, b"\x00") + b"\x00" * 32
    campos = data.encode().ljust(10, b"\x00") + b"10:00:00"
    return cabeca + campos + struct.pack("<QH", time_reference, 1) + b"\x00" * 254


def ixml(scene, take, family, entradas, *, circled=None, rate="24/1", flag="NDF") -> bytes:
    """iXML no estilo MixPre: entradas como (CHANNEL_INDEX, NAME) na ordem do arquivo."""
    tracks = "".join(
        f"<TRACK><CHANNEL_INDEX>{canal}</CHANNEL_INDEX><INTERLEAVE_INDEX>{posicao}"
        f"</INTERLEAVE_INDEX><NAME>{nome}</NAME></TRACK>"
        for posicao, (canal, nome) in enumerate(entradas, start=1)
    )
    circulo = "" if circled is None else f"<CIRCLED>{'TRUE' if circled else 'FALSE'}</CIRCLED>"
    texto = (
        f"<BWFXML><SCENE>{scene}</SCENE><TAKE>{take}</TAKE><TAPE>T1</TAPE>{circulo}"
        f"<NOTE>nota</NOTE><FILE_SET><FAMILY_UID>{family}</FAMILY_UID></FILE_SET>"
        f"<SPEED><TIMECODE_RATE>{rate}</TIMECODE_RATE><TIMECODE_FLAG>{flag}</TIMECODE_FLAG>"
        f"</SPEED><TRACK_LIST><TRACK_COUNT>{len(entradas)}</TRACK_COUNT>{tracks}</TRACK_LIST>"
        "</BWFXML>"
    )
    return texto.encode("utf-8")


def wav(path: Path, channels: int, frames: int, *, rate=RATE, extras=(), semente=0) -> Path:
    """WAV PCM 16 bits com amostras aleatorias e chunks extras antes do data."""
    align = channels * 2
    fmt = struct.pack("<HHIIHH", 1, channels, rate, rate * align, align, 16)
    dados = random.Random(semente or str(path)).randbytes(frames * align)
    corpo = b"WAVE" + _chunk(b"fmt ", fmt)
    for chunk_id, payload in extras:
        corpo += _chunk(chunk_id, payload)
    corpo += _chunk(b"data", dados)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(corpo)) + corpo)
    return path


def mixpre(path: Path, tc: int, data: str, scene, take, entradas, *, circled=None, frames=4800):
    meta = ixml(scene, take, f"{path.stem}-fam", entradas, circled=circled)
    extras = ((b"bext", bext(tc, data)), (b"iXML", meta))
    return wav(path, len(entradas), frames, extras=extras)


def duas_diarias(raiz: Path) -> Path:
    """Duas diarias MixPre com timecode sobreposto, a segunda mais cedo no dia."""
    d1, d2 = raiz / "05-05-25", raiz / "05-06-25"
    dia1, dia2 = "2025-05-05", "2025-06-05"
    a = [(3, "NEUMANN"), (4, "CCM8L")]
    b = [(3, "neumann"), (4, "MK41")]
    c = [(3, "NEUMANN"), (4, "MK41")]
    mixpre(d1 / "A.wav", 10 * HORA, dia1, "12A", "005", a, circled=True)
    mixpre(d1 / "B.wav", 10 * HORA + 5 * RATE, dia1, "12A", "006", b, circled=False)
    mixpre(d2 / "C.wav", 9 * HORA, dia2, "13", "001", c)
    mixpre(d2 / "D.wav", 9 * HORA + 3 * RATE, dia2, "13", "002", [(3, "Neumann")], circled=True)
    return raiz


def _clips(session):
    return {t.name: [(c.name, c.timeline_start_samples) for c in t.clips] for t in session.tracks}


def _codigos(report: Report) -> list[str]:
    return [issue.code for issue in report.issues]


def test_duas_diarias_em_blocos_na_ordem_das_datas(tmp_path):
    report = Report(strict=False)
    session = read_production(duas_diarias(tmp_path / "som"), report)
    assert session.layout == PRODUCTION_LAYOUT
    assert session.sample_rate == RATE
    inicio2 = -(-(10 * RATE + 5 * RATE + 4800 + 10 * RATE) // RATE) * RATE
    assert [b["start_samples"] for b in session.layout_blocks] == [0, inicio2]
    assert [b["date"] for b in session.layout_blocks] == ["2025-05-05", "2025-06-05"]
    assert [b["timecode_samples"] for b in session.layout_blocks] == [10 * HORA, 9 * HORA]
    clips = _clips(session)
    assert clips[f"In 3 {SEP} NEUMANN"] == [
        ("12A/005", 10 * RATE),
        ("12A/006", 15 * RATE),
        ("13/001", inicio2 + 10 * RATE),
        ("13/002", inicio2 + 13 * RATE),
    ]
    assert clips["In 4"] == [
        (f"12A/005 {SEP} CCM8L", 10 * RATE),
        (f"12A/006 {SEP} MK41", 15 * RATE),
        (f"13/001 {SEP} MK41", inicio2 + 10 * RATE),
    ]
    assert [m.name for m in session.markers] == [
        f"2025-05-05 {SEP} TC 10:00:00:00",
        f"2025-06-05 {SEP} TC 09:00:00:00",
    ]
    assert [m.position_samples for m in session.markers] == [10 * RATE, inicio2 + 10 * RATE]
    clip = session.tracks[0].clips[0]
    assert clip.raw["timecode_samples"] == 10 * HORA
    assert session.media_by_id(clip.media_id).origin_timecode_samples == 10 * HORA
    assert "Circled: yes" in clip.raw["production"]["annotation"]
    assert "PRODUCTION_TC_RATE_ASSUMED" not in _codigos(report)


def test_nota_de_campo_opcional_nao_vai_para_o_relatorio(tmp_path):
    report = Report(strict=False)
    read_production(duas_diarias(tmp_path / "som"), report)
    mensagens = " ".join(issue.message for issue in report.issues)
    assert "FAMILY_NAME" not in mensagens
    assert "PROJECT" not in mensagens


def test_filtro_circled_conta_os_takes(tmp_path):
    raiz = duas_diarias(tmp_path / "som")
    report = Report(strict=False)
    session = read_production(raiz, report, circled_only=True)
    nomes = sorted(c.name.split(f" {SEP} ")[0] for c in session.all_clips())
    assert nomes == ["12A/005", "12A/005", "13/002"]
    fora = next(i for i in report.issues if i.code == "PRODUCTION_CIRCLED_ONLY")
    assert fora.message == "2 takes left out because they are not circled"
    total = next(i for i in report.issues if i.code == "PRODUCTION_TAKES")
    assert total.context == {"takes": 4, "circled": 2}


def test_tascam_sem_metadados_com_s2_faltando(tmp_path):
    dia = tmp_path / "som" / "27_11"
    wav(dia / "TASCAM_0112S1.wav", 1, 2400)
    wav(dia / "TASCAM_0112S2.wav", 1, 2400)
    wav(dia / "TASCAM_0113S1.wav", 1, 3000)
    report = Report(strict=False)
    session = read_production(tmp_path / "som", report)
    clips = _clips(session)
    segundo = 10 * RATE + 2400 + 2 * RATE
    assert clips["Ch 1"] == [("TASCAM_0112S1", 10 * RATE), ("TASCAM_0113S1", segundo)]
    assert clips["Ch 2"] == [("TASCAM_0112S2", 10 * RATE)]
    assert session.markers[0].name == f"27_11 {SEP} no TC"
    codigos = _codigos(report)
    assert "PRODUCTION_NO_TIMECODE" in codigos
    assert "PRODUCTION_DAY_UNDATED" in codigos
    assert "PRODUCTION_TC_RATE_ASSUMED" in codigos
    assert session.timecode_rate.num == 24


def test_zoom_com_relogio_zerado_ordena_pela_pasta(tmp_path):
    raiz = tmp_path / "aguas"
    wav(raiz / "som - 27_11" / "241127_0001.wav", 2, 1200)
    wav(raiz / "som - 23_09" / "000109_0001.wav", 2, 1200)
    wav(raiz / "som - 24_09" / "240924_0001.wav", 2, 1200)
    report = Report(strict=False)
    session = read_production(raiz, report)
    blocos = session.layout_blocks
    assert [b["day"] for b in blocos] == ["som - 23_09", "som - 24_09", "som - 27_11"]
    assert [b["date"] for b in blocos] == ["2024-09-23", "2024-09-24", "2024-11-27"]
    unset = [i for i in report.issues if i.code == "PRODUCTION_CLOCK_UNSET"]
    assert [i.context["day"] for i in unset] == ["som - 23_09"]
    assert "clock looks unset" in unset[0].message
    assert sorted(t.name for t in session.tracks) == ["Ch 1", "Ch 2"]
    sem_tc = [i for i in report.issues if i.code == "PRODUCTION_NO_TIMECODE"]
    assert len(sem_tc) == 3


def test_taxas_misturadas_sao_recusadas(tmp_path):
    wav(tmp_path / "dia" / "a.wav", 1, 100)
    wav(tmp_path / "dia" / "b.wav", 1, 100, rate=44100)
    with pytest.raises(ValueError, match="mixed sample rates"):
        read_production(tmp_path / "dia", Report(strict=False))


def test_rf64_e_arquivo_ilegivel_ficam_de_fora_com_nota(tmp_path):
    dia = tmp_path / "dia"
    wav(dia / "bom.wav", 1, 100)
    (dia / "grande.wav").write_bytes(b"RF64" + b"\xff" * 4 + b"WAVE" + b"\x00" * 64)
    (dia / "lixo.wav").write_bytes(b"nada")
    report = Report(strict=False)
    session = read_production(dia, report)
    assert [t.name for t in session.tracks] == ["Ch 1"]
    notas = {i.message for i in report.issues if i.code == "PRODUCTION_FILE_NOTE"}
    assert "RF64 not supported yet; file left out (1 file)" in notas
    assert "unreadable WAV file left out (1 file)" in notas


def meia_noite(raiz: Path, *, com_datas: bool) -> Path:
    """Diaria noturna: dois takes antes e um depois da meia-noite, na ordem do nome."""
    dia = raiz / "05-05-25"
    datas = ("2025-05-05", "2025-05-05", "2025-05-06") if com_datas else ("", "", "")
    tcs = (22 * HORA, 23 * HORA, HORA // 2)
    for nome, tc, data in zip(("A.wav", "B.wav", "C.wav"), tcs, datas):
        mixpre(dia / nome, tc, data, "40", nome[0], [(3, "BOOM")])
    return raiz


@pytest.mark.parametrize("com_datas", [True, False], ids=["bext_dates", "timecode_drop"])
def test_take_depois_da_meia_noite_vai_depois_dos_da_noite(tmp_path, com_datas):
    report = Report(strict=False)
    session = read_production(meia_noite(tmp_path / "som", com_datas=com_datas), report)
    inicios = [c.timeline_start_samples for c in session.tracks[0].clips]
    assert inicios == [10 * RATE, 10 * RATE + HORA, 10 * RATE + 2 * HORA + HORA // 2]
    (bloco,) = session.layout_blocks
    assert bloco["timecode_samples"] == 22 * HORA
    assert session.markers[0].name == f"2025-05-05 {SEP} TC 22:00:00:00"
    notas = [i for i in report.issues if i.code == "PRODUCTION_MIDNIGHT"]
    assert [i.message for i in notas] == [
        "05-05-25: takes after midnight placed after the evening takes"
    ]
    assert notas[0].severity == "info" and notas[0].context["takes"] == 1


def gravadores_no_mesmo_canal(raiz: Path) -> Path:
    """Dois gravadores sem iXML, os dois em Ch 1, rodando no mesmo timecode."""
    dia = raiz / "05-05-25"
    meta = ((b"bext", bext(10 * HORA, "2025-05-05")),)
    wav(dia / "lapela.wav", 1, 4800, extras=meta)
    wav(dia / "boom.wav", 1, 4800, extras=meta)
    return raiz


def test_takes_sobrepostos_na_mesma_entrada_vao_para_outra_track(tmp_path):
    report = Report(strict=False)
    session = read_production(gravadores_no_mesmo_canal(tmp_path / "som"), report)
    assert [t.name for t in session.tracks] == ["Ch 1", "Ch 1 (2)"]
    assert [t.id for t in session.tracks] == ["t0", "t1"]
    for track in session.tracks:
        assert [c.timeline_start_samples for c in track.clips] == [10 * RATE]
    nota = next(i for i in report.issues if i.code == "PRODUCTION_OVERLAP")
    assert nota.severity == "info"
    assert nota.context == {"track": "Ch 1 (2)", "takes": 1}
    assert nota.message.startswith("Ch 1 (2): 1 takes")


def test_um_wav_so_e_uma_entrada(tmp_path):
    arquivo = mixpre(tmp_path / "d" / "X.wav", HORA, "2025-01-02", "1", "1", [(5, "BOOM")])
    session = read_production(arquivo, Report(strict=False))
    assert [t.name for t in session.tracks] == [f"In 5 {SEP} BOOM"]
    assert session.tracks[0].clips[0].timeline_start_samples == 10 * RATE
