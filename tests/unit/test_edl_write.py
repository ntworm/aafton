"""Testes da escrita de EDL CMX 3600 a partir do SessionIR."""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

import aafton.cli
from aafton.ir import ClipIR, FadeIR, MarkerIR, MediaAsset, SessionIR, TrackIR
from aafton.media_extract import write_wav_with_bext
from aafton.report import Report, StrictModeError
from aafton.timecode import TimecodeRate

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "formatos" / "cena12.edl"
TAXA = 48000
FPS = TimecodeRate(25, 1)
INICIO = 172800000
# Timecode de arquivo de dial01.wav: 10:00:00:00 a 25 fps.
TC_ARQUIVO = 1728000000


def _modulo(nome: str):
    """Modulo do pacote de formatos.

    O import acontece dentro da funcao para que a falta do modulo apareca
    como falha de cada teste, e nao como erro de coleta do arquivo inteiro.
    """
    from importlib import import_module

    return import_module(f"aafton.formats.{nome}")


def _codigos(report: Report) -> list[str]:
    """Codigos de todos os problemas registrados no relatorio."""
    return [issue.code for issue in report.issues]


def _linhas(caminho: Path) -> list[str]:
    """Linhas do arquivo, sem as vazias."""
    return [linha for linha in caminho.read_text(encoding="utf-8").splitlines() if linha.strip()]


def _wav(destino: Path, segundos: float, time_reference: int | None) -> Path:
    """WAV mono de 48 kHz e 24 bits; sem bext quando o timecode e None."""
    amostras = int(segundos * TAXA)
    if time_reference is None:
        with wave.open(str(destino), "wb") as arquivo:
            arquivo.setnchannels(1)
            arquivo.setsampwidth(3)
            arquivo.setframerate(TAXA)
            arquivo.writeframes(b"\x00\x00\x00" * amostras)
        return destino
    write_wav_with_bext(
        destino,
        b"\x00\x00\x00" * amostras,
        channels=1,
        sample_rate=TAXA,
        bits_per_sample=24,
        time_reference=time_reference,
    )
    return destino


def _clip(cid: str, track: str, nome: str, midia: str, inicio: int, fim: int, off: int) -> ClipIR:
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


def _asset(aid: str, nome: str, segundos: int, caminho: Path) -> MediaAsset:
    """Arquivo de midia da cena: mono de 48 kHz, ja resolvido."""
    return MediaAsset(
        id=aid,
        filename=nome,
        channels=1,
        sample_rate=TAXA,
        duration_samples=int(segundos * TAXA),
        status="resolved",
        resolved_path=str(caminho),
    )


def _cena(tmp_path: Path, com_bext: bool = True) -> SessionIR:
    """Cena CENA12: duas tracks de audio, tres clips e os dois arquivos.

    A sessao comeca em 01:00:00:00; a track 1 tem clips de 2 s e 1 s em
    01:00:00:00 e 01:00:04:00; a track 2 tem clip de 2 s em 01:00:01:00; os
    offsets sao de 1 s, 5 s e 0 s dentro de arquivos que comecam em
    10:00:00:00 e 00:00:00:00.
    """
    _wav(tmp_path / "dial01.wav", 20, TC_ARQUIVO if com_bext else None)
    _wav(tmp_path / "dial02.wav", 10, 0 if com_bext else None)
    dial01 = _asset("m0", "dial01.wav", 20, tmp_path / "dial01.wav")
    dial02 = _asset("m1", "dial02.wav", 10, tmp_path / "dial02.wav")
    primeira = TrackIR(
        id="t0",
        source_index=0,
        name="1",
        kind="audio",
        channel_count=1,
        clips=[
            _clip("t0c0", "t0", "dial01.wav", "m0", 172800000, 172896000, 48000),
            _clip("t0c1", "t0", "dial01.wav", "m0", 172992000, 173040000, 240000),
        ],
    )
    segunda = TrackIR(
        id="t1",
        source_index=1,
        name="2",
        kind="audio",
        channel_count=1,
        clips=[_clip("t1c0", "t1", "dial02.wav", "m1", 172848000, 172944000, 0)],
    )
    return SessionIR(
        name="CENA12",
        sample_rate=TAXA,
        timecode_rate=FPS,
        start_timecode_samples=INICIO,
        tracks=[primeira, segunda],
        media=[dial01, dial02],
    )


def test_edl_escreve_os_eventos_da_cena(tmp_path):
    """A EDL escrita tem as mesmas linhas da fixture da cena CENA12."""
    sessao = _cena(tmp_path)
    destino = tmp_path / "cena12.edl"
    report = Report(strict=False)

    _modulo("edl_write").write_edl(sessao, destino, report)

    assert _linhas(destino) == _linhas(FIXTURE)
    assert report.count("error") == 0


def test_edl_usa_quebra_de_linha_crlf(tmp_path):
    """O arquivo sai em CRLF, sem LF solto."""
    sessao = _cena(tmp_path)
    destino = tmp_path / "cena12.edl"

    _modulo("edl_write").write_edl(sessao, destino, Report(strict=False))

    dados = destino.read_bytes()
    assert b"\r\n" in dados
    assert b"\n" not in dados.replace(b"\r\n", b"")


def test_edl_lida_de_volta_tem_os_clips_da_fixture(tmp_path):
    """A EDL escrita, lida pelo leitor de formatos, da a cena da fixture."""
    pytest.importorskip("opentimelineio")
    from aafton.formats.otio_read import read_timeline

    sessao = _cena(tmp_path)
    destino = tmp_path / "cena12.edl"
    _modulo("edl_write").write_edl(sessao, destino, Report(strict=False))

    lida = read_timeline(destino, Report(strict=False), edl_rate=TimecodeRate(25, 1))

    clips = list(lida.all_clips())
    assert [clip.timeline_start_samples for clip in clips] == [172800000, 172992000, 172848000]
    assert [clip.timeline_end_samples for clip in clips] == [172896000, 173040000, 172944000]
    assert [clip.raw["source_tc_samples"] for clip in clips] == [1728048000, 1728240000, 0]


def test_posicao_fora_do_frame_avisa_uma_vez(tmp_path):
    """Posicao 100 samples fora do frame vira um aviso so, com o erro."""
    sessao = _cena(tmp_path)
    clip = sessao.tracks[0].clips[0]
    clip.timeline_start_samples += 100
    clip.timeline_end_samples += 100
    destino = tmp_path / "cena12.edl"
    report = Report(strict=False)

    _modulo("edl_write").write_edl(sessao, destino, report)

    avisos = [issue for issue in report.issues if issue.code == "FORMAT_POSITION_ROUNDED_TO_FRAME"]
    assert len(avisos) == 1
    assert "2" in avisos[0].message
    assert "100" in avisos[0].message


def test_track_de_dois_canais_escreve_dois_eventos(tmp_path):
    """Clip de track de dois canais vira um evento por canal."""
    sessao = _cena(tmp_path)
    sessao.tracks[0].channel_count = 2
    sessao.tracks[0].clips = [sessao.tracks[0].clips[0]]
    sessao.tracks = [sessao.tracks[0]]
    destino = tmp_path / "cena12.edl"
    report = Report(strict=False)

    _modulo("edl_write").write_edl(sessao, destino, report)

    linhas = [linha for linha in _linhas(destino) if linha.startswith("00")]
    assert [linha.split()[2] for linha in linhas] == ["A", "A2"]
    assert report.count("error") == 0


def test_fade_e_erro_e_nao_deixa_arquivo(tmp_path):
    """Fade e erro; em modo estrito nada e gravado na pasta."""
    sessao = _cena(tmp_path)
    sessao.tracks[0].clips[0].fade_in = FadeIR(kind="in", duration_samples=4800)
    destino = tmp_path / "cena12.edl"
    report = Report(strict=False)

    _modulo("edl_write").write_edl(sessao, destino, report)

    assert "FORMAT_EDL_FEATURE_UNSUPPORTED" in _codigos(report)
    assert destino.exists()

    pasta = tmp_path / "estrito"
    pasta.mkdir()
    with pytest.raises(StrictModeError):
        _modulo("edl_write").write_edl(sessao, pasta / "cena12.edl", Report(strict=True))
    assert list(pasta.iterdir()) == []


def test_wav_sem_bext_avisa_e_comeca_do_zero(tmp_path):
    """Sem bext o timecode de origem e zero, com um aviso por arquivo."""
    sessao = _cena(tmp_path, com_bext=False)
    destino = tmp_path / "cena12.edl"
    report = Report(strict=False)

    _modulo("edl_write").write_edl(sessao, destino, report)

    assert _codigos(report).count("FORMAT_SOURCE_TC_FROM_ZERO") == 2
    assert _linhas(destino)[2].split()[4] == "00:00:01:00"


def test_marcadores_sao_descartados_com_aviso(tmp_path):
    """Marcador da sessao vira um aviso so."""
    sessao = _cena(tmp_path)
    sessao.markers.append(MarkerIR(id="k0", name="TESTE", position_samples=172896000))
    destino = tmp_path / "cena12.edl"
    report = Report(strict=False)

    _modulo("edl_write").write_edl(sessao, destino, report)

    assert _codigos(report).count("FORMAT_EDL_MARKERS_DROPPED") == 1


def test_destino_com_sufixo_desconhecido_falha(tmp_path):
    """Sufixo que nao e de exportacao vira erro e nao grava arquivo."""
    sessao = _cena(tmp_path)
    destino = tmp_path / "cena.txt"
    report = Report(strict=False)

    _modulo("destino").write_destination(sessao, destino, report)

    assert "FORMAT_UNKNOWN_SUFFIX" in _codigos(report)
    assert not destino.exists()


def test_export_pela_cli_escreve_o_edl(monkeypatch, tmp_path):
    """O export do aafton escreve a EDL e sai com zero."""
    sessao = _cena(tmp_path)
    monkeypatch.setattr(aafton.cli, "read_session", lambda *args, **kwargs: sessao)
    destino = tmp_path / "cena12.edl"

    argumentos = ["export", str(tmp_path / "fonte.als"), str(destino), "--permissive"]
    codigo = aafton.cli.main(argumentos)

    assert codigo == 0
    assert destino.exists()
    assert _linhas(destino) == _linhas(FIXTURE)
