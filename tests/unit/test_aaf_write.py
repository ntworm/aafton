import wave

import pytest
from aafton.aaf_read import read_aaf
from aafton.aaf_write import write_aaf
from aafton.ir import ClipIR, FadeIR, MediaAsset, SessionIR, TrackIR
from aafton.report import Report
from aafton.timecode import TimecodeRate


def _mono_pan_session(tmp_path, *, pan=-0.25, source_offset=0):
    wav_path = tmp_path / "mono.wav"
    with wave.open(str(wav_path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(48000)
        handle.writeframes(b"\0" * 96000)

    media = MediaAsset(
        id="m1",
        filename=wav_path.name,
        channels=1,
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
        name="mono",
        media_id=media.id,
        timeline_start_samples=0,
        timeline_end_samples=48000,
        source_offset_samples=source_offset,
    )
    return SessionIR(
        name="pan mono",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=0,
        tracks=[
            TrackIR(
                id="t1",
                source_index=0,
                name="PAN MONO",
                kind="audio",
                channel_count=1,
                pan=pan,
                clips=[clip],
            )
        ],
        media=[media],
    )


def test_export_sem_audio_falha_fechado(tmp_path):
    from aafton.timecode import TimecodeRate

    session = SessionIR(
        name="vazia",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=0,
    )
    report = Report(strict=False)

    write_aaf(session, tmp_path / "vazia.aaf", report)

    codes = [issue.code for issue in report.issues]
    assert "EXPORT_NO_AUDIO_SOURCES" in codes
    assert "EXPORT_SELF_CHECK_OK" not in codes


def test_aaf_exporta_pan_estatico_mono(tmp_path):
    destination = tmp_path / "pan-mono.aaf"
    write_aaf(_mono_pan_session(tmp_path), destination, Report(strict=False))
    reread = read_aaf(destination, Report(strict=False))

    assert len(reread.tracks) == 1
    assert reread.tracks[0].pan == pytest.approx(-0.25)


def test_aaf_pan_mono_invalido_registra_erro_e_centraliza(tmp_path):
    report = Report(strict=False)
    destination = tmp_path / "pan-mono-invalido.aaf"

    write_aaf(
        _mono_pan_session(tmp_path, pan=1.5),
        destination,
        report,
    )
    reread = read_aaf(destination, Report(strict=False))

    invalid = [issue for issue in report.issues if issue.code == "PAN_VALUE_INVALID"]
    assert len(invalid) == 1
    assert invalid[0].severity == "error"
    assert reread.tracks[0].pan == 0.0


def test_self_check_alcanca_sourceclip_dentro_do_pan(tmp_path):
    report = Report(strict=False)

    write_aaf(
        _mono_pan_session(tmp_path, source_offset=24000),
        tmp_path / "pan-com-fonte-curta.aaf",
        report,
    )

    codes = [issue.code for issue in report.issues]
    assert "EXPORT_SOURCECLIP_EXCEEDS_SOURCE" in codes
    assert "EXPORT_SELF_CHECK_OK" not in codes


def test_aaf_estereo_usa_audio_channel_combiner_compativel_com_resolve(tmp_path):
    import aaf2
    from aaf2.auid import AUID

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
        timeline_start_samples=0,
        timeline_end_samples=48000,
        source_offset_samples=0,
    )
    session = SessionIR(
        name="stereo",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=0,
        tracks=[
            TrackIR(
                id="t1",
                source_index=0,
                name="STEREO",
                kind="audio",
                channel_count=2,
                clips=[clip],
            )
        ],
        media=[media],
    )
    destination = tmp_path / "stereo.aaf"
    report = Report(strict=False)

    write_aaf(session, destination, report)

    with aaf2.open(str(destination), "r") as f:
        composition = next(f.content.compositionmobs())
        audio_slots = [
            slot for slot in composition.slots if type(slot.segment).__name__ != "Timecode"
        ]
        assert len(audio_slots) == 1
        sequence = audio_slots[0].segment
        assert type(sequence).__name__ == "Sequence"
        assert int(sequence.length) == 48000
        assert len(sequence.components) == 1

        combiner = sequence.components[0]
        assert type(combiner).__name__ == "OperationGroup"
        assert combiner.operation.auid == AUID("6b46dd7a-132d-4856-ab21-8b751d8462ec")
        assert combiner.operation.name == "Audio Channel Combiner"
        # D12b: cada entrada usa seu master mono, como o AAF externo Pro Tools.
        assert [int(source.slot_id) for source in combiner.segments] == [1, 1]
        assert len({str(source.mob.mob_id) for source in combiner.segments}) == 2
        assert [
            (parameter.parameterdef.name, parameter.value) for parameter in combiner.parameters
        ] == [
            ("AvidParameterByteOrder", 18761),
            ("AvidEffectID", list(b"EFF2_AUDIO_CHANNEL_COMBINER\x00")),
        ]

    codes = [issue.code for issue in report.issues]
    assert "EXPORT_SELF_CHECK_OK" in codes
    assert "EXPORT_SOURCECLIP_EXCEEDS_SOURCE" not in codes

    reread = read_aaf(destination, Report(strict=False))
    assert len(reread.tracks) == 1
    assert reread.tracks[0].channel_count == 2
    assert [clip.channel_index for clip in reread.tracks[0].clips] == [0, 1]


def test_aaf_gravado_sobrevive_ao_roundtrip(tmp_path):
    import wave

    with wave.open(str(tmp_path / "m1.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(b"\0" * 100)

    start_tc = 48000 * 60 * 60

    clip1 = ClipIR(
        track_id="t1",
        channel_index=1,
        event_index=1,
        name="c1",
        raw={},
        gain_linear=1.0,
        muted=False,
        id="c1",
        media_id=str(tmp_path / "m1.wav"),
        timeline_start_samples=start_tc + 48000,
        timeline_end_samples=start_tc + 48000 + 24000,
        source_offset_samples=0,
    )
    clip2 = ClipIR(
        track_id="t1",
        channel_index=1,
        event_index=2,
        name="c2",
        raw={},
        gain_linear=1.0,
        muted=False,
        id="c2",
        media_id=str(tmp_path / "m1.wav"),
        timeline_start_samples=start_tc + 96000,
        timeline_end_samples=start_tc + 96000 + 48000,
        source_offset_samples=24000,
    )
    track = TrackIR(
        id="t1",
        source_index=1,
        kind="audio",
        name="DIALOGO",
        channel_count=1,
        plugins=[],
        clips=[clip1, clip2],
    )
    sessao_in = SessionIR(
        start_timecode_samples=start_tc,
        sample_rate=48000,
        tracks=[track],
        media=[],
        name="sessao_teste",
        timecode_rate=None,
        markers=[],
        source_path=None,
        source_application=None,
    )

    saida = tmp_path / "saida.aaf"
    write_aaf(sessao_in, saida)

    sessao_out = read_aaf(saida, Report(strict=False))

    assert sessao_out.start_timecode_samples == start_tc
    assert len(sessao_out.tracks) == 1
    assert sessao_out.tracks[0].name == "DIALOGO"

    clips_out = sessao_out.tracks[0].clips
    assert len(clips_out) == 2

    assert clips_out[0].timeline_start_samples == start_tc + 48000
    assert clips_out[0].timeline_end_samples == start_tc + 48000 + 24000
    assert clips_out[0].source_offset_samples == 0

    assert clips_out[1].timeline_start_samples == start_tc + 96000
    assert clips_out[1].timeline_end_samples == start_tc + 96000 + 48000
    assert clips_out[1].source_offset_samples == 24000


def test_aaf_escreve_com_taxa_de_timecode_do_session_ir(tmp_path):
    from aafton.timecode import TimecodeRate
    import aaf2

    session = SessionIR(
        name="Teste",
        sample_rate=48000,
        timecode_rate=TimecodeRate(24, 1, False),
        start_timecode_samples=0,
        tracks=[],
        media=[],
        markers=[],
        source_path=None,
        source_application=None,
    )
    saida = tmp_path / "saida24.aaf"
    write_aaf(session, saida)

    with aaf2.open(str(saida), "r") as f:
        comp = next(m for m in f.content.mobs if type(m).__name__ == "CompositionMob")
        tc_slot = next(s for s in comp.slots if s.name == "TC")
        tc = tc_slot.segment
        assert tc.fps == 24
        assert not tc.drop


def test_aaf_roundtrip_preserva_crossfade_com_sobreposicao(tmp_path):
    import wave

    from aafton.timecode import TimecodeRate

    wav_path = tmp_path / "crossfade.wav"
    with wave.open(str(wav_path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(48000)
        handle.writeframes(b"\0" * 24000)

    start_tc = 48000 * 60 * 60
    overlap = 4000
    first = ClipIR(
        id="c1",
        track_id="t1",
        channel_index=0,
        event_index=0,
        name="first",
        media_id=str(wav_path),
        timeline_start_samples=start_tc,
        timeline_end_samples=start_tc + 12000,
        source_offset_samples=0,
        fade_out=FadeIR(kind="out", duration_samples=overlap),
    )
    second = ClipIR(
        id="c2",
        track_id="t1",
        channel_index=0,
        event_index=1,
        name="second",
        media_id=str(wav_path),
        timeline_start_samples=start_tc + 12000 - overlap,
        timeline_end_samples=start_tc + 20000,
        source_offset_samples=12000,
        fade_in=FadeIR(kind="in", duration_samples=overlap),
    )
    session = SessionIR(
        name="crossfade",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1, False),
        start_timecode_samples=start_tc,
        tracks=[
            TrackIR(
                id="t1",
                source_index=0,
                name="DIALOGO",
                kind="audio",
                channel_count=1,
                clips=[first, second],
            )
        ],
    )
    report = Report(strict=False)

    destination = tmp_path / "crossfade.aaf"
    write_aaf(session, destination, report)
    reread = read_aaf(destination, Report(strict=False), fades="keep")

    assert not [issue for issue in report.issues if issue.code == "WRITE_OVERLAP"]
    assert [clip.timeline_start_samples for clip in reread.tracks[0].clips] == [
        first.timeline_start_samples,
        second.timeline_start_samples,
    ]
    assert reread.tracks[0].clips[0].fade_out.duration_samples == overlap
    assert reread.tracks[0].clips[1].fade_in.duration_samples == overlap


def test_strip_channel_suffix_helper():
    from aafton.aaf_write import _strip_channel_suffix

    assert _strip_channel_suffix("5.1 [C]") == "5.1"
    assert _strip_channel_suffix("5.1 [LFE]") == "5.1"
    assert _strip_channel_suffix("AMB 1ST [W]") == "AMB 1ST"
    assert _strip_channel_suffix("AMB 1ST [Z]") == "AMB 1ST"
    assert _strip_channel_suffix("STEREO [L]") == "STEREO"
    assert _strip_channel_suffix("STEREO [R]") == "STEREO"
    assert _strip_channel_suffix("Quad [Ls]") == "Quad"
    assert _strip_channel_suffix("Custom [ch1]") == "Custom"
    assert _strip_channel_suffix("Custom [ch12]") == "Custom"
    assert _strip_channel_suffix("MONO") == "MONO"
    assert _strip_channel_suffix("Track [Special]") == "Track [Special]"


def test_multichannel_track_export_strips_suffix_and_preserves_roundtrip(tmp_path):
    """5.1 [C] exportado e reimportado deve permanecer 5.1 [C], nao 5.1 [C] [L]."""
    import wave
    import aaf2
    from aafton.timecode import TimecodeRate

    wav_path = tmp_path / "tone.wav"
    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(b"\0" * 48000)

    start_tc = 48000 * 60 * 60
    suffixes = ["L", "R", "C", "LFE", "Ls", "Rs"]
    tracks = []
    for i, suf in enumerate(suffixes):
        clip = ClipIR(
            id=f"c{i}",
            track_id=f"t{i}",
            channel_index=0,
            event_index=0,
            name=f"tone_{suf}.wav",
            media_id=str(wav_path),
            timeline_start_samples=start_tc + i * 1000,
            timeline_end_samples=start_tc + (i + 1) * 1000,
            source_offset_samples=0,
        )
        tracks.append(
            TrackIR(
                id=f"t{i}",
                source_index=i,
                kind="audio",
                name=f"5.1 [{suf}]",
                channel_count=1,
                clips=[clip],
            )
        )

    session_in = SessionIR(
        name="SurroundSession",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1, False),
        start_timecode_samples=start_tc,
        tracks=tracks,
    )

    out_aaf = tmp_path / "surround.aaf"
    write_aaf(session_in, out_aaf)

    # Verifica os nomes dos slots no container AAF cru
    with aaf2.open(str(out_aaf), "r") as f:
        comp = next(m for m in f.content.mobs if type(m).__name__ == "CompositionMob")
        audio_slots = [
            s
            for s in comp.slots
            if getattr(s, "segment", None) and type(s.segment).__name__ != "Timecode"
        ]
        assert len(audio_slots) == 6
        for s in audio_slots:
            assert s.name == "5.1"

    # Reimporta o AAF e garante que os nomes nao acumulam sufixos
    session_out = read_aaf(out_aaf, Report(strict=False))
    assert len(session_out.tracks) == 6
    reimported_names = [t.name for t in session_out.tracks]
    assert reimported_names == [
        "5.1 [L]",
        "5.1 [R]",
        "5.1 [C]",
        "5.1 [LFE]",
        "5.1 [Ls]",
        "5.1 [Rs]",
    ]
