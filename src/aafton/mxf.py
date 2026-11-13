"""Extracao da midia MXF (OP-Atom e OP1a) com o ffmpeg.

O MXF e um container: o audio esta la dentro e o ffmpeg sabe ler. Este
modulo transforma um MXF em um WAV PCM de 24 bits, com o timecode do MXF
no chunk bext (time_reference), que e o que o resto do projeto sabe ler.

Cada MXF vira um WAV so, com todos os canais de audio juntos. O nome do WAV leva
um resumo do caminho de origem, para dois MXF com o mesmo nome em pastas
diferentes nao virarem o mesmo arquivo. Sem ffmpeg ou ffprobe no PATH, ou
quando o arquivo nao e um MXF valido, a extracao devolve None e o chamador
mantem o MXF como midia nao suportada.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import wave
from fractions import Fraction
from pathlib import Path

from .ir import MediaAsset
from .media_extract import file_sha256
from .report import Report
from .timecode import DROP_FRAME_RATES, TimecodeRate, label_to_frames, label_to_samples

ROTULO_SEM_TIMECODE = "00:00:00:00"


def resolver_mxf(
    asset: MediaAsset, escolhido: Path, report: Report, mxf_dir, taxa_tc
) -> Path | None:
    """Extrai o MXF para WAV e resolve o asset; None quando nao deu para extrair.

    Sem a pasta de midia, sem ffmpeg ou com a extracao falha, o asset
    continua marcado como midia nao suportada, como sempre foi.
    """
    wav = None
    if mxf_dir is not None:
        wav = extrair_mxf(escolhido, Path(mxf_dir), taxa_tc or TimecodeRate(25, 1), report)
    if wav is None:
        asset.status = "unsupported"
        asset.resolved_path = str(escolhido)
        report.error(
            "MEDIA_MXF_UNSUPPORTED",
            f"could not extract the audio of {escolhido.name}: ffmpeg or ffprobe "
            f"missing, without a media folder or invalid MXF. Install ffmpeg, or export "
            f"with audio in WAV or consolidate the media.",
            {"path": str(escolhido)},
        )
        return None
    return wav


def extrair_mxf(origem: Path, pasta: Path, taxa: TimecodeRate, report: Report) -> Path | None:
    """Extrai o audio de um MXF para um WAV PCM de 24 bits com bext.

    Devolve o caminho do WAV escrito, ou None quando nao da para extrair:
    sem ffmpeg, sem audio no arquivo, ou quando o ffmpeg falha.
    """
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        return None
    dados = probe_arquivo(origem)
    if dados is None:
        return None
    audios = _streams_de_audio(dados)
    if not audios:
        return None
    sample_rate = _sample_rate_unico(audios)
    if sample_rate is None:
        return None
    rotulo, taxa_do_timecode = _timecode_do_arquivo(dados, taxa, report)
    time_reference = round(label_to_samples(rotulo, taxa_do_timecode, sample_rate))
    destino = _grava_wav(origem, pasta, audios, time_reference)
    if destino is None:
        return None
    _registra(origem, destino, audios, rotulo, time_reference, report)
    return destino


def probe_arquivo(origem: Path) -> dict | None:
    """Le os streams e o formato do arquivo com o ffprobe; None quando falha.

    O ffprobe e chamado em bytes: o nome do arquivo pode ter acento e a
    decodificacao do locale quebraria a leitura do json.
    """
    comando = ["ffprobe", "-v", "quiet", "-print_format", "json"]
    comando += ["-show_format", "-show_streams", str(origem)]
    try:
        resultado = subprocess.run(comando, capture_output=True)
    except (OSError, ValueError, TypeError):
        return None
    if resultado.returncode != 0:
        return None
    return _json_do_probe(resultado.stdout)


def _json_do_probe(saida) -> dict | None:
    """O json que o ffprobe devolveu; None quando a saida nao e json."""
    if isinstance(saida, bytes):
        saida = (saida or b"").decode("utf-8", "replace")
    try:
        return json.loads(saida or "")
    except (ValueError, TypeError):
        return None


def tem_video(origem: Path) -> bool:
    """True quando o arquivo tem stream de video.

    Sem ffprobe no PATH, ou quando o ffprobe falha, a resposta e False: um
    MXF sem video conhecido nao serve como video da sessao.
    """
    dados = probe_arquivo(origem)
    if dados is None:
        return False
    return any(stream.get("codec_type") == "video" for stream in dados.get("streams", []))


def _streams_de_audio(dados: dict) -> list[dict]:
    """Os streams de audio do arquivo."""
    streams = dados.get("streams", [])
    return [stream for stream in streams if stream.get("codec_type") == "audio"]


def _sample_rate_unico(audios: list[dict]) -> int | None:
    """Taxa de amostras dos streams de audio; None quando nao sao todos iguais."""
    taxas = set()
    for stream in audios:
        try:
            taxas.add(int(stream.get("sample_rate", 0)))
        except (TypeError, ValueError):
            return None
    if len(taxas) != 1:
        return None
    unica = taxas.pop()
    return unica if unica > 0 else None


def _timecode_do_arquivo(
    dados: dict,
    taxa: TimecodeRate,
    report: Report,
) -> tuple[str, TimecodeRate]:
    """Rotulo do timecode do arquivo e a taxa em que ele e contado."""
    rotulo = _rotulo_do_timecode(dados)
    taxa_do_video = _taxa_do_video(dados)
    taxa_do_timecode = _taxa_do_timecode(taxa_do_video or taxa, rotulo)
    if taxa_do_video is not None and not _mesma_taxa(taxa_do_video, taxa):
        report.warning(
            "MEDIA_MXF_RATE_MISMATCH",
            f"the MXF video is at {taxa_do_video.num}/{taxa_do_video.den} fps and the session at "
            f"{taxa.num}/{taxa.den} fps; the timecode of the MXF was counted in the video",
            {
                "video_fps": taxa_do_video.nominal_fps,
                "session_fps": taxa.nominal_fps,
                "video_rate": f"{taxa_do_video.num}/{taxa_do_video.den}",
                "session_rate": f"{taxa.num}/{taxa.den}",
            },
        )
    if _rotulo_drop_frame(rotulo) and not taxa_do_timecode.drop_frame:
        report.warning(
            "MEDIA_MXF_RATE_MISMATCH",
            f"the timecode of the MXF is drop frame and the rate "
            f"{taxa_do_timecode.num}/{taxa_do_timecode.den} fps does not count drop frame; "
            f"the timecode was counted as non-drop frame",
            {
                "timecode": rotulo,
                "fps": taxa_do_timecode.nominal_fps,
                "rate": f"{taxa_do_timecode.num}/{taxa_do_timecode.den}",
            },
        )
    if rotulo is None or not _rotulo_valido(rotulo, taxa_do_timecode):
        rotulo = ROTULO_SEM_TIMECODE
        report.warning(
            "MEDIA_MXF_TIMECODE_MISSING",
            f"the MXF carries no timecode; the audio was counted from {rotulo}",
            {"timecode": rotulo},
        )
    return rotulo, taxa_do_timecode


def _mesma_taxa(uma: TimecodeRate, outra: TimecodeRate) -> bool:
    """True quando as duas taxas tem o mesmo numerador e denominador."""
    return (uma.num, uma.den) == (outra.num, outra.den)


def _taxa_do_timecode(taxa: TimecodeRate, rotulo: str | None) -> TimecodeRate:
    """Taxa em que o rotulo e contado, com o drop frame do proprio rotulo.

    O rotulo com ';' antes dos frames e drop frame, e so existe em 30000/1001
    e 60000/1001. Um rotulo sem ';' e nao drop frame, mesmo quando a sessao
    conta drop frame.
    """
    if rotulo is None:
        return taxa
    if not _rotulo_drop_frame(rotulo):
        return TimecodeRate(taxa.num, taxa.den, False)
    if (taxa.num, taxa.den) not in DROP_FRAME_RATES:
        return taxa
    return TimecodeRate(taxa.num, taxa.den, True)


def _rotulo_drop_frame(rotulo: str | None) -> bool:
    """True quando o rotulo do timecode usa o separador de drop frame."""
    if not rotulo:
        return False
    return ";" in rotulo.rsplit(":", 1)[-1]


def _rotulo_do_timecode(dados: dict) -> str | None:
    """Rotulo SMPTE do timecode, nos tags do formato ou nos do primeiro stream."""
    tags = dados.get("format", {}).get("tags") or {}
    rotulo = tags.get("timecode")
    if rotulo:
        return str(rotulo)
    for stream in dados.get("streams", []):
        rotulo = (stream.get("tags") or {}).get("timecode")
        if rotulo:
            return str(rotulo)
    return None


def _rotulo_valido(rotulo: str, taxa: TimecodeRate) -> bool:
    """True quando o rotulo e um timecode SMPTE que existe na taxa dada."""
    try:
        label_to_frames(rotulo, taxa)
    except ValueError:
        return False
    return True


def _taxa_do_video(dados: dict) -> TimecodeRate | None:
    """Taxa de frames do stream de video; None quando o arquivo nao tem video."""
    for stream in dados.get("streams", []):
        if stream.get("codec_type") != "video":
            continue
        texto = stream.get("r_frame_rate")
        if not isinstance(texto, str) or not texto:
            continue
        try:
            fracao = Fraction(texto)
        except (ValueError, ZeroDivisionError):
            continue
        if fracao <= 0 or fracao.denominator not in (1, 1001):
            continue
        return TimecodeRate(fracao.numerator, fracao.denominator)
    return None


def _grava_wav(origem: Path, pasta: Path, audios: list[dict], time_reference: int) -> Path | None:
    """Roda o ffmpeg e deixa o WAV no lugar; None quando o ffmpeg falha."""
    resumo = _resumo_do_caminho(origem)
    destino = pasta / f"{origem.stem}_{resumo}_mxf.wav"
    temporario = pasta / f"{origem.stem}_{resumo}_mxf_tmp.wav"
    comando = _comando_do_ffmpeg(origem, temporario, audios, time_reference)
    try:
        pasta.mkdir(parents=True, exist_ok=True)
        resultado = subprocess.run(comando, capture_output=True)
        if resultado.returncode != 0 or not temporario.is_file():
            temporario.unlink(missing_ok=True)
            return None
        os.replace(temporario, destino)
    except (OSError, ValueError, TypeError):
        return None
    return destino


def _resumo_do_caminho(origem: Path) -> str:
    """Oito hex do sha1 do caminho resolvido, para o nome do WAV nao colidir."""
    resumo = hashlib.sha1(str(origem.resolve()).encode("utf-8")).hexdigest()
    return resumo[:8]


def _comando_do_ffmpeg(
    origem: Path,
    destino: Path,
    audios: list[dict],
    time_reference: int,
) -> list[str]:
    """Comando do ffmpeg que extrai o audio do MXF para um WAV de 24 bits."""
    comando = ["ffmpeg", "-y", "-i", str(origem)]
    quantidade = len(audios)
    if quantidade > 1:
        entradas = "".join(f"[0:a:{indice}]" for indice in range(quantidade))
        comando += ["-filter_complex", f"{entradas}amerge=inputs={quantidade}[a]"]
        comando += ["-map", "[a]"]
    else:
        comando += ["-map", "0:a:0"]
    comando += ["-c:a", "pcm_s24le", "-write_bext", "1"]
    comando += ["-metadata", f"time_reference={time_reference}", "-rf64", "never"]
    comando += [str(destino)]
    return comando


def _registra(
    origem: Path,
    destino: Path,
    audios: list[dict],
    rotulo: str,
    time_reference: int,
    report: Report,
) -> None:
    """Registra a extracao no relatorio."""
    report.info(
        "MEDIA_MXF_EXTRACTED",
        f"{origem.name} became {destino.name}",
        {
            "source": str(origem),
            "wav": str(destino),
            "channels": _canais(destino, audios),
            "timecode": rotulo,
            "time_reference": time_reference,
            "sha256": file_sha256(destino),
        },
    )


def _canais(destino: Path, audios: list[dict]) -> int:
    """Numero de canais do WAV extraido."""
    try:
        with wave.open(str(destino), "rb") as arquivo:
            return arquivo.getnchannels()
    except (wave.Error, OSError):
        return sum(int(stream.get("channels", 1)) for stream in audios)
