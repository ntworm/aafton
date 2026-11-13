"""Timecode queimado no proxy de video.

O Live le a imagem de um video sem timecode: o primeiro frame vale hora zero.
Este modulo grava um proxy H.264/AAC com o timecode desenhado no rodape.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Optional

from .ir import SessionIR
from .report import Report
from .timecode import TimecodeRate, samples_to_label

# Fonte monoespacada do Windows, onde o rotulo de timecode fica alinhado.
CAMINHO_FONTE_PADRAO = "C:/Windows/Fonts/consola.ttf"

# Audio silencioso que entra no proxy quando o video de origem nao tem audio.
AUDIO_SILENCIOSO = "anullsrc=channel_layout=stereo:sample_rate=48000"

# Estilo do texto queimado: fonte, cor, caixa e lugar no rodape do quadro.
RODAPE = (
    "fontsize=h/14:fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=8:x=(w-tw)/2:y=h-th-h/20"
)

# Diferenca maxima de taxa, em fps, para o video e a sessao valerem a mesma fonte.
TOLERANCIA_FPS = 0.001


def _fonte_padrao() -> Optional[str]:
    """Caminho da fonte monoespacada do Windows, quando ela existe no disco."""
    if Path(CAMINHO_FONTE_PADRAO).exists():
        return CAMINHO_FONTE_PADRAO
    return None


def _tem_drawtext() -> bool:
    """Diz se o ffmpeg da maquina tem o filtro drawtext."""
    if shutil.which("ffmpeg") is None:
        return False
    try:
        res = subprocess.run(
            ["ffmpeg", "-hide_banner", "-filters"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        return False
    return " drawtext " in res.stdout


def _escapar_timecode(start_label: str) -> str:
    """Escapa os separadores do rotulo para o filtro drawtext."""
    return start_label.replace(":", "\\:").replace(";", "\\;")


def _escapar_fonte(fontfile: str) -> str:
    """Escapa o caminho da fonte para o filtro drawtext."""
    return fontfile.replace("\\", "/").replace(":", "\\:")


def drawtext_filter(start_label: str, rate: TimecodeRate, fontfile: Optional[str] = None) -> str:
    """Filtro drawtext que escreve o timecode no rodape do quadro."""
    partes = [
        f"drawtext=timecode='{_escapar_timecode(start_label)}'",
        f"rate={rate.num}/{rate.den}",
    ]
    if fontfile:
        partes.append(f"fontfile='{_escapar_fonte(fontfile)}'")
    else:
        partes.append("font=monospace")
    partes.append(RODAPE)
    return ":".join(partes)


def burn_command(
    video_path: Path,
    output_path: Path,
    start_label: str,
    rate: TimecodeRate,
    has_audio: bool,
    fontfile: Optional[str] = None,
) -> list[str]:
    """Comando do ffmpeg que queima o timecode e grava o proxy."""
    cmd = ["ffmpeg", "-y", "-i", str(video_path)]
    if not has_audio:
        cmd += ["-f", "lavfi", "-i", AUDIO_SILENCIOSO]
    cmd += ["-vf", drawtext_filter(start_label, rate, fontfile)]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p"]
    cmd += ["-c:a", "aac", "-b:a", "192k"]
    if not has_audio:
        cmd.append("-shortest")
    # O moov vai para a frente do arquivo: o navegador toca sem buscar o fim.
    cmd += ["-movflags", "+faststart"]
    cmd.append(str(output_path))
    return cmd


def _caminho_de_saida(video_path: Path) -> Path:
    """Caminho do proxy queimado, ao lado do video de origem."""
    stem = video_path.stem
    if stem.endswith("_LiveProxy"):
        stem = stem[: -len("_LiveProxy")]
    return video_path.parent / f"{stem}_LiveProxy_TC.mp4"


def _saida_pronta(output_path: Path) -> bool:
    """Diz se o arquivo gravado existe e tem conteudo."""
    return output_path.exists() and output_path.stat().st_size > 0


def _caminho_json(output_path: Path) -> Path:
    """Caminho do JSON que descreve o proxy queimado."""
    return output_path.with_name(output_path.name + ".json")


def _descreve(video_path: Path, start_label: str, rate: TimecodeRate) -> Optional[dict]:
    """Descreve o proxy esperado: a fonte que queimou, o start e a rate."""
    try:
        info = video_path.stat()
    except OSError:
        return None
    return {
        "source": str(video_path.resolve()),
        "source_size": info.st_size,
        "source_mtime_ns": info.st_mtime_ns,
        "start": start_label,
        "rate": f"{rate.num}/{rate.den}",
    }


def _proxy_serve(
    video_path: Path,
    output_path: Path,
    start_label: str,
    rate: TimecodeRate,
) -> bool:
    """Diz se o proxy ja foi queimado com a mesma fonte, o mesmo start e a mesma rate."""
    if not _saida_pronta(output_path):
        return False
    try:
        dados = json.loads(_caminho_json(output_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return dados == _descreve(video_path, start_label, rate)


def _grava_json(
    output_path: Path,
    video_path: Path,
    start_label: str,
    rate: TimecodeRate,
    report: Report,
) -> None:
    """Grava o JSON do proxy queimado; avisa quando o disco nao deixou."""
    caminho = _caminho_json(output_path)
    dados = _descreve(video_path, start_label, rate)
    gravou = False
    if dados is not None:
        try:
            caminho.write_text(json.dumps(dados, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            gravou = True
        except OSError:
            gravou = False
    if not gravou:
        report.warning(
            "VIDEO_TC_META_FAILED",
            f"could not write the json of the proxy {output_path.name}",
            {"path": str(caminho)},
        )


def burn_timecode_proxy(
    video_path: Path,
    start_label: str,
    rate: TimecodeRate,
    report: Report,
) -> Optional[Path]:
    """Grava o proxy com o timecode queimado; None quando nao foi possivel."""
    output_path = _caminho_de_saida(video_path)
    if _proxy_serve(video_path, output_path, start_label, rate):
        report.info(
            "VIDEO_TC_REUSED",
            f"timecode {start_label} already burned into the proxy {output_path.name}",
            {"start": start_label, "rate": f"{rate.num}/{rate.den}", "path": str(output_path)},
        )
        return output_path
    if not _tem_drawtext():
        report.warning(
            "VIDEO_BURN_UNAVAILABLE",
            "ffmpeg with the drawtext filter is not available; "
            "the video proxy stays without the burned timecode",
            {"path": str(video_path)},
        )
        return None

    from .video import video_has_audio

    cmd = burn_command(
        video_path,
        output_path,
        start_label,
        rate,
        video_has_audio(video_path),
        _fonte_padrao(),
    )
    try:
        res = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")
        codigo = res.returncode
    except OSError:
        codigo = -1
    if codigo != 0 or not _saida_pronta(output_path):
        report.warning(
            "VIDEO_BURN_FAILED",
            f"could not burn the timecode into {video_path.name}",
            {"path": str(output_path)},
        )
        return None
    _grava_json(output_path, video_path, start_label, rate, report)
    report.info(
        "VIDEO_TC_BURNED",
        f"timecode {start_label} burned into the proxy {output_path.name}",
        {
            "start": start_label,
            "rate": f"{rate.num}/{rate.den}",
            "path": str(output_path),
        },
    )
    return output_path


def _taxa_do_video(video_path: Path) -> Optional[Fraction]:
    """Taxa de frames do video, lida pelo ffprobe; None quando nao da para ler."""
    if shutil.which("ffprobe") is None:
        return None
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=r_frame_rate",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")
    except OSError:
        return None
    if res.returncode != 0:
        return None
    try:
        return Fraction(res.stdout.strip())
    except (ValueError, ZeroDivisionError):
        return None


def _divergencia_de_taxa(video_rate: Fraction, session_rate: Fraction) -> bool:
    """Diz se a taxa do video e outra que a taxa da sessao."""
    return abs(float(video_rate) - float(session_rate)) > TOLERANCIA_FPS


def burn_for_live(
    original: Path,
    current: Path,
    frame0_samples: int,
    session: SessionIR,
    report: Report,
) -> Path:
    """Proxy com o timecode queimado; o caminho atual quando nao deu para gravar."""
    taxa_video = _taxa_do_video(original)
    taxa_sessao = session.timecode_rate.fps
    if taxa_video is not None and _divergencia_de_taxa(taxa_video, taxa_sessao):
        report.warning(
            "VIDEO_BURN_RATE_MISMATCH",
            f"the video is at {float(taxa_video):.3f} fps and the session at "
            f"{float(taxa_sessao):.3f} fps; the burned timecode would be off every second",
            {
                "video_fps": float(taxa_video),
                "session_fps": float(taxa_sessao),
                "video_rate": f"{taxa_video.numerator}/{taxa_video.denominator}",
                "session_rate": f"{session.timecode_rate.num}/{session.timecode_rate.den}",
                "path": str(original),
            },
        )
        return current
    label = samples_to_label(frame0_samples, session.timecode_rate, session.sample_rate)
    queimado = burn_timecode_proxy(original, label, session.timecode_rate, report)
    if queimado is None:
        return current
    return queimado
