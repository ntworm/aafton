"""Testes da rodada 3 dos escritores e dos leitores de formatos.

Cobre a duracao da fonte na EDL, o mudo, o volume e o pan da track, o canal
unico do FCPXML, a leitura de midia estereo, o offset arredondado do XML e o
reel que colide.
"""

from __future__ import annotations

import json
import shutil
import wave
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from aafton.cli import main
from aafton.formats.destino import write_destination
from aafton.formats.edl_write import write_edl
from aafton.formats.fcpxml_read import read_fcpxml
from aafton.formats.fcpxml_write import write_fcpxml
from aafton.formats.otio_read import read_timeline
from aafton.formats.otio_write import write_fcp_xml
from aafton.ir import ClipIR, MediaAsset, SessionIR, TrackIR
from aafton.media_extract import write_wav_with_bext
from aafton.report import Report, StrictModeError
from aafton.timecode import TimecodeRate, label_to_frames

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures" / "formatos"
TEMPLATE = REPO / "templates" / "live12.als"
FORMATO_XML = "cena12_premiere.xml"

TAXA = 48000
FPS = TimecodeRate(25, 1)
# Timecode de arquivo de dial01.wav: 10:00:00:00 a 25 fps.
TC_ARQUIVO = 1728000000
# Os tres formatos de escrita e o codigo do erro de recurso que cada um usa.
FORMATOS = [
    (".edl", "FORMAT_EDL_FEATURE_UNSUPPORTED"),
    (".xml", "FORMAT_XML_FEATURE_UNSUPPORTED"),
    (".fcpxml", "FORMAT_FCPXML_FEATURE_UNSUPPORTED"),
]


def _wav(destino: Path, segundos: float, canais: int = 1, time_reference: int | None = 0) -> Path:
    """WAV de 48 kHz e 24 bits, com bext quando o timecode e dado."""
    amostras = int(segundos * TAXA)
    dados = b"\x00\x00\x00" * amostras * canais
    if time_reference is None:
        with wave.open(str(destino), "wb") as arquivo:
            arquivo.setnchannels(canais)
            arquivo.setsampwidth(3)
            arquivo.setframerate(TAXA)
            arquivo.writeframes(dados)
        return destino
    write_wav_with_bext(
        destino,
        dados,
        channels=canais,
        sample_rate=TAXA,
        bits_per_sample=24,
        time_reference=time_reference,
    )
    return destino


def _asset(aid: str, nome: str, canais: int, caminho: Path, segundos: float = 2.0) -> MediaAsset:
    """Arquivo de midia da cena, ja resolvido."""
    return MediaAsset(
        id=aid,
        filename=nome,
        channels=canais,
        sample_rate=TAXA,
        duration_samples=int(segundos * TAXA),
        status="resolved",
        resolved_path=str(caminho),
    )


def _clip(
    cid: str,
    track: str,
    nome: str,
    midia: str,
    inicio: int,
    fim: int,
    off: int = 0,
) -> ClipIR:
    """Clip da cena, com o tempo em samples absolutos."""
    return ClipIR(
        id=cid,
        track_id=track,
        channel_index=0,
        event_index=0,
        name=nome,
        media_id=midia,
        timeline_start_samples=inicio,
        timeline_end_samples=fim,
        source_offset_samples=off,
    )


def _sessao(tracks: list[TrackIR], media: list[MediaAsset], taxa: TimecodeRate = FPS) -> SessionIR:
    """Sessao simples, com as tracks e as midias dadas."""
    return SessionIR(
        name="CENA",
        sample_rate=TAXA,
        timecode_rate=taxa,
        start_timecode_samples=0,
        tracks=tracks,
        media=media,
    )


def _linhas_evento(caminho: Path) -> list[list[str]]:
    """Campos de cada linha de evento da EDL."""
    linhas = []
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        if linha[:3].isdigit():
            linhas.append(linha.split())
    return linhas


def _tempos_do_evento(caminho: Path) -> tuple[int, int, int, int]:
    """Os quatro tempos do primeiro evento da EDL, em frames."""
    campos = _linhas_evento(caminho)[0]
    return tuple(label_to_frames(rotulo, FPS) for rotulo in campos[4:8])


def test_edl_duracao_da_fonte_iguala_a_do_registro(tmp_path):
    """Clip de duracao fracionaria tem a mesma duracao na fonte e no registro."""
    pytest.importorskip("opentimelineio")
    wav = _wav(tmp_path / "audio.wav", 2.0)
    asset = _asset("m0", "audio.wav", 1, wav)
    track = TrackIR(
        id="t0",
        source_index=0,
        name="1",
        kind="audio",
        channel_count=1,
        clips=[_clip("t0c0", "t0", "audio.wav", "m0", 900, 3780, 1000)],
    )
    sessao = _sessao([track], [asset])
    destino = tmp_path / "cena.edl"
    report = Report(strict=False)

    write_edl(sessao, destino, report)

    src_in, src_out, rec_in, rec_out = _tempos_do_evento(destino)
    assert src_out - src_in == rec_out - rec_in
    lida = read_timeline(destino, Report(strict=False), edl_rate=FPS)
    assert len(list(lida.all_clips())) == 1


@pytest.mark.parametrize("sufixo,codigo", FORMATOS)
def test_mudo_volume_e_pan_da_track_viram_erro(tmp_path, sufixo, codigo):
    """Mudo, volume e pan da track viram erro de recurso nos tres formatos."""
    pytest.importorskip("opentimelineio")
    wav = _wav(tmp_path / "audio.wav", 2.0)
    asset = _asset("m0", "audio.wav", 1, wav)
    tracks = [
        TrackIR(
            id="t0",
            source_index=0,
            name="MUDO",
            kind="audio",
            channel_count=1,
            muted=True,
            clips=[_clip("t0c0", "t0", "audio.wav", "m0", 0, 96000)],
        ),
        TrackIR(
            id="t1",
            source_index=1,
            name="VOLUME",
            kind="audio",
            channel_count=1,
            gain_linear=0.5,
            clips=[_clip("t1c0", "t1", "audio.wav", "m0", 0, 96000)],
        ),
        TrackIR(
            id="t2",
            source_index=2,
            name="PAN",
            kind="audio",
            channel_count=1,
            pan=0.25,
            clips=[_clip("t2c0", "t2", "audio.wav", "m0", 0, 96000)],
        ),
    ]
    sessao = _sessao(tracks, [asset])
    destino = tmp_path / f"cena{sufixo}"
    report = Report(strict=False)

    write_destination(sessao, destino, report)

    assert destino.exists()
    erros = [issue for issue in report.issues if issue.code == codigo]
    assert len(erros) == 3
    assert {issue.context.get("track") for issue in erros} == {"MUDO", "VOLUME", "PAN"}

    estrito = tmp_path / f"estrito{sufixo}"
    with pytest.raises(StrictModeError):
        write_destination(sessao, estrito, Report(strict=True))
    assert not estrito.exists()


def test_fcpxml_escreve_um_asset_clip_por_clip_estereo(tmp_path):
    """Clip de arquivo estereo vira um asset-clip so, na lane da track."""
    wav = _wav(tmp_path / "par.wav", 2.0, canais=2)
    asset = _asset("m0", "par.wav", 2, wav)
    track = TrackIR(
        id="t0",
        source_index=0,
        name="1",
        kind="audio",
        channel_count=2,
        clips=[_clip("t0c0", "t0", "par.wav", "m0", 0, 96000)],
    )
    sessao = _sessao([track], [asset])
    destino = tmp_path / "cena.fcpxml"
    report = Report(strict=False)

    write_fcpxml(sessao, destino, report)

    assert report.count("error") == 0
    raiz = ET.fromstring(destino.read_text(encoding="utf-8"))
    asset_clips = list(raiz.iter("asset-clip"))
    assert len(asset_clips) == 1
    assert asset_clips[0].get("lane") == "-1"
    assert [a.get("audioChannels") for a in raiz.findall("./resources/asset")] == ["2"]

    lida = read_fcpxml(destino, Report(strict=False))
    assert len(lida.tracks) == 1
    assert lida.tracks[0].channel_count == 2
    assert len(lida.tracks[0].clips) == 1


@pytest.mark.parametrize("sufixo", [".edl", ".xml"])
def test_edl_e_xml_recusam_midia_estereo(tmp_path, sufixo):
    """Midia de dois canais vira erro FORMAT_MULTICHANNEL_UNSUPPORTED."""
    pytest.importorskip("opentimelineio")
    wav = _wav(tmp_path / "par.wav", 2.0, canais=2)
    asset = _asset("m0", "par.wav", 2, wav)
    track = TrackIR(
        id="t0",
        source_index=0,
        name="1",
        kind="audio",
        channel_count=2,
        clips=[_clip("t0c0", "t0", "par.wav", "m0", 0, 96000)],
    )
    sessao = _sessao([track], [asset])
    destino = tmp_path / f"cena{sufixo}"
    report = Report(strict=False)

    write_destination(sessao, destino, report)

    assert destino.exists()
    assert "FORMAT_MULTICHANNEL_UNSUPPORTED" in [issue.code for issue in report.issues]

    estrito = tmp_path / f"estrito{sufixo}"
    with pytest.raises(StrictModeError):
        write_destination(sessao, estrito, Report(strict=True))
    assert not estrito.exists()


def _cena_do_xml(tmp_path: Path, stereo: bool) -> Path:
    """Pasta com a fixture do XML e as duas midias da cena CENA12."""
    pasta = tmp_path / "cena"
    pasta.mkdir()
    shutil.copy(FIXTURES / FORMATO_XML, pasta / FORMATO_XML)
    _wav(pasta / "dial01.wav", 20, canais=2 if stereo else 1, time_reference=TC_ARQUIVO)
    _wav(pasta / "dial02.wav", 10, time_reference=0)
    return pasta


def _codigos(relatorio: Path) -> list[str]:
    """Codigos de todos os problemas do relatorio gravado em json."""
    dados = json.loads(relatorio.read_text(encoding="utf-8"))
    return [issue["code"] for issue in dados["issues"]]


def test_import_do_xml_com_midia_estereo_falha_fechado(tmp_path):
    """O import do XML com midia estereo para no erro de multicanal."""
    pytest.importorskip("opentimelineio")
    cena = _cena_do_xml(tmp_path, stereo=True)
    saida = tmp_path / "saida.als"

    codigo = main(
        ["import", str(cena / FORMATO_XML), str(saida), "--template", str(TEMPLATE)],
    )

    assert codigo == 1
    assert not saida.exists()


def test_import_permissivo_do_xml_com_midia_estereo_reporta(tmp_path):
    """No modo permissivo o multicanal fica no relatorio do import."""
    pytest.importorskip("opentimelineio")
    cena = _cena_do_xml(tmp_path, stereo=True)
    relatorio = tmp_path / "relatorio.json"

    codigo = main(
        [
            "import",
            str(cena / FORMATO_XML),
            str(tmp_path / "saida.als"),
            "--template",
            str(TEMPLATE),
            "--permissive",
            "--report",
            str(relatorio),
        ],
    )

    assert codigo == 1
    assert "FORMAT_MULTICHANNEL_UNSUPPORTED" in _codigos(relatorio)


def test_import_do_xml_com_midia_mono_continua(tmp_path):
    """O import do XML com midia mono nao muda."""
    pytest.importorskip("opentimelineio")
    cena = _cena_do_xml(tmp_path, stereo=False)
    relatorio = tmp_path / "relatorio.json"

    codigo = main(
        [
            "import",
            str(cena / FORMATO_XML),
            str(tmp_path / "saida.als"),
            "--template",
            str(TEMPLATE),
            "--permissive",
            "--report",
            str(relatorio),
        ],
    )

    assert codigo == 0
    assert "FORMAT_MULTICHANNEL_UNSUPPORTED" not in _codigos(relatorio)


def test_xml_conta_o_arredondamento_do_offset_da_fonte(tmp_path):
    """Offset de 48000 samples a 29.97 entra na contagem de arredondamento."""
    pytest.importorskip("opentimelineio")
    wav = _wav(tmp_path / "audio.wav", 2.0, time_reference=0)
    asset = _asset("m0", "audio.wav", 1, wav)
    track = TrackIR(
        id="t0",
        source_index=0,
        name="1",
        kind="audio",
        channel_count=1,
        clips=[_clip("t0c0", "t0", "audio.wav", "m0", 0, 8008, 48000)],
    )
    sessao = _sessao([track], [asset], taxa=TimecodeRate(30000, 1001))
    destino = tmp_path / "cena.xml"
    report = Report(strict=False)

    write_fcp_xml(sessao, destino, report)

    avisos = [issue for issue in report.issues if issue.code == "FORMAT_POSITION_ROUNDED_TO_FRAME"]
    assert len(avisos) == 1
    assert avisos[0].message.startswith("1 positions")


def test_edl_da_reel_unico_para_nomes_iguais(tmp_path):
    """Dois arquivos com o mesmo comeco de nome recebem reels diferentes."""
    nomes = ["dialogue_01.wav", "dialogue_02.wav", "dialogue_03.wav"]
    media = []
    tracks = []
    for indice, nome in enumerate(nomes):
        wav = _wav(tmp_path / nome, 2.0)
        media.append(_asset(f"m{indice}", nome, 1, wav))
        clip = _clip(
            f"t{indice}c0",
            f"t{indice}",
            nome,
            f"m{indice}",
            indice * 96000,
            (indice + 1) * 96000,
        )
        tracks.append(
            TrackIR(
                id=f"t{indice}",
                source_index=indice,
                name=str(indice + 1),
                kind="audio",
                channel_count=1,
                clips=[clip],
            )
        )
    sessao = _sessao(tracks, media)
    destino = tmp_path / "cena.edl"
    report = Report(strict=False)

    write_edl(sessao, destino, report)

    reels = [campos[1] for campos in _linhas_evento(destino)]
    assert reels == ["DIALOGUE", "DIALOG01", "DIALOG02"]
