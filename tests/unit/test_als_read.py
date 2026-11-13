# tests/unit/test_als_read.py
import gzip
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from aafton.als_read import read_session
from aafton.als_write import WriteOptions, write_session
from aafton.als_xml import load_als
from aafton.ir import ClipIR, MarkerIR, MediaAsset, SessionIR, TrackIR
from aafton.report import Report
from aafton.timecode import TimecodeRate

TEMPLATE = Path("templates/live12.als")

pytestmark = pytest.mark.skipif(
    not TEMPLATE.exists(), reason="Portao B: templates/live12.als ainda nao foi autorado"
)

UMA_HORA = 172800000


def _sessao(tmp_path, live_origin=None):
    wav = tmp_path / "cena12_tk3.wav"
    wav.write_bytes(b"RIFF")
    asset = MediaAsset(
        id="m1",
        filename="cena12_tk3.wav",
        channels=1,
        sample_rate=48000,
        status="resolved",
        resolved_path=str(wav),
    )
    track = TrackIR(id="t0", source_index=0, name="DIALOGO 1", kind="audio", channel_count=1)
    track.clips.append(
        ClipIR(
            id="c0",
            track_id="t0",
            channel_index=0,
            event_index=0,
            name="cena12_tk3.wav",
            media_id="m1",
            timeline_start_samples=UMA_HORA + 240000,
            timeline_end_samples=UMA_HORA + 240000 + 96000,
            source_offset_samples=24000,
        )
    )
    return SessionIR(
        name="cena12",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=UMA_HORA,
        tracks=[track],
        media=[asset],
        markers=[MarkerIR(id="k1", name="corte seco", position_samples=UMA_HORA + 480000)],
    )


def _ida_e_volta(tmp_path, live_origin_samples):
    destino = tmp_path / "cena12.als"
    original = _sessao(tmp_path)
    write_session(
        original,
        TEMPLATE,
        destino,
        WriteOptions(sample_rate=48000, bpm=60, live_origin_samples=live_origin_samples),
        Report(strict=True),
    )
    return original, read_session(destino, Report(strict=True))


def test_ancorado_no_inicio_da_sessao_volta_no_mesmo_sample(tmp_path):
    original, lido = _ida_e_volta(tmp_path, UMA_HORA)
    assert (
        lido.tracks[0].clips[0].timeline_start_samples
        == original.tracks[0].clips[0].timeline_start_samples
    )


def test_ancorado_em_zero_volta_no_mesmo_sample(tmp_path):
    original, lido = _ida_e_volta(tmp_path, 0)
    assert (
        lido.tracks[0].clips[0].timeline_start_samples
        == original.tracks[0].clips[0].timeline_start_samples
    )


def test_ancorado_com_pre_roll_volta_no_mesmo_sample(tmp_path):
    original, lido = _ida_e_volta(tmp_path, UMA_HORA - 480000)
    assert (
        lido.tracks[0].clips[0].timeline_start_samples
        == original.tracks[0].clips[0].timeline_start_samples
    )


def test_duracao_e_offset_sobrevivem(tmp_path):
    original, lido = _ida_e_volta(tmp_path, UMA_HORA)
    clip_original = original.tracks[0].clips[0]
    clip_lido = lido.tracks[0].clips[0]
    assert clip_lido.duration_samples == clip_original.duration_samples
    assert clip_lido.source_offset_samples == clip_original.source_offset_samples


def test_nome_da_track_e_taxa_de_frames_sobrevivem(tmp_path):
    original, lido = _ida_e_volta(tmp_path, UMA_HORA)
    assert lido.tracks[0].name == "DIALOGO 1"
    assert lido.timecode_rate == TimecodeRate(25, 1)
    assert lido.sample_rate == 48000


def test_marker_volta_como_marker_e_o_locator_de_ancoragem_nao(tmp_path):
    original, lido = _ida_e_volta(tmp_path, UMA_HORA)
    nomes = [m.name for m in lido.markers]
    assert "corte seco" in nomes
    assert not any(nome.startswith("TC ") for nome in nomes)


def test_caminho_da_midia_volta_resolvido(tmp_path):
    original, lido = _ida_e_volta(tmp_path, UMA_HORA)
    asset = lido.media_by_id(lido.tracks[0].clips[0].media_id)
    assert asset.status == "resolved"
    assert asset.filename == "cena12_tk3.wav"


def test_relative_path_type_um_do_save_as_continua_audio(tmp_path):
    destino = tmp_path / "save-as.als"
    write_session(
        _sessao(tmp_path),
        TEMPLATE,
        destino,
        WriteOptions(
            sample_rate=48000,
            bpm=60,
            live_origin_samples=UMA_HORA,
        ),
        Report(strict=True),
    )
    root = load_als(destino)
    relative_type = next(root.iter("AudioClip")).find("SampleRef/FileRef/RelativePathType")
    assert relative_type is not None
    relative_type.set("Value", "1")
    with gzip.open(destino, "wb") as handle:
        ET.ElementTree(root).write(
            handle,
            encoding="utf-8",
            xml_declaration=True,
        )

    lido = read_session(destino, Report(strict=False))

    assert lido.tracks[0].is_video is False


def test_als_read_missing_media(tmp_path):
    from aafton.report import Report
    from aafton.als_read import read_session

    als = tmp_path / "missing.als"
    import gzip

    xml_content = b"""<?xml version="1.0" encoding="UTF-8"?>
<Ableton>
  <LiveSet>
    <Tracks>
      <AudioTrack>
        <Name><EffectiveName Value="Track 1" /></Name>
        <DeviceChain>
          <MainSequencer>
            <Sample>
              <ArrangerAutomation>
                <Events>
                  <AudioClip>
                    <Name Value="clip1" />
                    <CurrentStart Value="0" />
                    <CurrentEnd Value="1" />
                    <Loop>
                      <LoopStart Value="0" />
                    </Loop>
                    <SampleRef>
                      <FileRef>
                        <Path Value="C:\\does\\not\\exist.wav" />
                      </FileRef>
                    </SampleRef>
                  </AudioClip>
                </Events>
              </ArrangerAutomation>
            </Sample>
          </MainSequencer>
        </DeviceChain>
      </AudioTrack>
    </Tracks>
  </LiveSet>
</Ableton>
"""
    with gzip.open(als, "wb") as f:
        f.write(xml_content)
    import json

    sidecar = als.with_suffix(".aafton.json")
    sidecar.write_text(
        json.dumps(
            {
                "format_version": 1,
                "stereo": "split",
                "live_origin_samples": 0,
                "session_start_samples": 0,
                "sample_rate": 48000,
                "bpm": 120.0,
                "timecode_rate": {"num": 24, "den": 1, "drop_frame": False},
            }
        )
    )

    report = Report(strict=False)
    session = read_session(als, report)

    assert session.tracks[0].channel_count == 1
    asset = session.media_by_id(session.tracks[0].clips[0].media_id)
    assert asset.channels is None

    issues = [i.code for i in report.issues]
    assert "MEDIA_METADATA_UNREADABLE" in issues


def test_grouptrack_nao_vira_slot_de_aaf(tmp_path):
    # This xml simulates an ALS with GroupTrack and AudioTrack
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <Ableton>
      <LiveSet>
        <Tracks>
          <GroupTrack Id="10">
            <Name><EffectiveName Value="SFX" /></Name>
          </GroupTrack>
          <AudioTrack Id="11">
            <Name><EffectiveName Value="SFX 1" /></Name>
            <TrackGroupId Value="10" />
            <DeviceChain><MainSequencer><Sample><ArrangerAutomation><Events>
            </Events></ArrangerAutomation></Sample></MainSequencer></DeviceChain>
          </AudioTrack>
        </Tracks>
      </LiveSet>
    </Ableton>
    """
    als = tmp_path / "com_grupo.als"
    import gzip

    with gzip.open(als, "wt", encoding="utf-8") as f:
        f.write(xml)

    from aafton.sidecar import write_sidecar, Anchor
    from aafton.timecode import TimecodeRate

    write_sidecar(
        als,
        Anchor(
            live_origin_samples=0,
            session_start_samples=0,
            sample_rate=48000,
            timecode_rate=TimecodeRate(25, 1, False),
            bpm=60.0,
        ),
    )

    from aafton.als_read import read_session
    from aafton.report import Report

    session = read_session(als, Report(strict=False))

    # We should only get the 1 AudioTrack, no GroupTrack!
    assert len(session.tracks) == 1
    assert session.tracks[0].name == "SFX 1"
