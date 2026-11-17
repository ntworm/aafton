"""Track de LTC gerada pelo import com a opcao --ltc.

A track nao vem do AAF: e um LTC sintetico que comeca no primeiro frame
depois da origem e continua dez segundos depois do ultimo clip. Ela entra no
set como uma AudioTrack comum, mas o sidecar marca ``generated`` para que a
leitura do set de volta descarte a track e a midia que so ela usava.

O LTC nunca comeca antes de 00:00:00:00: uma origem do Live anterior a zero
e adiantada para o frame zero, com aviso no relatorio. Uma origem de menos de
meio frame antes de zero vale zero para o Live: o chase de MTC sai com o
offset 00:00:00:00, sem aviso de origem negativa. E um LTC que passaria
do limite de 24:00:00:00 nao e gravado: o relatorio registra o erro e o
import continua sem a track de LTC.
"""

from __future__ import annotations

import math
import wave
from fractions import Fraction
from pathlib import Path

from .audio_audit import normalize_name
from .ir import ClipIR, MediaAsset, SessionIR, TrackIR
from .ltc import LIMITE_DE_HORAS, RATES_LTC, write_ltc_wav
from .media_extract import parse_bext
from .report import Report
from .timecode import (
    TimecodeRate,
    frames_to_label,
    frames_to_samples_exact,
    label_to_frames,
    samples_to_label,
)

# Quantos segundos de LTC o sinal continua depois do ultimo clip.
FOLGA_FINAL_SEGUNDOS = 10

# Folga em samples entre a origem e o primeiro frame do LTC.
FOLGA_INICIAL = 2

# Nome da track gerada e a marca que o sidecar guarda dela.
NOME_DA_TRACK = "LTC"

# Sufixo do nome gerado quando a sessao ja tem uma track chamada LTC.
SUFIXO_DE_AAFTON = " (aafton)"
MARCA = "ltc"


def _chave_da_taxa(rate: TimecodeRate) -> str | None:
    """Chave do RATES_LTC que corresponde a taxa da sessao; None se nao houver."""
    for chave, taxa in RATES_LTC.items():
        if taxa == rate:
            return chave
    return None


def _nome_da_track_gerada(session: SessionIR) -> str:
    """Nome da track gerada: LTC, ou um nome livre quando o LTC ja existe.

    O sidecar indexa os clips pelo nome da track, e o audit recusa um nome
    repetido: duas tracks LTC no mesmo set quebrariam o release-mask. O
    audit compara o nome normalizado, entao 'Ltc' e 'LTC ' colidem com
    'LTC' tambem.
    """
    ocupados = {normalize_name(track.name) for track in session.tracks}
    candidato = NOME_DA_TRACK
    numero = 0
    while normalize_name(candidato) in ocupados:
        numero += 1
        sufixo = SUFIXO_DE_AAFTON if numero == 1 else f"{SUFIXO_DE_AAFTON} {numero}"
        candidato = f"{NOME_DA_TRACK}{sufixo}"
    return candidato


def _primeiro_frame(alvo: int, rate: TimecodeRate, sample_rate: int) -> int:
    """Menor frame cujo inicio exato alcanca o alvo, em samples."""
    passo = frames_to_samples_exact(1, rate, sample_rate)
    return math.ceil(Fraction(alvo) / passo)


def _limites_do_ltc(
    session: SessionIR,
    origin_samples: int,
    rate: TimecodeRate,
    sample_rate: int,
) -> tuple[int, int]:
    """Indices do primeiro e do ultimo frame do LTC, em frames."""
    primeiro = _primeiro_frame(origin_samples + FOLGA_INICIAL, rate, sample_rate)
    ultimo = _primeiro_frame(
        _fim_dos_clips(session, origin_samples) + FOLGA_FINAL_SEGUNDOS * sample_rate,
        rate,
        sample_rate,
    )
    return primeiro, ultimo


def _segundos_antes_de_zero(origin_samples: int, sample_rate: int) -> str:
    """Distancia da origem ate zero, escrita sem sinal negativo.

    O Live nao le um rotulo de timecode negativo: a origem anterior a zero
    vira uma distancia de zero, e nao um rotulo com sinal.
    """
    segundos = -origin_samples / sample_rate
    texto = f"{segundos:.3f}".rstrip("0").rstrip(".")
    return f"{texto} s before 00:00:00:00"


def _fim_dos_clips(session: SessionIR, origin_samples: int) -> int:
    """Maior fim de clip da sessao; a origem quando nao ha clip nenhum."""
    fins = [clip.timeline_end_samples for clip in session.all_clips()]
    return max(fins) if fins else origin_samples


def _nome_do_wav(rotulo: str, chave: str) -> str:
    """Nome do WAV de LTC: o rotulo vira texto de arquivo."""
    return f"LTC_{rotulo.replace(':', '-').replace(';', '-')}_{chave}.wav"


def _asset_do_ltc(caminho: Path, nome: str) -> MediaAsset:
    """Asset do WAV de LTC, com o inicio lido do BWF e o tamanho do arquivo."""
    bext = parse_bext(caminho) or {}
    with wave.open(str(caminho), "rb") as arquivo:
        amostras = arquivo.getnframes()
        taxa = arquivo.getframerate()
    return MediaAsset(
        id="m_ltc",
        filename=nome,
        channels=1,
        sample_rate=taxa,
        duration_samples=amostras,
        status="resolved",
        resolved_path=str(caminho),
        origin_timecode_samples=int(bext.get("time_reference", 0)),
    )


def _track_do_ltc(asset: MediaAsset, nome: str) -> TrackIR:
    """Track de LTC com um clip sem warp, do primeiro sample do WAV ate o fim."""
    inicio = asset.origin_timecode_samples or 0
    track = TrackIR(
        id="t_ltc",
        source_index=-1,
        original_source_indices=[-1],
        name=nome,
        kind="audio",
        channel_count=1,
    )
    track.raw = {"generated": MARCA}
    # A track nasce muda: o LTC no Main e um chiado alto ate ser roteado.
    track.muted = True
    clip = ClipIR(
        id="c_ltc",
        track_id=track.id,
        channel_index=0,
        event_index=0,
        name=nome,
        media_id=asset.id,
        timeline_start_samples=inicio,
        timeline_end_samples=inicio + (asset.duration_samples or 0),
        source_offset_samples=0,
    )
    track.clips.append(clip)
    return track


def _avisa_chase_mtc(
    report: Report,
    primeiro_clip: str | None,
    origin_samples: int,
    rate: TimecodeRate,
    sample_rate: int,
    chave: str,
) -> None:
    """Manda no relatorio os ajustes de MTC que o Live precisa para seguir a picture.

    Uma origem de menos de meio frame antes de zero vale zero para o Live.
    """
    if origin_samples < -_meio_frame(rate, sample_rate):
        report.warning(
            "MTC_CHASE_ORIGIN_NEGATIVE",
            "Live does not accept a negative SMPTE offset in the MTC chase: the "
            f"Live origin is {_segundos_antes_de_zero(origin_samples, sample_rate)}. "
            f"{_sugestao_de_origem(primeiro_clip, rate)}",
            {
                "origin": _segundos_antes_de_zero(origin_samples, sample_rate),
                "origin_samples": origin_samples,
                "rate": chave,
                "suggestion": f"--live-origin {frames_to_label(0, rate)}",
                "first_clip": primeiro_clip,
            },
        )
        return
    offset = samples_to_label(max(origin_samples, 0), rate, sample_rate)
    report.info(
        "MTC_CHASE_SETTINGS",
        f"for Live to follow the picture MTC (Pro Tools as MTC master): enable sync "
        f"MTC on the MIDI input that carries the MTC, set the SMPTE offset to {offset} "
        f"(the TC of the beat zero) and the rate to {chave}",
        {"offset": offset, "rate": chave, "origin_samples": origin_samples},
    )


def _meio_frame(rate: TimecodeRate, sample_rate: int) -> Fraction:
    """Metade de um frame em samples."""
    return frames_to_samples_exact(1, rate, sample_rate) / 2


def _primeiro_clip(session: SessionIR, rate: TimecodeRate, sample_rate: int) -> str | None:
    """Rotulo do primeiro clip da sessao; None quando a sessao nao tem clip.

    O rotulo e o maior frame de origem que a importacao aceita: o Live so
    aceita origens de frame inteiro, e o frame seguinte so vale quando o
    inicio dele, truncado para sample, ainda cabe no clip.
    """
    inicios = [clip.timeline_start_samples for clip in session.all_clips()]
    if not inicios:
        return None
    # A importacao aceita a origem no frame F quando o inicio de F,
    # truncado para sample inteiro (arredondado para baixo), nao passa do
    # clip. O maior F e o primeiro frame que passa do clip, menos um.
    frames = _primeiro_frame(min(inicios) + 1, rate, sample_rate) - 1
    return frames_to_label(frames, rate)


def _sugestao_de_origem(primeiro_clip: str | None, rate: TimecodeRate) -> str:
    """Sugestao de origem que a importacao aceita: entre zero e o primeiro clip.

    Uma origem depois do primeiro clip e recusada com CLIP_BEFORE_ORIGIN. Os
    dois limites usam o rotulo da taxa: em drop frame, eles vem com ';'.
    """
    zero = frames_to_label(0, rate)
    if primeiro_clip is None:
        return f"Use --live-origin {zero} or later to keep the chase aligned"
    return f"Use --live-origin between {zero} and {primeiro_clip} (the first clip)"


def _avisa_inicio_adiantado(report: Report, origin_samples: int, sample_rate: int) -> None:
    """Avisa que o inicio do LTC foi adiantado para o frame zero."""
    report.warning(
        "LTC_START_CLAMPED",
        "the Live origin was "
        f"{_segundos_antes_de_zero(origin_samples, sample_rate)} and the LTC was moved "
        "forward to 00:00:00:00 (frame 0)",
        {
            "origin": _segundos_antes_de_zero(origin_samples, sample_rate),
            "origin_samples": origin_samples,
            "start": "00:00:00:00",
            "clamped": True,
        },
    )


def _recusa_acima_de_24h(
    report: Report,
    primeiro: int,
    ultimo: int,
    rate: TimecodeRate,
) -> None:
    """Recusa o LTC que passaria do limite de 24:00:00:00."""
    inicio = frames_to_label(primeiro, rate)
    fim = frames_to_label(ultimo, rate)
    report.error(
        "LTC_TRACK_SKIPPED_24H",
        f"the LTC would go from {inicio} to {fim} and would pass the limit of {LIMITE_DE_HORAS}; "
        "no LTC track was created",
        {"start": inicio, "end": fim, "limit": LIMITE_DE_HORAS},
    )


def _avisa_track_escrita(
    report: Report,
    nome: str,
    rotulo: str,
    chave: str,
    level_dbfs: float,
    caminho: Path,
) -> None:
    """Manda no relatorio a track de LTC que foi escrita."""
    report.info(
        "LTC_TRACK_WRITTEN",
        f"track {nome} generated from {rotulo} at the rate {chave}, "
        f"level {level_dbfs} dBFS: {caminho}. "
        f"The track is muted and goes out through Main: route it to a dedicated "
        f"physical output and unmute it before playing",
        {
            "start": rotulo,
            "rate": chave,
            "level_dbfs": level_dbfs,
            "path": str(caminho),
            "output": "Main",
            "muted": True,
        },
    )


def add_ltc_track(
    session: SessionIR,
    origin_samples: int,
    media_dir: Path,
    report: Report,
    level_dbfs: float = -18.0,
) -> TrackIR | None:
    """Cria a track de LTC da sessao, grava o WAV e devolve a track.

    Devolve None quando a taxa da sessao nao tem LTC equivalente ou quando o
    LTC passaria do limite de 24:00:00:00: o import continua, com o
    resultado no relatorio.
    """
    rate = session.timecode_rate
    sample_rate = session.sample_rate
    chave = _chave_da_taxa(rate)
    if chave is None:
        report.warning(
            "LTC_RATE_UNSUPPORTED",
            f"the rate {rate.num}/{rate.den} fps of the session has no LTC equivalent; "
            "no LTC track was created",
            {"rate": f"{rate.num}/{rate.den}", "fps": float(rate.fps)},
        )
        return None
    primeiro, ultimo = _limites_do_ltc(session, origin_samples, rate, sample_rate)
    primeiro_clip = _primeiro_clip(session, rate, sample_rate)
    if primeiro < 0:
        primeiro = 0
        _avisa_inicio_adiantado(report, origin_samples, sample_rate)
    if ultimo > label_to_frames(LIMITE_DE_HORAS, rate):
        _recusa_acima_de_24h(report, primeiro, ultimo, rate)
        return None
    nome = _nome_da_track_gerada(session)
    rotulo = frames_to_label(primeiro, rate)
    arquivo = _nome_do_wav(rotulo, chave)
    caminho = media_dir / arquivo
    write_ltc_wav(caminho, rotulo, rate, ultimo - primeiro, sample_rate, level_dbfs)
    asset = _asset_do_ltc(caminho, arquivo)
    session.media.append(asset)
    track = _track_do_ltc(asset, nome)
    session.tracks.append(track)
    _avisa_track_escrita(report, nome, rotulo, chave, level_dbfs, caminho)
    _avisa_chase_mtc(report, primeiro_clip, origin_samples, rate, sample_rate, chave)
    return track


def _e_gerada(track: TrackIR) -> bool:
    """Diz se a track foi gerada por esta ferramenta."""
    raw = getattr(track, "raw", None)
    return isinstance(raw, dict) and bool(raw.get("generated"))


def drop_generated_tracks(session: SessionIR, report: Report) -> None:
    """Tira do set as tracks geradas e as midias que so elas usavam."""
    geradas = [track for track in session.tracks if _e_gerada(track)]
    if not geradas:
        return
    session.tracks = [track for track in session.tracks if not _e_gerada(track)]
    usadas = {clip.media_id for track in session.tracks for clip in track.clips if clip.media_id}
    orfas = {clip.media_id for track in geradas for clip in track.clips if clip.media_id}
    session.media = [asset for asset in session.media if asset.id not in (orfas - usadas)]
    for track in geradas:
        report.info(
            "LTC_TRACK_SKIPPED",
            f"the generated track {track.name!r} was discarded when reading the set",
            {"track": track.name},
        )
