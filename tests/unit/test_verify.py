# tests/unit/test_verify.py
from pathlib import Path

import pytest

from aafton.aaf_write import write_aaf
from aafton.als_write import WriteOptions, write_session
from aafton.ir import ClipIR, MediaAsset, SessionIR, TrackIR
from aafton.report import Report
from aafton.timecode import TimecodeRate
from aafton.verify import _index, verify_conversion
from tests.aaf_factory import wav_bytes

TEMPLATE = Path("templates/live12.als")

pytestmark = pytest.mark.skipif(
    not TEMPLATE.exists(), reason="Portao B: templates/live12.als ainda nao foi autorado"
)

UMA_HORA = 172800000


@pytest.fixture
def wav(tmp_path):
    caminho = tmp_path / "cena12_tk3.wav"
    caminho.write_bytes(wav_bytes(48000 * 120))
    return caminho


def _sessao(wav):
    asset = MediaAsset(
        id="m1",
        filename=str(wav),
        channels=1,
        sample_rate=48000,
        duration_samples=48000 * 120,
        status="resolved",
        resolved_path=str(wav),
        raw={},
    )
    track = TrackIR(
        id="t0",
        source_index=0,
        name="DIALOGO 1",
        kind="audio",
        channel_count=1,
        clips=[],
        plugins=[],
    )
    for indice in range(3):
        inicio = UMA_HORA + 240000 + indice * 192000
        track.clips.append(
            ClipIR(
                id=f"c{indice}",
                track_id="t0",
                channel_index=0,
                event_index=indice,
                name="cena12_tk3.wav",
                media_id="m1",
                timeline_start_samples=inicio,
                timeline_end_samples=inicio + 96000,
                source_offset_samples=24000,
                raw={},
            )
        )
    return SessionIR(
        name="cena12",
        sample_rate=48000,
        timecode_rate=TimecodeRate(24, 1, False),
        start_timecode_samples=UMA_HORA,
        tracks=[track],
        media=[asset],
        markers=[],
        source_path=None,
        source_application=None,
    )


def _par(tmp_path, wav, live_origin):
    aaf = tmp_path / "cena12.aaf"
    als = tmp_path / "cena12.als"
    sessao = _sessao(wav)
    write_aaf(sessao, aaf, Report(strict=False))
    write_session(
        sessao,
        TEMPLATE,
        als,
        WriteOptions(sample_rate=48000, bpm=60, live_origin_samples=live_origin),
        Report(strict=False),
    )
    return aaf, als


def test_conversao_correta_da_zero_de_desvio_em_todos_os_clips(tmp_path, wav):
    aaf, als = _par(tmp_path, wav, UMA_HORA)
    resultado = verify_conversion(aaf, als)
    assert resultado.ok is True
    assert len(resultado.rows) == 3
    assert all(linha.delta_samples == 0 for linha in resultado.rows)


def test_verificacao_passa_igual_com_ancoragem_em_zero(tmp_path, wav):
    aaf, als = _par(tmp_path, wav, 0)
    resultado = verify_conversion(aaf, als)
    assert resultado.ok is True


def test_a_tabela_traz_timecode_dos_dois_lados(tmp_path, wav):
    aaf, als = _par(tmp_path, wav, UMA_HORA)
    linha = verify_conversion(aaf, als).rows[0]
    # At 24 fps, 240000 samples @ 48000 is 5 seconds.
    assert linha.aaf_in == "01:00:05:00"
    assert linha.als_in == "01:00:05:00"


def test_clip_faltando_no_als_e_reportado_e_reprova(tmp_path, wav):
    aaf, als = _par(tmp_path, wav, UMA_HORA)
    sessao_curta = _sessao(wav)
    sessao_curta.tracks[0].clips = sessao_curta.tracks[0].clips[:2]
    write_session(
        sessao_curta,
        TEMPLATE,
        als,
        WriteOptions(sample_rate=48000, bpm=60, live_origin_samples=UMA_HORA),
        Report(strict=False),
    )
    resultado = verify_conversion(aaf, als)
    assert resultado.ok is False
    assert any(linha.status == "missing in the ALS" for linha in resultado.rows)


def test_desvio_acima_da_tolerancia_reprova(tmp_path, wav):
    aaf, als = _par(tmp_path, wav, UMA_HORA)
    sessao_torta = _sessao(wav)
    sessao_torta.tracks[0].clips[0].timeline_start_samples += 5
    sessao_torta.tracks[0].clips[0].timeline_end_samples += 5
    write_session(
        sessao_torta,
        TEMPLATE,
        als,
        WriteOptions(sample_rate=48000, bpm=60, live_origin_samples=UMA_HORA),
        Report(strict=False),
    )
    resultado = verify_conversion(aaf, als, override_tolerance_samples=0)
    assert resultado.ok is False
    assert max(abs(linha.delta_samples) for linha in resultado.rows) == 5


def test_tolerancia_explicita_permite_o_desvio(tmp_path, wav):
    aaf, als = _par(tmp_path, wav, UMA_HORA)
    sessao_torta = _sessao(wav)
    sessao_torta.tracks[0].clips[0].timeline_start_samples += 5
    sessao_torta.tracks[0].clips[0].timeline_end_samples += 5
    write_session(
        sessao_torta,
        TEMPLATE,
        als,
        WriteOptions(sample_rate=48000, bpm=60, live_origin_samples=UMA_HORA),
        Report(strict=False),
    )
    assert verify_conversion(aaf, als, override_tolerance_samples=8).ok is True


def test_verify_aceita_combiner_estereo_mapeado_para_dois_canais(tmp_path):
    import wave

    wav_path = tmp_path / "stereo.wav"
    with wave.open(str(wav_path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(48000)
        handle.writeframes(b"\0" * 192000)

    media = MediaAsset(
        id="stereo",
        filename=wav_path.name,
        channels=2,
        sample_rate=48000,
        duration_samples=48000,
        status="resolved",
        resolved_path=str(wav_path),
    )
    clip = ClipIR(
        id="c1",
        track_id="t1",
        channel_index=0,
        event_index=0,
        name="stereo",
        media_id=media.id,
        timeline_start_samples=UMA_HORA + 48000,
        timeline_end_samples=UMA_HORA + 96000,
        source_offset_samples=0,
    )
    session = SessionIR(
        name="stereo",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=UMA_HORA,
        tracks=[
            TrackIR(
                id="t1",
                source_index=0,
                original_source_indices=[0, 1],
                name="STEREO",
                kind="audio",
                channel_count=2,
                clips=[clip],
            )
        ],
        media=[media],
    )
    aaf_path = tmp_path / "stereo.aaf"
    als_path = tmp_path / "stereo.als"
    write_aaf(session, aaf_path, Report(strict=False))
    write_session(
        session,
        TEMPLATE,
        als_path,
        WriteOptions(
            sample_rate=48000,
            bpm=60,
            live_origin_samples=UMA_HORA,
            stereo="pair",
        ),
        Report(strict=False),
    )

    result = verify_conversion(aaf_path, als_path, override_tolerance_samples=0)

    assert result.ok is True
    assert len(result.rows) == 2
    assert all(row.delta_samples == 0 for row in result.rows)
    assert result.audio_expected == 2
    assert result.audio_compared == 2
    assert result.coverage_msg == (
        "Coverage: 2 of 2 audio events compared (0 missing, 0 extra); "
        "Mapping: 2 of 2 slots mapped, across 1 tracks of the .als"
    )


def test_a_tabela_em_texto_tem_cabecalho_e_uma_linha_por_clip(tmp_path, wav):
    aaf, als = _par(tmp_path, wav, UMA_HORA)
    texto = verify_conversion(aaf, als).to_table()
    linhas = [linha for linha in texto.splitlines() if linha.strip()]
    assert any("TC AAF" in linha for linha in linhas)
    assert len(linhas) >= 5


def test_index_with_identically_named_tracks_does_not_collide():
    """Dois tracks com mesmo nome nao devem sobrescrever clips no indice do verify."""
    c1 = ClipIR(
        id="c1",
        track_id="t0",
        channel_index=0,
        event_index=0,
        name="audio1.wav",
        media_id="m1",
        source_offset_samples=0,
        timeline_start_samples=1000,
        timeline_end_samples=2000,
    )
    c2 = ClipIR(
        id="c2",
        track_id="t0",
        channel_index=0,
        event_index=1,
        name="audio2.wav",
        media_id="m1",
        source_offset_samples=0,
        timeline_start_samples=3000,
        timeline_end_samples=4000,
    )
    c3 = ClipIR(
        id="c3",
        track_id="t1",
        channel_index=0,
        event_index=0,
        name="audio3.wav",
        media_id="m1",
        source_offset_samples=0,
        timeline_start_samples=1000,
        timeline_end_samples=2000,
    )
    c4 = ClipIR(
        id="c4",
        track_id="t1",
        channel_index=0,
        event_index=1,
        name="audio4.wav",
        media_id="m1",
        source_offset_samples=0,
        timeline_start_samples=3000,
        timeline_end_samples=4000,
    )
    t0 = TrackIR(id="t0", source_index=0, name="5.1", kind="audio", channel_count=1, clips=[c1, c2])
    t1 = TrackIR(id="t1", source_index=1, name="5.1", kind="audio", channel_count=1, clips=[c3, c4])

    session = SessionIR(
        name="test_collision",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1, False),
        start_timecode_samples=0,
        tracks=[t0, t1],
    )

    idx = _index(session)
    assert len(idx) == 4
    assert (0, 0) in idx and idx[(0, 0)].id == "c1"
    assert (0, 1) in idx and idx[(0, 1)].id == "c2"
    assert (1, 0) in idx and idx[(1, 0)].id == "c3"
    assert (1, 1) in idx and idx[(1, 1)].id == "c4"


def test_verify_reads_fades_setting_from_sidecar(tmp_path, wav):
    from aafton.aaf_read import read_aaf
    from aafton.media_extract import resolve_asset
    from tests.aaf_factory import build_aaf_with_rendered_fades

    aaf = build_aaf_with_rendered_fades(
        tmp_path / "fades.aaf",
        wav,
        [
            ("clip", 96000, 0),
            ("fade", 24000, "Fade 1"),
            ("clip", 96000, 48000),
        ],
        start_timecode_frames=0,
    )
    als = tmp_path / "fades.als"
    rep = Report(strict=False)
    session = read_aaf(aaf, rep, fades="absorb")
    for asset in session.media:
        resolve_asset(asset, [tmp_path], rep)
    write_session(
        session,
        TEMPLATE,
        als,
        WriteOptions(sample_rate=48000, bpm=60, live_origin_samples=0, fades="absorb"),
        rep,
    )
    resultado = verify_conversion(aaf, als)
    assert resultado.ok is True
    assert len(resultado.rows) == 2


def test_verify_video_row_format():
    from aafton.verify import VerifyResult, VerifyRow

    res_presumida = VerifyResult(
        regime_name="Sample-exact",
        rows=[
            VerifyRow(
                track="VIDEO",
                clip="Meu Video",
                aaf_in="01:00:00:00",
                aaf_out="01:00:10:00",
                als_in="01:00:00:00",
                als_out="01:00:10:00",
                delta_samples=0,
                delta_frames=0.0,
                status="presumed",
                anchor_source="VIDEO_ANCHOR_EXPLICIT",
            )
        ],
    )
    tabela_presumida = res_presumida.to_table()
    assert "---" in tabela_presumida
    assert "01:00:00:00-01:00:10:00" in tabela_presumida  # ALS
    assert "VIDEO_ANCHOR_EXPLICIT" in tabela_presumida
    assert "Note: The position of the video" in tabela_presumida

    res_ok = VerifyResult(
        regime_name="Sample-exact",
        rows=[
            VerifyRow(
                track="VIDEO",
                clip="Meu Video",
                aaf_in="01:00:00:00",
                aaf_out="01:00:10:00",
                als_in="01:00:00:00",
                als_out="01:00:10:00",
                delta_samples=0,
                delta_frames=0.0,
                status="ok",
                anchor_source="VIDEO_ANCHOR_FROM_AAF",
            )
        ],
    )
    tabela_ok = res_ok.to_table()
    assert "---" not in tabela_ok
    assert "01:00:00:00-01:00:10:00" in tabela_ok
    assert "Note:" not in tabela_ok


def test_verify_result_gera_relatorio_markdown_legivel():
    from aafton.verify import VerifyResult, VerifyRow

    resultado = VerifyResult(
        regime_name="Sample-exact",
        coverage_msg="Coverage: 1 of 1 audio events compared (0 missing, 0 extra)",
        tolerance_samples=0,
        audio_expected=1,
        audio_compared=1,
        rows=[
            VerifyRow(
                track="DIALOGO | 1",
                clip="fala",
                aaf_in="01:00:00:00",
                aaf_out="01:00:01:00",
                als_in="01:00:00:00",
                als_out="01:00:01:00",
                delta_samples=0,
                delta_frames=0.0,
                delta_end_samples=0,
                status="ok",
            )
        ],
    )

    markdown = resultado.to_markdown()

    assert "# Verification AAF -> ALS" in markdown
    assert "**Result**: passed" in markdown
    assert "**Largest deviation**: 0 samples" in markdown
    assert "DIALOGO \\| 1" in markdown
    assert (
        "| Track | Clip | TC AAF | TC ALS | Start deviation (samples) | End deviation (samples) | Status |"
        in markdown
    )


def test_cli_verify_grava_relatorio_markdown(tmp_path, wav):
    from aafton.cli import main

    aaf, als = _par(tmp_path, wav, UMA_HORA)
    report_path = tmp_path / "verify.md"

    exit_code = main(["verify", str(aaf), str(als), "--report", str(report_path)])

    assert exit_code == 0
    markdown = report_path.read_text(encoding="utf-8")
    assert "**Result**: passed" in markdown
    assert "**Largest deviation**: 0 samples" in markdown


def test_agrupamento_verify_ponta_a_ponta_sem_desvio(tmp_path):
    from aafton.aaf_write import write_aaf
    from aafton.als_read import read_session
    from aafton.sidecar import sidecar_path_for
    from aafton.report import Report
    from aafton.cli import main
    import wave
    import json

    aaf = tmp_path / "grupo.aaf"
    als = tmp_path / "grupo.als"

    wav1 = tmp_path / "audio1.wav"
    wav2 = tmp_path / "audio2.wav"
    wav3 = tmp_path / "audio3.wav"
    for w in [wav1, wav2, wav3]:
        with wave.open(str(w), "wb") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(48000)
            f.writeframes(b"\0" * 48000)

    c1 = ClipIR(
        id="c1",
        track_id="t0",
        channel_index=0,
        event_index=0,
        name="audio1",
        media_id="m1",
        source_offset_samples=0,
        timeline_start_samples=UMA_HORA,
        timeline_end_samples=UMA_HORA + 1000,
    )
    c2 = ClipIR(
        id="c2",
        track_id="t1",
        channel_index=0,
        event_index=0,
        name="audio2",
        media_id="m2",
        source_offset_samples=0,
        timeline_start_samples=UMA_HORA + 2000,
        timeline_end_samples=UMA_HORA + 4000,
    )
    c3 = ClipIR(
        id="c3",
        track_id="t2",
        channel_index=0,
        event_index=0,
        name="audio3",
        media_id="m3",
        source_offset_samples=0,
        timeline_start_samples=UMA_HORA + 5000,
        timeline_end_samples=UMA_HORA + 8000,
    )

    t0 = TrackIR(id="t0", source_index=0, name="SFX 1", kind="audio", channel_count=1, clips=[c1])
    t1 = TrackIR(
        id="t1", source_index=1, name="Corta relva", kind="audio", channel_count=1, clips=[c2]
    )
    t2 = TrackIR(id="t2", source_index=2, name="SFX 2", kind="audio", channel_count=1, clips=[c3])

    asset1 = MediaAsset(
        id="m1",
        filename="audio1.wav",
        channels=1,
        sample_rate=48000,
        duration_samples=48000,
        origin_timecode_samples=0,
        status="resolved",
        resolved_path=str(wav1),
    )
    asset2 = MediaAsset(
        id="m2",
        filename="audio2.wav",
        channels=1,
        sample_rate=48000,
        duration_samples=48000,
        origin_timecode_samples=0,
        status="resolved",
        resolved_path=str(wav2),
    )
    asset3 = MediaAsset(
        id="m3",
        filename="audio3.wav",
        channels=1,
        sample_rate=48000,
        duration_samples=48000,
        origin_timecode_samples=0,
        status="resolved",
        resolved_path=str(wav3),
    )

    sessao = SessionIR(
        name="test",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1, False),
        start_timecode_samples=UMA_HORA,
        tracks=[t0, t1, t2],
        media=[asset1, asset2, asset3],
    )
    write_aaf(sessao, aaf, Report(strict=False))

    main(["import", str(aaf), str(als), "--groups", "prefix"])

    sessao_als = read_session(als, Report(strict=False))
    nomes_agrupados = [t.name for t in sessao_als.tracks if t.kind == "audio"]
    assert nomes_agrupados == ["SFX 1", "SFX 2", "Corta relva"]

    with open(sidecar_path_for(als), "r") as f:
        sidecar_lido = json.load(f)
    assert sidecar_lido["audio_source_indices"] == [[0], [2], [1]]

    # Do round trip via verify
    resultado = verify_conversion(aaf, als)
    assert resultado.ok is True
    assert len(resultado.rows) == 3

    assert resultado.rows[0].track == "SFX 1"
    assert resultado.rows[0].clip == "audio1"
    assert resultado.rows[0].status == "ok"
    assert resultado.rows[0].delta_samples == 0

    assert resultado.rows[1].track == "Corta relva"
    assert resultado.rows[1].clip == "audio2"
    assert resultado.rows[1].status == "ok"
    assert resultado.rows[1].delta_samples == 0

    assert resultado.rows[2].track == "SFX 2"
    assert resultado.rows[2].clip == "audio3"
    assert resultado.rows[2].status == "ok"
    assert resultado.rows[2].delta_samples == 0


def test_verify_com_tracks_mesmo_nome_ponta_a_ponta(tmp_path):
    from aafton.aaf_write import write_aaf
    from aafton.report import Report
    from aafton.cli import main
    import wave

    aaf = tmp_path / "grupo.aaf"
    als = tmp_path / "grupo.als"

    wav1 = tmp_path / "audio1.wav"
    with wave.open(str(wav1), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(48000)
        f.writeframes(b"\0" * 48000)

    c1 = ClipIR(
        id="c1",
        track_id="t0",
        channel_index=0,
        event_index=0,
        name="audio1",
        media_id="m1",
        source_offset_samples=0,
        timeline_start_samples=UMA_HORA,
        timeline_end_samples=UMA_HORA + 1000,
    )
    c2 = ClipIR(
        id="c2",
        track_id="t1",
        channel_index=0,
        event_index=0,
        name="audio1",
        media_id="m1",
        source_offset_samples=0,
        timeline_start_samples=UMA_HORA + 2000,
        timeline_end_samples=UMA_HORA + 4000,
    )

    t0 = TrackIR(id="t0", source_index=0, name="SFX", kind="audio", channel_count=1, clips=[c1])
    t1 = TrackIR(id="t1", source_index=1, name="SFX", kind="audio", channel_count=1, clips=[c2])

    asset1 = MediaAsset(
        id="m1",
        filename="audio1.wav",
        channels=1,
        sample_rate=48000,
        duration_samples=48000,
        origin_timecode_samples=0,
        status="resolved",
        resolved_path=str(wav1),
    )

    sessao = SessionIR(
        name="test",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1, False),
        start_timecode_samples=UMA_HORA,
        tracks=[t0, t1],
        media=[asset1],
    )
    write_aaf(sessao, aaf, Report(strict=False))

    main(["import", str(aaf), str(als)])

    resultado = verify_conversion(aaf, als)
    assert resultado.ok is True
    assert len(resultado.rows) == 2
    assert resultado.rows[0].delta_samples == 0
    assert resultado.rows[1].delta_samples == 0


def test_map_swapped_fails(tmp_path):
    import json
    from aafton.sidecar import sidecar_path_for
    from aafton.aaf_write import write_aaf
    from aafton.cli import main
    import wave

    aaf = tmp_path / "grupo.aaf"
    als = tmp_path / "grupo.als"
    wav1 = tmp_path / "audio1.wav"
    with wave.open(str(wav1), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(48000)
        f.writeframes(b"\0" * 48000)

    c1 = ClipIR(
        id="c1",
        track_id="t0",
        channel_index=0,
        event_index=0,
        name="audio1",
        media_id="m1",
        source_offset_samples=0,
        timeline_start_samples=UMA_HORA,
        timeline_end_samples=UMA_HORA + 1000,
    )
    c2 = ClipIR(
        id="c2",
        track_id="t1",
        channel_index=0,
        event_index=0,
        name="audio2",
        media_id="m1",
        source_offset_samples=0,
        timeline_start_samples=UMA_HORA + 2000,
        timeline_end_samples=UMA_HORA + 4000,
    )
    t0 = TrackIR(id="t0", source_index=0, name="SFX 1", kind="audio", channel_count=1, clips=[c1])
    t1 = TrackIR(
        id="t1", source_index=1, name="Corta relva", kind="audio", channel_count=1, clips=[c2]
    )
    asset = MediaAsset(
        id="m1",
        filename="audio1.wav",
        channels=1,
        sample_rate=48000,
        duration_samples=48000,
        origin_timecode_samples=0,
        status="resolved",
        resolved_path=str(wav1),
    )

    sessao = SessionIR(
        name="test_swap",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1, False),
        start_timecode_samples=UMA_HORA,
        tracks=[t0, t1],
        media=[asset],
    )
    write_aaf(sessao, aaf, Report(strict=False))

    main(["import", str(aaf), str(als)])

    sidecar_path = sidecar_path_for(als)
    with open(sidecar_path, "r") as f:
        sidecar = json.load(f)
    sidecar["audio_source_indices"] = [1, 0]
    with open(sidecar_path, "w") as f:
        json.dump(sidecar, f)

    resultado = verify_conversion(aaf, als)
    assert resultado.ok is False
    # Eles estao trocados no ALS, logo as contas de tempo vao falhar


@pytest.mark.parametrize(
    "invalid_map",
    [
        [0],  # curto
        [0, 1, 2],  # longo
        [0, 0],  # duplicado
        ["0", "1"],  # string
        [0, True],  # bool (isinstance int = true in python, check should catch it)
        [1, 2],  # conjunto diferente
    ],
)
def test_map_invalid_fails(tmp_path, invalid_map):
    import json
    from aafton.sidecar import sidecar_path_for
    from aafton.cli import main
    from aafton.aaf_write import write_aaf
    import wave

    aaf = tmp_path / "grupo.aaf"
    als = tmp_path / "grupo.als"
    wav1 = tmp_path / "audio1.wav"
    with wave.open(str(wav1), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(48000)
        f.writeframes(b"\0" * 48000)

    t0 = TrackIR(id="t0", source_index=0, name="SFX 1", kind="audio", channel_count=1, clips=[])
    t1 = TrackIR(id="t1", source_index=1, name="SFX 2", kind="audio", channel_count=1, clips=[])
    sessao = SessionIR(
        name="test",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1, False),
        start_timecode_samples=0,
        tracks=[t0, t1],
        media=[],
    )
    write_aaf(sessao, aaf, Report(strict=False))

    main(["import", str(aaf), str(als)])
    sidecar_path = sidecar_path_for(als)

    with open(sidecar_path, "r") as f:
        sidecar = json.load(f)
    sidecar["audio_source_indices"] = invalid_map
    with open(sidecar_path, "w") as f:
        json.dump(sidecar, f)

    with pytest.raises(ValueError):
        verify_conversion(aaf, als)


def test_grouped_als_no_map_fails(tmp_path, capsys):
    import json
    from aafton.sidecar import sidecar_path_for
    from aafton.cli import main
    from aafton.aaf_write import write_aaf

    aaf = tmp_path / "grupo.aaf"
    als = tmp_path / "grupo.als"

    # Must have GroupTrack => At least two tracks with same prefix and one isolated, etc.
    t0 = TrackIR(id="t0", source_index=0, name="SFX 1", kind="audio", channel_count=1, clips=[])
    t1 = TrackIR(id="t1", source_index=1, name="SFX 2", kind="audio", channel_count=1, clips=[])
    t2 = TrackIR(id="t2", source_index=2, name="Solo", kind="audio", channel_count=1, clips=[])

    sessao = SessionIR(
        name="test",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1, False),
        start_timecode_samples=0,
        tracks=[t0, t1, t2],
        media=[],
    )
    write_aaf(sessao, aaf, Report(strict=False))

    main(["import", str(aaf), str(als), "--groups", "prefix"])

    # Verify XML actually has GroupTrack
    import xml.etree.ElementTree as ET
    import gzip

    root = ET.parse(gzip.open(als, "rb")).getroot()
    assert len(root.findall(".//LiveSet/Tracks/GroupTrack")) > 0

    sidecar_path = sidecar_path_for(als)
    with open(sidecar_path, "r") as f:
        sidecar = json.load(f)
    del sidecar["audio_source_indices"]
    with open(sidecar_path, "w") as f:
        json.dump(sidecar, f)

    with pytest.raises(ValueError, match="identity unavailable"):
        verify_conversion(aaf, als)

    # Also verify the CLI behavior
    from aafton.cli import main

    _ = capsys.readouterr()  # Clear buffer from import_
    args = ["verify", str(aaf), str(als)]
    codigo = main(args)
    captured = capsys.readouterr()
    assert codigo == 2
    assert captured.out == ""
    assert captured.err == (
        "error: audio_source_indices map missing in a grouped session; identity unavailable\n"
    )


def test_ungrouped_als_no_map_works(tmp_path):
    import json
    from aafton.sidecar import sidecar_path_for
    from aafton.cli import main
    from aafton.aaf_write import write_aaf
    import wave

    aaf = tmp_path / "grupo.aaf"
    als = tmp_path / "grupo.als"
    wav1 = tmp_path / "audio1.wav"
    with wave.open(str(wav1), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(48000)
        f.writeframes(b"\0" * 48000)

    c1 = ClipIR(
        id="c1",
        track_id="t0",
        channel_index=0,
        event_index=0,
        name="audio1",
        media_id="m1",
        source_offset_samples=0,
        timeline_start_samples=UMA_HORA,
        timeline_end_samples=UMA_HORA + 1000,
    )
    t0 = TrackIR(id="t0", source_index=0, name="Solo 1", kind="audio", channel_count=1, clips=[c1])
    asset = MediaAsset(
        id="m1",
        filename="audio1.wav",
        channels=1,
        sample_rate=48000,
        duration_samples=48000,
        origin_timecode_samples=0,
        status="resolved",
        resolved_path=str(wav1),
    )
    sessao = SessionIR(
        name="test",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1, False),
        start_timecode_samples=0,
        tracks=[t0],
        media=[asset],
    )
    write_aaf(sessao, aaf, Report(strict=False))

    main(["import", str(aaf), str(als), "--groups", "none"])

    # Verify XML has NO GroupTrack
    import xml.etree.ElementTree as ET
    import gzip

    root = ET.parse(gzip.open(als, "rb")).getroot()
    assert len(root.findall(".//LiveSet/Tracks/GroupTrack")) == 0

    sidecar_path = sidecar_path_for(als)
    with open(sidecar_path, "r") as f:
        sidecar = json.load(f)
    del sidecar["audio_source_indices"]
    with open(sidecar_path, "w") as f:
        json.dump(sidecar, f)

    resultado = verify_conversion(aaf, als)
    assert resultado.ok is True
    assert len(resultado.rows) == 1
    assert resultado.rows[0].delta_samples == 0


def test_index_com_mapa_curto_falha():
    from aafton.verify import _index
    from aafton.ir import SessionIR, TrackIR, ClipIR

    c1 = ClipIR(
        id="c1",
        track_id="t0",
        channel_index=0,
        event_index=0,
        name="clip1",
        media_id="m1",
        source_offset_samples=0,
        timeline_start_samples=0,
        timeline_end_samples=1000,
    )
    c2 = ClipIR(
        id="c2",
        track_id="t1",
        channel_index=0,
        event_index=0,
        name="clip2",
        media_id="m2",
        source_offset_samples=0,
        timeline_start_samples=0,
        timeline_end_samples=1000,
    )

    t0 = TrackIR(
        id="t0", source_index=10, name="Track 1", kind="audio", channel_count=1, clips=[c1]
    )
    t1 = TrackIR(
        id="t1", source_index=11, name="Track 2", kind="audio", channel_count=1, clips=[c2]
    )

    sessao = SessionIR(
        name="test",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1, False),
        start_timecode_samples=0,
        tracks=[t0, t1],
        media=[],
    )

    import pytest

    with pytest.raises(ValueError, match="Size of the audio_source_indices map does not match"):
        _index(sessao, source_indices=[99])


def test_verify_conversion_falha_com_origem_duplicada(tmp_path, monkeypatch):
    from aafton.cli import main
    from aafton.aaf_write import write_aaf
    import wave

    aaf = tmp_path / "grupo.aaf"
    als = tmp_path / "grupo.als"
    wav1 = tmp_path / "audio1.wav"
    with wave.open(str(wav1), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(48000)
        f.writeframes(b"\0" * 48000)

    t0 = TrackIR(id="t0", source_index=0, name="SFX 1", kind="audio", channel_count=1, clips=[])
    t1 = TrackIR(id="t1", source_index=1, name="SFX 2", kind="audio", channel_count=1, clips=[])
    sessao = SessionIR(
        name="test",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1, False),
        start_timecode_samples=0,
        tracks=[t0, t1],
        media=[],
    )
    write_aaf(sessao, aaf, Report(strict=False))

    main(["import", str(aaf), str(als)])

    import aafton.verify

    original_read_aaf = aafton.verify.read_aaf

    def fake_read_aaf(path, *args, **kwargs):
        sessao_real = original_read_aaf(path, *args, **kwargs)
        # Forca duplicata de source_index na origem
        sessao_real.tracks[1].source_index = sessao_real.tracks[0].source_index
        return sessao_real

    monkeypatch.setattr(aafton.verify, "read_aaf", fake_read_aaf)

    with pytest.raises(ValueError, match="There are duplicate source_index values in the source"):
        verify_conversion(aaf, als)


# ---------------------------------------------------------------------------
# Controles estruturais adicionados no prompt 50
# ---------------------------------------------------------------------------


def test_canal_irmao_divergente_reprova_verify(tmp_path):
    """Quando o canal irmao do AAF difere do clip no .als, o verify reprova.

    Cenario:
    - AAF "bom": VOZ [L] e VOZ [R] com clips identicos em sample 1000-2000.
    - Importacao com --stereo pair: sidecar registra [[0, 1]] para a track fundida.
    - AAF "ruim": mesma estrutura, mas VOZ [R] tem o clip deslocado para 1100-2100.
    - verify_conversion(aaf_ruim, als) deve reprovar na linha do slot 1 (canal R).

    A asserção especifica qual linha reprovou e com que desvio, para garantir
    que a reprovação acontece pelo motivo certo (divergência no irmão) e não
    por qualquer outro erro.
    """
    import wave
    from aafton.cli import main
    from aafton.aaf_write import write_aaf

    # Criar WAV real para que o import nao rejeite a ausência de arquivo
    def make_wav(path):
        with wave.open(str(path), "wb") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(48000)
            f.writeframes(b"\x00\x00" * 96000)

    wav_l = tmp_path / "voz_L.wav"
    wav_r = tmp_path / "voz_R.wav"
    make_wav(wav_l)
    make_wav(wav_r)

    # Clip compartilhado: posicao identica nos dois canais
    INICIO = UMA_HORA + 48000  # 1 segundo depois da ancora

    def make_clip(id_, track_id, wav_path, start, end, media_id):
        return ClipIR(
            id=id_,
            track_id=track_id,
            channel_index=0,
            event_index=0,
            name=wav_path.stem,
            media_id=media_id,
            source_offset_samples=0,
            timeline_start_samples=start,
            timeline_end_samples=end,
        )

    asset_l = MediaAsset(
        id="ml",
        filename=str(wav_l),
        channels=1,
        sample_rate=48000,
        duration_samples=96000,
        origin_timecode_samples=0,
        status="resolved",
        resolved_path=str(wav_l),
    )
    asset_r = MediaAsset(
        id="mr",
        filename=str(wav_r),
        channels=1,
        sample_rate=48000,
        duration_samples=96000,
        origin_timecode_samples=0,
        status="resolved",
        resolved_path=str(wav_r),
    )

    # AAF bom: L e R identicos para que o par seja aceito no import
    clip_l_bom = make_clip("cl1", "tl", wav_l, INICIO, INICIO + 48000, "ml")
    clip_r_bom = make_clip("cr1", "tr", wav_r, INICIO, INICIO + 48000, "mr")

    t_l_bom = TrackIR(
        id="tl", source_index=0, name="VOZ [L]", kind="audio", channel_count=1, clips=[clip_l_bom]
    )
    t_r_bom = TrackIR(
        id="tr", source_index=1, name="VOZ [R]", kind="audio", channel_count=1, clips=[clip_r_bom]
    )

    sessao_boa = SessionIR(
        name="voz",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1, False),
        start_timecode_samples=UMA_HORA,
        tracks=[t_l_bom, t_r_bom],
        media=[asset_l, asset_r],
    )

    aaf_bom = tmp_path / "voz_bom.aaf"
    als = tmp_path / "voz.als"
    write_aaf(sessao_boa, aaf_bom, Report(strict=False))

    # Importar com pair: sidecar deve registrar [[0, 1]] para a track fundida
    main(["import", str(aaf_bom), str(als), "--stereo", "pair", "--groups", "none"])

    # Confirmar que o sidecar registra os dois índices
    import json
    from aafton.sidecar import sidecar_path_for

    with open(sidecar_path_for(als)) as f:
        sidecar = json.load(f)
    assert sidecar["audio_source_indices"] == [[0, 1]], (
        f"Esperado [[0, 1]], obtido {sidecar['audio_source_indices']}"
    )

    # Verificar contra o AAF bom: deve passar
    res_bom = verify_conversion(aaf_bom, als, override_tolerance_samples=0)
    assert res_bom.ok is True, f"Verificação do AAF bom falhou: {res_bom.rows}"

    # AAF ruim: VOZ [R] com clip deslocado 100 samples
    DESVIO = 100
    clip_l_ruim = make_clip("cl1", "tl", wav_l, INICIO, INICIO + 48000, "ml")
    clip_r_ruim = make_clip("cr1", "tr", wav_r, INICIO + DESVIO, INICIO + 48000 + DESVIO, "mr")

    t_l_ruim = TrackIR(
        id="tl", source_index=0, name="VOZ [L]", kind="audio", channel_count=1, clips=[clip_l_ruim]
    )
    t_r_ruim = TrackIR(
        id="tr", source_index=1, name="VOZ [R]", kind="audio", channel_count=1, clips=[clip_r_ruim]
    )

    sessao_ruim = SessionIR(
        name="voz",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1, False),
        start_timecode_samples=UMA_HORA,
        tracks=[t_l_ruim, t_r_ruim],
        media=[asset_l, asset_r],
    )

    aaf_ruim = tmp_path / "voz_ruim.aaf"
    write_aaf(sessao_ruim, aaf_ruim, Report(strict=False))

    # Verificar contra o AAF ruim: deve reprovar
    res_ruim = verify_conversion(aaf_ruim, als, override_tolerance_samples=0)

    assert res_ruim.ok is False, "verify deveria reprovar quando o canal irmao diverge"

    # Identificar a linha que reprovou (deve ser slot 1, o canal R)
    linhas_reprovadas = [r for r in res_ruim.rows if r.status not in ("ok", "presumed")]
    assert len(linhas_reprovadas) >= 1, "Nenhuma linha foi marcada como reprovada"

    # A reprovação deve ser pelo desvio exato de DESVIO samples no slot 1 (VOZ [R])
    linha_slot1 = next((r for r in res_ruim.rows if abs(r.delta_samples) == DESVIO), None)
    assert linha_slot1 is not None, (
        f"Esperada linha com delta_samples={DESVIO} (desvio do canal R), "
        f"rows obtidas: {res_ruim.rows}"
    )
    assert linha_slot1.status not in ("ok", "presumed"), (
        f"A linha do canal R deveria reprovar mas status={linha_slot1.status!r}"
    )


def test_verify_nao_muta_objeto_read_aaf(tmp_path, monkeypatch):
    """verify_conversion não pode modificar o objeto SessionIR lido do AAF.

    O defeito que este teste sela: uma linha 'origem.tracks = [...]' dentro de
    verify_conversion filtrava os canais irmãos do AAF para caber no .als,
    reduzindo o número de tracks do objeto de origem. Isso invertia a direção
    da prova e permitia que divergências nos irmãos passassem despercebidas.

    Estratégia: monkeypatch em verify.read_aaf para capturar o objeto exato
    que verify_conversion recebe. Depois da chamada, o objeto capturado não
    pode ter menos tracks que o AAF original.
    """
    import wave
    from aafton.cli import main
    from aafton.aaf_write import write_aaf
    import aafton.verify as verify_mod

    def make_wav(path):
        with wave.open(str(path), "wb") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(48000)
            f.writeframes(b"\x00\x00" * 96000)

    wav_l = tmp_path / "voz_L.wav"
    wav_r = tmp_path / "voz_R.wav"
    make_wav(wav_l)
    make_wav(wav_r)

    INICIO = UMA_HORA + 48000

    asset_l = MediaAsset(
        id="ml",
        filename=str(wav_l),
        channels=1,
        sample_rate=48000,
        duration_samples=96000,
        origin_timecode_samples=0,
        status="resolved",
        resolved_path=str(wav_l),
    )
    asset_r = MediaAsset(
        id="mr",
        filename=str(wav_r),
        channels=1,
        sample_rate=48000,
        duration_samples=96000,
        origin_timecode_samples=0,
        status="resolved",
        resolved_path=str(wav_r),
    )

    clip_l = ClipIR(
        id="cl1",
        track_id="tl",
        channel_index=0,
        event_index=0,
        name="voz_L",
        media_id="ml",
        source_offset_samples=0,
        timeline_start_samples=INICIO,
        timeline_end_samples=INICIO + 48000,
    )
    clip_r = ClipIR(
        id="cr1",
        track_id="tr",
        channel_index=0,
        event_index=0,
        name="voz_R",
        media_id="mr",
        source_offset_samples=0,
        timeline_start_samples=INICIO,
        timeline_end_samples=INICIO + 48000,
    )

    t_l = TrackIR(
        id="tl", source_index=0, name="VOZ [L]", kind="audio", channel_count=1, clips=[clip_l]
    )
    t_r = TrackIR(
        id="tr", source_index=1, name="VOZ [R]", kind="audio", channel_count=1, clips=[clip_r]
    )

    sessao = SessionIR(
        name="voz",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1, False),
        start_timecode_samples=UMA_HORA,
        tracks=[t_l, t_r],
        media=[asset_l, asset_r],
    )

    aaf = tmp_path / "voz.aaf"
    als = tmp_path / "voz.als"
    write_aaf(sessao, aaf, Report(strict=False))
    main(["import", str(aaf), str(als), "--stereo", "pair", "--groups", "none"])

    # O AAF tem 2 tracks de audio. Capturar o objeto que verify_conversion usa.
    objetos_capturados = []
    original_read_aaf = verify_mod.read_aaf

    def read_aaf_capturador(path, *args, **kwargs):
        obj = original_read_aaf(path, *args, **kwargs)
        objetos_capturados.append(obj)
        return obj

    monkeypatch.setattr(verify_mod, "read_aaf", read_aaf_capturador)

    verify_conversion(aaf, als)

    assert len(objetos_capturados) == 1, "Esperava exatamente uma chamada a read_aaf"
    sessao_capturada = objetos_capturados[0]

    # Contar apenas tracks de audio (excluir video, se houver)
    tracks_audio = [t for t in sessao_capturada.tracks if not getattr(t, "is_video", False)]
    assert len(tracks_audio) == 2, (
        f"A origem foi mutada: esperava 2 tracks de audio, encontrou {len(tracks_audio)}. "
        "verify_conversion não pode filtrar origem.tracks."
    )


# Corte no fim da midia: o AAF pede mais que o WAV do set tem, o set termina no fim dele.
MIDIA_CURTA = 48000 * 10
INICIO_CORTE = UMA_HORA + 240000


def _sessao_corte(wav, fim_relativo, *, offset=0, duracao=MIDIA_CURTA):
    asset = MediaAsset(
        id="m1",
        filename=str(wav),
        channels=1,
        sample_rate=48000,
        duration_samples=duracao,
        status="resolved",
        resolved_path=str(wav),
    )
    clip = ClipIR(
        id="c0",
        track_id="t0",
        channel_index=0,
        event_index=0,
        name="voz.wav",
        media_id="m1",
        timeline_start_samples=INICIO_CORTE,
        timeline_end_samples=INICIO_CORTE + fim_relativo,
        source_offset_samples=offset,
    )
    track = TrackIR(
        id="t0", source_index=0, name="Audio 7", kind="audio", channel_count=1, clips=[clip]
    )
    return SessionIR(
        name="corte",
        sample_rate=48000,
        timecode_rate=TimecodeRate(24, 1, False),
        start_timecode_samples=UMA_HORA,
        tracks=[track],
        media=[asset],
    )


def _verify_corte(tmp_path, excesso_aaf, fim_set, offset_set=0):
    wav = tmp_path / "voz.wav"
    wav.write_bytes(wav_bytes(MIDIA_CURTA))
    aaf = tmp_path / "corte.aaf"
    als = tmp_path / "corte.als"
    # O AAF declara uma midia maior, para o export nao cortar nem reprovar o clip.
    origem = _sessao_corte(wav, MIDIA_CURTA + excesso_aaf, duracao=MIDIA_CURTA + 48000)
    write_aaf(origem, aaf, Report(strict=False))
    write_session(
        _sessao_corte(wav, fim_set, offset=offset_set),
        TEMPLATE,
        als,
        WriteOptions(sample_rate=48000, bpm=60, live_origin_samples=UMA_HORA),
        Report(strict=False),
    )
    return verify_conversion(aaf, als)


def test_verify_aceita_clip_cortado_no_fim_da_midia(tmp_path):
    resultado = _verify_corte(tmp_path, 216, MIDIA_CURTA)
    assert [linha.status for linha in resultado.rows] == ["trimmed to media end"]
    assert resultado.rows[0].delta_end_samples == -216
    assert resultado.ok is True
    assert "trimmed to media end" in resultado.to_table()


def test_verify_reprova_corte_com_offset_diferente(tmp_path):
    resultado = _verify_corte(tmp_path, 216, MIDIA_CURTA - 10, offset_set=10)
    assert [linha.status for linha in resultado.rows] == ["out of tolerance"]
    assert resultado.ok is False


def test_verify_reprova_clip_encurtado_longe_do_fim_da_midia(tmp_path):
    resultado = _verify_corte(tmp_path, 216, MIDIA_CURTA - 1000)
    assert [linha.status for linha in resultado.rows] == ["out of tolerance"]
    assert resultado.ok is False


def test_verify_reprova_excesso_de_um_frame(tmp_path):
    resultado = _verify_corte(tmp_path, 2000, MIDIA_CURTA)
    assert [linha.status for linha in resultado.rows] == ["out of tolerance"]
    assert resultado.ok is False
