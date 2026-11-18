"""Testes das lacunas da auditoria de 18/11/2026 (T01 a T07).

Cada teste cobre um caminho de codigo que a auditoria achou sem cobertura.
Quando o codigo do produto ainda faz o contrario do comportamento correto,
o teste fica com xfail estrito: a suite continua verde e o defeito
continua visivel.
"""

from __future__ import annotations

import gzip
import shutil
from pathlib import Path

import pytest

from aafton.aaf_read import read_aaf
from aafton.als_read import read_session
from aafton.ir import ClipIR, MediaAsset, SessionIR, TrackIR
from aafton.media_extract import write_wav_with_bext
from aafton.report import Report, StrictModeError
from aafton.timecode import TimecodeRate, frames_to_label
from tests.aaf_factory import wav_bytes

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "templates" / "live12.als"
FIXTURE_EDL = REPO / "tests" / "fixtures" / "formatos" / "cena12.edl"

TAXA = 48000
UMA_HORA = 172800000
# Timecode de origem do dial01.wav da cena CENA12: 10:00:00:00 a 25 fps.
TC_ARQUIVO = 1728000000
# A EDL da cena usa 10:00:01:00 como origem do primeiro evento do dial01.
TC_EVENTO = TC_ARQUIVO + 48000


def _wav_com_bext(
    destino: Path, *, segundos: float, time_reference: int, sample_rate: int = TAXA
) -> Path:
    """WAV mono de 24 bits com o chunk bext carregando o TC de origem em samples."""
    amostras = int(segundos * sample_rate)
    write_wav_with_bext(
        destino,
        b"\x00\x00\x00" * amostras,
        channels=1,
        sample_rate=sample_rate,
        bits_per_sample=24,
        time_reference=time_reference,
    )
    return destino


def _codigos(report: Report) -> list[str]:
    """Codigos de todos os problemas registrados no relatorio."""
    return [issue.code for issue in report.issues]


def _problemas(report: Report, codigo: str) -> list:
    """Problemas de um codigo, na ordem em que foram registrados."""
    return [issue for issue in report.issues if issue.code == codigo]


# ---------------------------------------------------------------------------
# T01 - a tolerancia automatica do verify com taxas de slot misturadas
# ---------------------------------------------------------------------------


def _aaf_de_taxas_misturas(path: Path, wav_path: Path, *, fps: int = 24) -> Path:
    """AAF com dois slots de som: um na taxa do audio e outro em frames."""
    import aaf2

    path = Path(path)
    with aaf2.open(str(path), "w") as f:
        composition = f.create.CompositionMob("MISTURADO")
        f.content.mobs.append(composition)
        meta = {
            "format": {"filename": str(Path(wav_path).resolve()), "format_name": "wav"},
            "streams": [
                {
                    "codec_type": "audio",
                    "codec_name": "pcm_s16le",
                    "sample_rate": TAXA,
                    "duration_ts": TAXA * 60,
                    "channels": 1,
                }
            ],
        }
        master_mob, _source_mob, _tape_mob = f.content.link_external_wav(meta)

        slot = composition.create_timeline_slot(edit_rate=TAXA)
        slot.name = "AUDIO 48K"
        seq = f.create.Sequence(media_kind="sound")
        slot.segment = seq
        clip = f.create.SourceClip(media_kind="sound", start=0)
        clip.length = TAXA
        clip.mob = master_mob
        clip.slot_id = 1
        seq.components.append(clip)

        slot_frames = composition.create_timeline_slot(edit_rate=fps)
        slot_frames.name = "AUDIO 24FPS"
        seq_frames = f.create.Sequence(media_kind="sound")
        slot_frames.segment = seq_frames
        clip_frames = f.create.SourceClip(media_kind="sound", start=0)
        clip_frames.length = fps
        clip_frames.mob = master_mob
        clip_frames.slot_id = 1
        seq_frames.components.append(clip_frames)

        tc_slot = composition.create_timeline_slot(edit_rate=25)
        tc_slot.name = "TC"
        tc = f.create.Timecode(fps=25, drop=False)
        tc.start = 90000
        tc.length = 50
        tc_slot.segment = tc
    return path


def _sessao_de_taxas_misturas(wav: Path, desvio: int) -> SessionIR:
    """Sessao com as duas faixas; a segunda desvia `desvio` samples do AAF."""
    asset = MediaAsset(
        id="m1",
        filename=str(wav),
        channels=1,
        sample_rate=TAXA,
        duration_samples=TAXA * 120,
        status="resolved",
        resolved_path=str(wav),
    )
    faixas = []
    for indice, (nome, inicio) in enumerate(
        (("AUDIO 48K", UMA_HORA), ("AUDIO 24FPS", UMA_HORA + desvio))
    ):
        clip = ClipIR(
            id=f"t{indice}c0",
            track_id=f"t{indice}",
            channel_index=0,
            event_index=0,
            name=wav.name,
            media_id=asset.id,
            timeline_start_samples=inicio,
            timeline_end_samples=inicio + TAXA,
            source_offset_samples=0,
        )
        faixas.append(
            TrackIR(
                id=f"t{indice}",
                source_index=indice,
                name=nome,
                kind="audio",
                channel_count=1,
                clips=[clip],
            )
        )
    return SessionIR(
        name="misturado",
        sample_rate=TAXA,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=UMA_HORA,
        tracks=faixas,
        media=[asset],
    )


def _par_misturado(tmp_path: Path, desvio: int = 2000) -> tuple[Path, Path]:
    """AAF de taxas misturadas e o .als que repete a faixa por `desvio` samples."""
    from aafton.als_write import WriteOptions, write_session

    wav = tmp_path / "voz.wav"
    wav.write_bytes(wav_bytes(TAXA * 120))
    aaf = _aaf_de_taxas_misturas(tmp_path / "misturado.aaf", wav)
    als = tmp_path / "misturado.als"
    write_session(
        _sessao_de_taxas_misturas(wav, desvio),
        TEMPLATE,
        als,
        WriteOptions(sample_rate=TAXA, bpm=60, live_origin_samples=UMA_HORA),
        Report(strict=False),
    )
    return aaf, als


def test_t01_o_par_misturado_difere_de_um_frame_de_24_fps(tmp_path):
    """The mixed pair really differs by one frame of 24 fps: 2000 samples."""
    import aaf2

    aaf, als = _par_misturado(tmp_path)

    with aaf2.open(str(aaf), "r") as f:
        comp = next(m for m in f.content.mobs if type(m).__name__ == "CompositionMob")
        taxas = []
        for slot in comp.slots:
            if getattr(getattr(slot, "segment", None), "media_kind", None) != "Sound":
                continue
            er = slot.edit_rate
            taxas.append(er.num / er.den if hasattr(er, "num") else float(er))
    assert taxas == [48000.0, 24.0]

    origem = read_aaf(aaf, Report(strict=False))
    destino = read_session(als, Report(strict=False))

    assert [t.name for t in origem.tracks] == ["AUDIO 48K", "AUDIO 24FPS"]
    assert [t.clips[0].timeline_start_samples for t in origem.tracks] == [UMA_HORA, UMA_HORA]
    assert [t.clips[0].duration_samples for t in origem.tracks] == [TAXA, TAXA]
    assert [t.clips[0].timeline_start_samples for t in destino.tracks] == [
        UMA_HORA,
        UMA_HORA + 2000,
    ]


@pytest.mark.xfail(
    strict=True,
    reason="audit 18/11 T01: mixed slot edit rates collapse the automatic tolerance to 0 samples",
)
def test_t01_tolerancia_automatica_usa_o_frame_da_taxa_misturada(tmp_path):
    """The automatic tolerance uses the frame of the frame-based slot, not zero."""
    from aafton.verify import verify_conversion

    aaf, als = _par_misturado(tmp_path)

    resultado = verify_conversion(aaf, als)

    assert resultado.regime_name.startswith("Frame-exact")
    assert resultado.tolerance_samples == 2000
    assert resultado.ok is True


# ---------------------------------------------------------------------------
# T02 - timecode de origem fora do arquivo de midia
# ---------------------------------------------------------------------------


def test_t02_timecode_de_origem_fora_da_midia_e_erro(tmp_path):
    """The origin timecode before the start of the file is an error, not a silent clip."""
    pytest.importorskip("opentimelineio")
    from aafton.formats import origem as formatos_origem

    pasta = tmp_path / "cena"
    pasta.mkdir()
    shutil.copy(FIXTURE_EDL, pasta / "cena12.edl")
    # O bext do dial01 comeca 1 s depois do TC de origem do primeiro evento.
    _wav_com_bext(pasta / "dial01.wav", segundos=10, time_reference=TC_EVENTO + 48000)
    _wav_com_bext(pasta / "dial02.wav", segundos=10, time_reference=0)

    report = Report(strict=False)
    sessao = formatos_origem.read_origin(
        pasta / "cena12.edl", report, edl_rate=TimecodeRate(25, 1)
    )

    erros = _problemas(report, "FORMAT_SOURCE_TC_OUTSIDE_MEDIA")
    assert len(erros) == 1
    assert erros[0].severity == "error"
    assert report.incomplete is True
    # O clip fora da midia fica sem offset; o outro evento do mesmo arquivo passa.
    assert sessao.tracks[0].clips[0].source_offset_samples is None
    assert sessao.tracks[0].clips[1].source_offset_samples == 144000

    with pytest.raises(StrictModeError) as erro:
        formatos_origem.read_origin(
            pasta / "cena12.edl", Report(strict=True), edl_rate=TimecodeRate(25, 1)
        )
    assert "FORMAT_SOURCE_TC_OUTSIDE_MEDIA" in str(erro.value)


# ---------------------------------------------------------------------------
# T03 - taxa da midia diferente da taxa da sessao na escrita da EDL
# ---------------------------------------------------------------------------


def test_t03_taxa_diferente_da_sessao_nao_escreve_o_timecode_de_origem(tmp_path):
    """A file at another rate than the session is an error and writes no source TC."""
    from aafton.formats import edl_write

    wav = tmp_path / "take44.wav"
    _wav_com_bext(wav, segundos=10, time_reference=480000, sample_rate=44100)
    asset = MediaAsset(
        id="m0",
        filename="take44.wav",
        channels=1,
        sample_rate=44100,
        duration_samples=44100 * 10,
        status="resolved",
        resolved_path=str(wav),
    )
    clip = ClipIR(
        id="t0c0",
        track_id="t0",
        channel_index=0,
        event_index=0,
        name="take44.wav",
        media_id="m0",
        timeline_start_samples=UMA_HORA,
        timeline_end_samples=UMA_HORA + 48000,
        source_offset_samples=48000,
    )
    track = TrackIR(
        id="t0", source_index=0, name="TAKE", kind="audio", channel_count=1, clips=[clip]
    )
    sessao = SessionIR(
        name="CENA",
        sample_rate=TAXA,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=UMA_HORA,
        tracks=[track],
        media=[asset],
    )
    destino = tmp_path / "cena.edl"
    report = Report(strict=False)

    edl_write.write_edl(sessao, destino, report)

    erros = _problemas(report, "FORMAT_SOURCE_TC_RATE_MISMATCH")
    assert len(erros) == 1
    assert erros[0].severity == "error"
    linha = next(
        linha
        for linha in destino.read_text(encoding="utf-8").splitlines()
        if linha.startswith("001")
    )
    source_in = linha.split()[4]
    do_bext = frames_to_label((480000 + 48000) * 25 // TAXA, TimecodeRate(25, 1))
    assert source_in != do_bext


# ---------------------------------------------------------------------------
# T04 - slot de som sem Sequence nem SourceClip alcancavel
# ---------------------------------------------------------------------------


def test_t04_slot_sem_sequence_e_ignorado_com_aviso(tmp_path):
    """A sound slot with no reachable Sequence is dropped, with a warning."""
    import aaf2
    from aafton.als_write import WriteOptions, write_session
    from aafton.media_extract import resolve_asset

    wav = tmp_path / "voz.wav"
    wav.write_bytes(wav_bytes(TAXA * 5))
    aaf = tmp_path / "orfao.aaf"
    with aaf2.open(str(aaf), "w") as f:
        composition = f.create.CompositionMob("ORFAO")
        f.content.mobs.append(composition)
        meta = {
            "format": {"filename": str(wav.resolve()), "format_name": "wav"},
            "streams": [
                {
                    "codec_type": "audio",
                    "codec_name": "pcm_s16le",
                    "sample_rate": TAXA,
                    "duration_ts": TAXA * 60,
                    "channels": 1,
                }
            ],
        }
        master_mob, _source_mob, _tape_mob = f.content.link_external_wav(meta)

        slot = composition.create_timeline_slot(edit_rate=TAXA)
        slot.name = "DIALOGO 1"
        seq = f.create.Sequence(media_kind="sound")
        slot.segment = seq
        clip = f.create.SourceClip(media_kind="sound", start=0)
        clip.length = TAXA
        clip.mob = master_mob
        clip.slot_id = 1
        seq.components.append(clip)

        # O slot orfao tem som, mas nenhum Sequence nem SourceClip dentro.
        slot_orfao = composition.create_timeline_slot(edit_rate=TAXA)
        slot_orfao.name = "TRACK ORFAO"
        filler = f.create.Filler("sound", length=TAXA)
        filler.media_kind = "sound"
        slot_orfao.segment = filler

        tc_slot = composition.create_timeline_slot(edit_rate=25)
        tc_slot.name = "TC"
        tc = f.create.Timecode(fps=25, drop=False)
        tc.start = 90000
        tc.length = 50
        tc_slot.segment = tc

    report = Report(strict=False)
    sessao = read_aaf(aaf, report)

    avisos = _problemas(report, "AAF_NO_SEQUENCE")
    assert len(avisos) == 1
    assert avisos[0].severity == "warning"
    assert [t.name for t in sessao.tracks] == ["DIALOGO 1"]

    for asset in sessao.media:
        resolve_asset(asset, [tmp_path], report)
    als = tmp_path / "orfao.als"
    write_session(
        sessao,
        TEMPLATE,
        als,
        WriteOptions(sample_rate=TAXA, bpm=60, live_origin_samples=UMA_HORA),
        report,
    )
    texto = gzip.open(als, "rb").read().decode("utf-8")
    assert "DIALOGO 1" in texto
    assert "TRACK ORFAO" not in texto


# ---------------------------------------------------------------------------
# T05 - contagem de canal deduzida da midia
# ---------------------------------------------------------------------------


def _als_minimo(path: Path, faixas: list) -> Path:
    """Writes a minimal .als: each track is (Id, name, [(clip, wav, start, end)])."""
    partes = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<Ableton>",
        "  <LiveSet>",
        "    <Tracks>",
    ]
    for id_track, nome, clips in faixas:
        partes.append(f'      <AudioTrack Id="{id_track}">')
        partes.append(f'        <Name><EffectiveName Value="{nome}" /></Name>')
        partes.append(
            "        <DeviceChain><MainSequencer><Sample><ArrangerAutomation><Events>"
        )
        for nome_clip, wav, inicio, fim in clips:
            partes.append(f'          <AudioClip Id="{nome_clip}">')
            partes.append(f'            <Name Value="{nome_clip}" />')
            partes.append(f'            <CurrentStart Value="{inicio}" />')
            partes.append(f'            <CurrentEnd Value="{fim}" />')
            partes.append('            <Loop><LoopStart Value="0" /></Loop>')
            partes.append("            <SampleRef><FileRef>")
            partes.append(f'              <Path Value="{wav}" />')
            partes.append("            </FileRef></SampleRef>")
            partes.append("          </AudioClip>")
        partes.append("        </Events></ArrangerAutomation></Sample></MainSequencer></DeviceChain>")
        partes.append("      </AudioTrack>")
    partes.extend(["    </Tracks>", "  </LiveSet>", "</Ableton>"])
    with gzip.open(path, "wb") as handle:
        handle.write("\n".join(partes).encode("utf-8"))
    return path


def _sidecar(path: Path, *, bindings: list | None = None) -> Path:
    """Writes the sidecar of the .als with the given source bindings."""
    from aafton.sidecar import Anchor, write_sidecar

    return write_sidecar(
        path,
        Anchor(
            live_origin_samples=0,
            session_start_samples=0,
            sample_rate=TAXA,
            timecode_rate=TimecodeRate(25, 1),
            bpm=120.0,
            track_bindings=bindings,
        ),
    )


def test_t05_canais_divergentes_na_track_avisam_e_assumem_o_maior(tmp_path):
    """Clips with different channel counts warn and the track takes the largest."""
    mono = tmp_path / "mono.wav"
    mono.write_bytes(wav_bytes(TAXA, channels=1))
    stereo = tmp_path / "stereo.wav"
    stereo.write_bytes(wav_bytes(TAXA, channels=2))
    als = _als_minimo(
        tmp_path / "canais.als",
        [("1", "DIALOGO 1", [("mono", mono.resolve(), 0, 1), ("stereo", stereo.resolve(), 4, 5)])],
    )
    _sidecar(als)
    report = Report(strict=False)

    sessao = read_session(als, report)

    avisos = _problemas(report, "TRACK_CHANNEL_MISMATCH")
    assert len(avisos) == 1
    assert avisos[0].severity == "warning"
    assert sessao.tracks[0].channel_count == 2


# ---------------------------------------------------------------------------
# T06 - binding de origem usado por duas faixas
# ---------------------------------------------------------------------------


def _als_com_duas_faixas(tmp_path: Path, wav: Path) -> Path:
    """Writes a .als with two audio tracks carrying the same Live Id."""
    return _als_minimo(
        tmp_path / "ambiguo.als",
        [
            ("7", "DIALOGO 1", [("primeiro", wav.resolve(), 0, 1)]),
            ("7", "DIALOGO 2", [("segundo", wav.resolve(), 4, 5)]),
        ],
    )


def test_t06_binding_reaproveitado_por_outra_track_avisa(tmp_path):
    """A second track claiming the same binding is refused, with a warning."""
    wav = tmp_path / "voz.wav"
    wav.write_bytes(wav_bytes(TAXA))
    als = _als_com_duas_faixas(tmp_path, wav)
    _sidecar(
        als,
        bindings=[
            {
                "index": 0,
                "live_track_id": "7",
                "track_name": "DIALOGO 1",
                "origin_slot_id": 0,
                "channel_count": 1,
            }
        ],
    )
    report = Report(strict=False)

    sessao = read_session(als, report)

    avisos = _problemas(report, "AMBIGUOUS_TRACK_MATCH")
    assert len(avisos) == 1
    assert avisos[0].severity == "warning"
    assert [t.source_index for t in sessao.tracks] == [0, 1]
    assert [t.clips[0].name for t in sessao.tracks] == ["primeiro", "segundo"]


@pytest.mark.xfail(
    strict=True,
    reason="audit 18/11 T06: two bindings with the same live_track_id raise no AMBIGUOUS_TRACK_MATCH",
)
def test_t06_binding_usado_por_duas_tracks_avisa_e_nao_reaproveita(tmp_path):
    """Two bindings with the same live_track_id warn about the ambiguity."""
    wav = tmp_path / "voz.wav"
    wav.write_bytes(wav_bytes(TAXA))
    als = _als_com_duas_faixas(tmp_path, wav)
    _sidecar(
        als,
        bindings=[
            {
                "index": 0,
                "live_track_id": "7",
                "track_name": "DIALOGO 1",
                "origin_slot_id": 0,
                "channel_count": 1,
            },
            {
                "index": 1,
                "live_track_id": "7",
                "track_name": "DIALOGO 2",
                "origin_slot_id": 1,
                "channel_count": 1,
            },
        ],
    )
    report = Report(strict=False)

    sessao = read_session(als, report)

    assert "AMBIGUOUS_TRACK_MATCH" in _codigos(report)
    assert [t.source_index for t in sessao.tracks] == [0, 1]
    assert [t.clips[0].name for t in sessao.tracks] == ["primeiro", "segundo"]


# ---------------------------------------------------------------------------
# T07 - AAF sem CompositionMob
# ---------------------------------------------------------------------------


def test_t07_aaf_sem_compositionmob_e_erro_e_nao_sessao_vazia(tmp_path):
    """An AAF with no CompositionMob is an error, not an empty conversion."""
    import aaf2

    aaf = tmp_path / "sem_composicao.aaf"
    with aaf2.open(str(aaf), "w") as f:
        master = f.create.MasterMob("SO MASTER")
        f.content.mobs.append(master)

    with pytest.raises(StrictModeError) as erro:
        read_aaf(aaf, Report(strict=True))
    assert "AAF_NO_COMPOSITION" in str(erro.value)

    report = Report(strict=False)
    sessao = read_aaf(aaf, report)

    assert sessao.tracks == []
    assert report.incomplete is True
    assert "AAF_NO_COMPOSITION" in _codigos(report)
