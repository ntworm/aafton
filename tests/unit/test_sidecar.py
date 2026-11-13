import json

import pytest
from aafton.sidecar import (
    Anchor,
    SidecarMissingError,
    parse_anchor_locator_name,
    read_anchor,
    sidecar_path_for,
    write_sidecar,
)
from aafton.timecode import TimecodeRate

UMA_HORA = 172800000


def _anchor():
    return Anchor(
        live_origin_samples=UMA_HORA,
        session_start_samples=UMA_HORA,
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1),
        bpm=60.0,
        source_path="cena12.aaf",
    )


def test_caminho_do_sidecar_e_derivado_do_als(tmp_path):
    assert sidecar_path_for(tmp_path / "cena12.als").name == "cena12.aafton.json"


def test_ida_e_volta_pelo_json(tmp_path):
    destino = tmp_path / "cena12.als"
    write_sidecar(destino, _anchor())
    lido = read_anchor(destino)
    assert lido.live_origin_samples == UMA_HORA
    assert lido.timecode_rate == TimecodeRate(25, 1)
    assert lido.bpm == 60.0


def test_json_e_legivel_por_humano(tmp_path):
    destino = tmp_path / "cena12.als"
    write_sidecar(destino, _anchor())
    dados = json.loads(sidecar_path_for(destino).read_text(encoding="utf-8"))
    assert dados["live_origin_timecode"] == "01:00:00:00"
    assert dados["timecode_rate"] == {"num": 25, "den": 1, "drop_frame": False}


def test_nome_do_locator_de_ancoragem_e_parseavel():
    anchor = parse_anchor_locator_name("TC 01:00:00:00 @ 48000", TimecodeRate(25, 1))
    assert anchor == UMA_HORA


def test_nome_de_locator_sem_timecode_devolve_none():
    assert parse_anchor_locator_name("corte seco", TimecodeRate(25, 1)) is None


def test_sem_sidecar_e_sem_locator_falha_em_vez_de_assumir_zero(tmp_path):
    destino = tmp_path / "cena12.als"
    with pytest.raises(SidecarMissingError, match="anchor"):
        read_anchor(destino)


def test_drop_frame_sobrevive_ao_json(tmp_path):
    destino = tmp_path / "cena12.als"
    anchor = _anchor()
    anchor.timecode_rate = TimecodeRate(30000, 1001, drop_frame=True)
    write_sidecar(destino, anchor)
    assert read_anchor(destino).timecode_rate.drop_frame is True


def test_fades_mode_sobrevive_ao_json(tmp_path):
    destino = tmp_path / "cena12.als"
    anchor = _anchor()
    anchor.fades = "keep"
    write_sidecar(destino, anchor)
    assert read_anchor(destino).fades == "keep"


def test_write_session_grava_intervalos_de_clip_para_o_auditor(tmp_path):
    from pathlib import Path

    from aafton.als_write import WriteOptions, write_session
    from aafton.ir import ClipIR, MediaAsset, SessionIR, TrackIR
    from aafton.report import Report

    asset = MediaAsset(
        id="m",
        filename="mono.wav",
        channels=1,
        sample_rate=48000,
        duration_samples=3 * 48000,
        status="resolved",
        resolved_path=str(tmp_path / "mono.wav"),
    )
    clips = [
        ClipIR(
            id=f"c{i}",
            track_id="t",
            channel_index=0,
            event_index=i,
            name="mono",
            media_id="m",
            timeline_start_samples=UMA_HORA + a,
            timeline_end_samples=UMA_HORA + b,
            source_offset_samples=0,
        )
        for i, (a, b) in enumerate([(96013, 120013), (48007, 60007)])
    ]
    session = SessionIR(
        name="s",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=UMA_HORA,
        media=[asset],
        tracks=[
            TrackIR(
                id="t",
                source_index=0,
                name="CAL_TWO_CLIPS",
                kind="audio",
                channel_count=1,
                clips=clips,
            )
        ],
    )
    destino = tmp_path / "s.als"
    write_session(
        session,
        Path("templates/live12.als"),
        destino,
        WriteOptions(sample_rate=48000, bpm=60.0, live_origin_samples=UMA_HORA),
        Report(strict=False),
    )
    dados = json.loads(sidecar_path_for(destino).read_text(encoding="utf-8"))
    assert dados["clip_ranges"] == [
        {
            "track": "CAL_TWO_CLIPS",
            "ranges": [[UMA_HORA + 48007, UMA_HORA + 60007], [UMA_HORA + 96013, UMA_HORA + 120013]],
        }
    ]
    assert read_anchor(destino).clip_ranges == dados["clip_ranges"]


def test_locator_com_fps_no_fim_le_a_mesma_ancoragem():
    """O fps vem depois da taxa de amostragem: o leitor antigo continua valendo."""
    rate = TimecodeRate(25, 1)
    assert parse_anchor_locator_name("TC 01:00:00:00 @ 48000 @ 25", rate) == (
        parse_anchor_locator_name("TC 01:00:00:00 @ 48000", rate)
    )
