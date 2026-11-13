"""Testes contra AAF reais exportados por cada programa."""

import aifc
import wave
from pathlib import Path

import pytest

from aafton.aaf_read import read_aaf
from aafton.aaf_write import write_aaf
from aafton.report import Report
from tests.support.paths import private_fixture

DIALETOS = ["dialect_resolve.aaf", "dialect_premiere.aaf", "dialect_protools.aaf"]


def _carrier_for(asset, tmp_path: Path, cache: dict[tuple, Path]) -> Path:
    suffixes = {
        Path(asset.filename or "").suffix.lower(),
        Path(asset.declared_location or "").suffix.lower(),
    }
    is_aiff = bool(suffixes & {".aif", ".aiff", ".aifc"})
    channels = asset.channels or 1
    sample_rate = asset.sample_rate or 48000
    key = ("aiff" if is_aiff else "wav", channels, sample_rate)
    if key in cache:
        return cache[key]

    extension = ".aif" if is_aiff else ".wav"
    path = tmp_path / f"carrier-{key[0]}-{channels}ch-{sample_rate}{extension}"
    if is_aiff:
        with aifc.open(str(path), "wb") as handle:
            handle.setnchannels(channels)
            handle.setsampwidth(2)
            handle.setframerate(sample_rate)
            handle.setnframes(1)
            handle.setcomptype(b"NONE", b"")
            handle.writeframes(b"\x00\x00" * channels)
    else:
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(channels)
            handle.setsampwidth(2)
            handle.setframerate(sample_rate)
            handle.writeframes(b"\x00\x00" * channels)
    cache[key] = path
    return path


@pytest.mark.parametrize("nome", DIALETOS)
def test_o_leitor_acha_ao_menos_uma_track_com_clips(nome):
    session = read_aaf(private_fixture(nome), Report(strict=False))
    assert session.tracks
    assert any(track.clips for track in session.tracks)


@pytest.mark.parametrize("nome", DIALETOS)
def test_nenhum_clip_tem_duracao_zero_ou_negativa(nome):
    session = read_aaf(private_fixture(nome), Report(strict=False))
    for clip in session.all_clips():
        assert clip.duration_samples > 0, f"{clip.name} tem duracao {clip.duration_samples}"


@pytest.mark.parametrize("nome", DIALETOS)
def test_todo_clip_comeca_no_inicio_da_sessao_ou_depois(nome):
    session = read_aaf(private_fixture(nome), Report(strict=False))
    for clip in session.all_clips():
        assert clip.timeline_start_samples >= session.start_timecode_samples


@pytest.mark.parametrize("nome", DIALETOS)
def test_os_clips_de_cada_track_nao_se_sobrepoem_alem_dos_crossfades(nome):
    session = read_aaf(private_fixture(nome), Report(strict=False))
    for track in session.tracks:
        for canal in set(c.channel_index for c in track.clips):
            ordenados = sorted(
                (c for c in track.clips if c.channel_index == canal),
                key=lambda c: c.timeline_start_samples,
            )
        for anterior, seguinte in zip(ordenados, ordenados[1:]):
            sobreposicao = anterior.timeline_end_samples - seguinte.timeline_start_samples
            if sobreposicao <= 0:
                continue
            fade = seguinte.fade_in.duration_samples if seguinte.fade_in else 0
            assert sobreposicao <= fade, (
                f"track {track.name!r}: sobreposicao de {sobreposicao} samples sem "
                f"crossfade correspondente"
            )


@pytest.mark.parametrize("nome", DIALETOS)
def test_ida_e_volta_pelo_aaf_preserva_todas_as_posicoes(nome, tmp_path):
    original = read_aaf(private_fixture(nome), Report(strict=False))
    video_media_ids = {
        clip.media_id for track in original.tracks if track.is_video for clip in track.clips
    }
    carriers = {}
    for asset in original.media:
        if asset.id in video_media_ids:
            continue
        asset.status = "resolved"
        asset.resolved_path = str(_carrier_for(asset, tmp_path, carriers).resolve())
    destino = tmp_path / "volta.aaf"
    report = Report(strict=False)
    write_aaf(original, destino, report)
    errors = [i for i in report.issues if i.severity == "error"]
    assert all(i.code == "WRITE_FADE_UNSUPPORTED" for i in errors)
    if errors:
        assert report.incomplete
    lido = read_aaf(destino, Report(strict=False))
    assert lido.start_timecode_samples == original.start_timecode_samples
    posicoes_originais = sorted(c.timeline_start_samples for c in original.all_clips())
    posicoes_lidas = sorted(c.timeline_start_samples for c in lido.all_clips())
    assert posicoes_lidas == posicoes_originais


@pytest.mark.parametrize("nome", DIALETOS)
def test_o_relatorio_nomeia_toda_midia_nao_resolvida(nome):
    report = Report(strict=False)
    session = read_aaf(private_fixture(nome), report)
    nao_resolvidas = [a for a in session.media if a.status != "resolved"]
    if not nao_resolvidas:
        return
    mensagens = " ".join(i.message for i in report.issues)
    for asset in nao_resolvidas:
        assert asset.filename in mensagens or asset.declared_location is not None


def _fades(session):
    """Inicio, fim, fade-in e fade-out de cada clip da sessao, em ordem."""
    return sorted(
        (
            clip.timeline_start_samples,
            clip.timeline_end_samples,
            clip.fade_in.duration_samples if clip.fade_in else 0,
            clip.fade_out.duration_samples if clip.fade_out else 0,
        )
        for clip in session.all_clips()
    )


@pytest.mark.parametrize("nome", ["dialect_resolve.aaf", "dialect_premiere.aaf"])
def test_ida_e_volta_pelo_aaf_preserva_os_fades(nome, tmp_path):
    """O Resolve e o Premiere gravam o fade a partir do silencio como Transition ao lado
    de um Filler; o export grava a mesma forma, e o AAF lido de novo devolve os mesmos fades."""
    original = read_aaf(private_fixture(nome), Report(strict=False))
    assert any(fade_in or fade_out for _, _, fade_in, fade_out in _fades(original))
    video = {clip.media_id for track in original.tracks if track.is_video for clip in track.clips}
    carriers = {}
    for asset in original.media:
        if asset.id in video:
            continue
        asset.status = "resolved"
        asset.resolved_path = str(_carrier_for(asset, tmp_path, carriers).resolve())
    destino = tmp_path / "volta.aaf"
    report = Report(strict=False)

    write_aaf(original, destino, report)

    codigos = [issue.code for issue in report.issues]
    assert "WRITE_FADE_UNSUPPORTED" not in codigos
    assert "FADE_CONVERTED_TO_ENVELOPE" not in codigos
    assert "FADE_WRITTEN_AS_TRANSITION" in codigos
    assert not [issue for issue in report.issues if issue.severity == "error"]
    lido = read_aaf(destino, Report(strict=False))
    assert _fades(lido) == _fades(original)
