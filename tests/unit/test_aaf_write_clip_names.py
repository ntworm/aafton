"""Defeito D2: preservacao dos nomes dos clips da timeline na exportacao AAF.

Garante que clip.name definido no SessionIR seja transportado para o MasterMob
correspondente no AAF em vez de decair para o nome do arquivo no disco ou hash sha256.
"""

from pathlib import Path
import wave

import aaf2

from aafton.aaf_read import read_aaf
from aafton.aaf_write import write_aaf
from aafton.ir import ClipIR, MediaAsset, SessionIR, TrackIR
from aafton.report import Report
from aafton.timecode import TimecodeRate


def _criar_wav(caminho: Path, duracao_samples: int = 96000, canais: int = 1) -> Path:
    """Cria WAV silencioso com cabecalho valido."""
    with wave.open(str(caminho), "wb") as handle:
        handle.setnchannels(canais)
        handle.setsampwidth(2)
        handle.setframerate(48000)
        handle.writeframes(b"\0" * (duracao_samples * canais * 2))
    return caminho


def _mono_session(track_name: str, clips: list[ClipIR], media_path: Path) -> SessionIR:
    """Monta sessao mono basica para testes de clipes."""
    return SessionIR(
        name="sessao_mono_teste",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=0,
        tracks=[
            TrackIR(
                id="t1",
                source_index=0,
                name=track_name,
                kind="audio",
                channel_count=1,
                clips=clips,
            )
        ],
        media=[
            MediaAsset(
                id="m1",
                filename=media_path.name,
                channels=1,
                sample_rate=48000,
                status="resolved",
                resolved_path=str(media_path),
            )
        ],
    )


def test_mono_clip_name_preserved_in_aaf_export(tmp_path):
    """Clip mono com nome customizado preserva exatamente seu nome no AAF."""
    wav = _criar_wav(tmp_path / "audio.wav")
    clip = ClipIR(
        id="c1",
        track_id="t1",
        channel_index=0,
        event_index=0,
        name="Dialogo Principal",
        media_id="m1",
        timeline_start_samples=0,
        timeline_end_samples=48000,
        source_offset_samples=0,
    )
    session = _mono_session("VOZ", [clip], wav)

    out_aaf = tmp_path / "mono_nome.aaf"
    write_aaf(session, out_aaf, Report(strict=False))

    relida = read_aaf(out_aaf, Report(strict=False))
    assert len(relida.tracks) == 1
    assert len(relida.tracks[0].clips) == 1
    assert relida.tracks[0].clips[0].name == "Dialogo Principal"


def test_multi_clips_distinct_names_on_same_media_asset(tmp_path):
    """Multiplos clips no mesmo asset preservam nomes distintos na timeline."""
    wav = _criar_wav(tmp_path / "audio_compartilhado.wav", duracao_samples=192000)
    clips = [
        ClipIR(
            id="c1",
            track_id="t1",
            channel_index=0,
            event_index=0,
            name="Take 1",
            media_id="m1",
            timeline_start_samples=0,
            timeline_end_samples=48000,
            source_offset_samples=0,
        ),
        ClipIR(
            id="c2",
            track_id="t1",
            channel_index=0,
            event_index=1,
            name="Take 2",
            media_id="m1",
            timeline_start_samples=48000,
            timeline_end_samples=96000,
            source_offset_samples=48000,
        ),
    ]
    session = _mono_session("TRILHA", clips, wav)

    out_aaf = tmp_path / "multi_nomes.aaf"
    write_aaf(session, out_aaf, Report(strict=False))

    relida = read_aaf(out_aaf, Report(strict=False))
    lidos = relida.tracks[0].clips
    assert len(lidos) == 2
    assert lidos[0].name == "Take 1"
    assert lidos[1].name == "Take 2"


def test_clip_name_empty_falls_back_to_filename(tmp_path):
    """Clip sem nome herda o nome do arquivo da midia sem erro."""
    wav = _criar_wav(tmp_path / "ambiente.wav")
    clip = ClipIR(
        id="c1",
        track_id="t1",
        channel_index=0,
        event_index=0,
        name="",
        media_id="m1",
        timeline_start_samples=0,
        timeline_end_samples=48000,
        source_offset_samples=0,
    )
    session = _mono_session("FX", [clip], wav)

    out_aaf = tmp_path / "sem_nome.aaf"
    write_aaf(session, out_aaf, Report(strict=False))

    relida = read_aaf(out_aaf, Report(strict=False))
    assert len(relida.tracks[0].clips) == 1
    assert relida.tracks[0].clips[0].name == "ambiente"


def test_mixed_named_and_unnamed_clips_no_name_contamination(tmp_path):
    """Clipes nomeados e nao-nomeados no mesmo asset nao se contaminam mutuamente."""
    wav = _criar_wav(tmp_path / "locucao.wav", duracao_samples=240000)
    clips = [
        ClipIR(
            id="c1",
            track_id="t1",
            channel_index=0,
            event_index=0,
            name="Custom Clip",
            media_id="m1",
            timeline_start_samples=0,
            timeline_end_samples=48000,
            source_offset_samples=0,
        ),
        ClipIR(
            id="c2",
            track_id="t1",
            channel_index=0,
            event_index=1,
            name="",
            media_id="m1",
            timeline_start_samples=48000,
            timeline_end_samples=96000,
            source_offset_samples=48000,
        ),
        ClipIR(
            id="c3",
            track_id="t1",
            channel_index=0,
            event_index=2,
            name=None,
            media_id="m1",
            timeline_start_samples=96000,
            timeline_end_samples=144000,
            source_offset_samples=96000,
        ),
    ]
    session = _mono_session("VOZ", clips, wav)

    out_aaf = tmp_path / "misto_nomes.aaf"
    write_aaf(session, out_aaf, Report(strict=False))

    relida = read_aaf(out_aaf, Report(strict=False))
    lidos = relida.tracks[0].clips
    assert [c.name for c in lidos] == ["Custom Clip", "locucao", "locucao"]


def test_stereo_track_clip_name_preserved_with_channel_suffixes(tmp_path):
    """Clip estereo preserva o nome base com sufixos .L e .R nos canais."""
    wav = _criar_wav(tmp_path / "stereo.wav", duracao_samples=96000, canais=2)
    session = SessionIR(
        name="sessao_stereo",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=0,
        tracks=[
            TrackIR(
                id="t1",
                source_index=0,
                name="STEREO_TRACK",
                kind="audio",
                channel_count=2,
                clips=[
                    ClipIR(
                        id="c1_L",
                        track_id="t1",
                        channel_index=0,
                        event_index=0,
                        name="Musica Tema",
                        media_id="m1",
                        timeline_start_samples=0,
                        timeline_end_samples=48000,
                        source_offset_samples=0,
                    ),
                    ClipIR(
                        id="c1_R",
                        track_id="t1",
                        channel_index=1,
                        event_index=0,
                        name="Musica Tema",
                        media_id="m1",
                        timeline_start_samples=0,
                        timeline_end_samples=48000,
                        source_offset_samples=0,
                    ),
                ],
            )
        ],
        media=[
            MediaAsset(
                id="m1",
                filename="stereo.wav",
                channels=2,
                sample_rate=48000,
                status="resolved",
                resolved_path=str(wav),
            )
        ],
    )

    out_aaf = tmp_path / "stereo.aaf"
    write_aaf(session, out_aaf, Report(strict=False))

    relida = read_aaf(out_aaf, Report(strict=False))
    clips = relida.tracks[0].clips
    assert len(clips) == 2
    assert clips[0].name == "Musica Tema.L"
    assert clips[1].name == "Musica Tema.R"


def test_multichannel_interleaved_does_not_create_redundant_mobs(tmp_path):
    """Track 5.1 entrelaçada com eventos nomeados compartilha o mesmo MasterMob nos canais."""
    wav = _criar_wav(tmp_path / "surround.wav", duracao_samples=96000 * 6, canais=6)
    clips = []
    # Evento 0: 'Take 1' nos 6 canais
    for i in range(6):
        clips.append(
            ClipIR(
                id=f"c0_{i}",
                track_id="t1",
                channel_index=i,
                event_index=0,
                name="Take 1",
                media_id="m1",
                timeline_start_samples=0,
                timeline_end_samples=48000,
                source_offset_samples=0,
            )
        )
    # Evento 1: 'Take 2' nos 6 canais
    for i in range(6):
        clips.append(
            ClipIR(
                id=f"c1_{i}",
                track_id="t1",
                channel_index=i,
                event_index=1,
                name="Take 2",
                media_id="m1",
                timeline_start_samples=48000,
                timeline_end_samples=96000,
                source_offset_samples=48000,
            )
        )

    track = TrackIR(id="t1", source_index=0, name="5.1", kind="audio", channel_count=6, clips=clips)
    session = SessionIR(
        name="sess_surround",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=0,
        tracks=[track],
        media=[
            MediaAsset(
                id="m1",
                filename="surround.wav",
                channels=6,
                sample_rate=48000,
                status="resolved",
                resolved_path=str(wav),
            )
        ],
    )

    out_aaf = tmp_path / "surround_dedup.aaf"
    write_aaf(session, out_aaf, Report(strict=False))

    with aaf2.open(str(out_aaf), "r") as f:
        master_mobs = list(f.content.mastermobs())
        # Deve ter exatamente 2 MasterMobs (um para Take 1 e um para Take 2), e nao 7 ou 12!
        assert len(master_mobs) == 2
        names = {m.name for m in master_mobs}
        assert names == {"Take 1", "Take 2"}
