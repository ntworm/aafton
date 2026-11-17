"""Production audio step of the app page: the recorder folder read and converted.

The read calls the same reader as the import, ``read_production``, with a
report that does not raise, and writes nothing. The conversion calls the
shared pipeline with the circled filter, and the verdict is the verify of
production audio run on the written set.
"""

from __future__ import annotations

import dataclasses
import re
import struct
from pathlib import Path

from ..bwf_meta import BwfInfo, read_bwf
from ..ir import SessionIR, TrackIR
from ..media_extract import read_audio_metadata
from ..pipeline import ImportOptions, check_production_output, import_session
from ..report import Report, StrictModeError
from ..som_direto import is_production_input, read_production
from ..som_direto_verify import verify_production
from ..timecode import TimecodeRate
from . import app_convert, app_view
from .app_messages import falha_da_conversao, falha_do_motor
from .app_read import error

# A direcao desta aba nas rotas e no LAST das conversoes.
DIRECTION = "production"

SEP = app_view.MIDDLE_DOT

# Track de uma entrada In N sem microfone fixo: cada clip leva o microfone.
# A track extra das sobreposicoes, ``In N (2)``, segue a mesma regra.
ENTRADA_SIMPLES = re.compile(r"In \d+(?: \(\d+\))?")

# Codigos de formato do fmt: float e o extensivel, que traz o subformato.
FORMATO_FLOAT = 3
FORMATO_EXTENSIVEL = 0xFFFE

# Quantos clips a tabela de uma track mostra de cada vez.
LIMITE_CLIPS = 300


def read(payload: dict) -> dict:
    """Reads the recorder folder or WAV and describes the set it becomes."""
    origem = str(payload.get("input") or "").strip()
    if not origem:
        return error("bad_request", "The recorder folder is required.")
    caminho = Path(origem)
    if not caminho.exists():
        return error("not_found", app_convert.sumiu(caminho))
    if not is_production_input(caminho):
        return error("unsupported", "Production audio is a folder or a .wav file.")
    report = Report(strict=False)
    try:
        sessao = read_production(caminho, report)
    except Exception as exc:  # noqa: BLE001 - the message of the engine is the answer
        return error("unreadable", falha_do_motor(exc, caminho, "read"))
    fonte = {"path": str(caminho.resolve()), "name": sessao.name, "format": "production"}
    tracks = [_track(indice, track, sessao) for indice, track in enumerate(sessao.tracks)]
    return {
        "ok": True,
        "direction": DIRECTION,
        "source": fonte,
        "facts": _facts(sessao, report),
        "timeline": _linha_do_set(sessao),
        "blocks": [_bloco(bloco, sessao.timecode_rate) for bloco in sessao.layout_blocks],
        "tracks": tracks,
        "notes": app_view.notes(report.issues),
        "suggest": {"output": str(_destino_sugerido(caminho))},
    }


def _destino_sugerido(caminho: Path) -> Path:
    """The Live set beside the source, in a project folder of its own.

    The project never goes inside the recorder folder: the monos of the set
    would be read as takes by the next read and by the verify.
    """
    pasta = caminho.resolve().parent
    nome = caminho.stem if caminho.is_file() else caminho.name
    if caminho.is_file():
        # WAV solto: a pasta dele e a diaria, dentro da pasta do gravador;
        # o projeto vai ao lado da pasta do gravador (dois niveis acima).
        pasta = pasta.parent.parent if pasta.parent.parent != pasta.parent else pasta.parent
    return pasta / f"{nome} Project" / f"{nome}.als"


def _linha_do_set(sessao: SessionIR) -> dict:
    """The set timeline from zero, where the first block starts, to the last clip end."""
    fins = [clip.timeline_end_samples for clip in sessao.all_clips()]
    return {"start_samples": 0, "end_samples": max(fins, default=0)}


def _bloco(bloco: dict, taxa: TimecodeRate) -> dict:
    """One day of the ruler: where it starts in the set and its label."""
    data = bloco.get("date")
    dia = data[5:] if data else str(bloco.get("day") or "")
    tc = bloco.get("timecode")
    return {
        "start_samples": bloco.get("start_samples", 0),
        "label": f"{dia} {SEP} {segundo_mais_proximo(tc, taxa) if tc else 'no TC'}",
    }


def segundo_mais_proximo(tc: str, taxa: TimecodeRate) -> str:
    """hh:mm:ss do timecode arredondado ao segundo mais proximo pelos frames."""
    try:
        horas, minutos, segundos, quadros = (int(parte) for parte in re.split("[:;]", tc))
    except ValueError:
        return tc[:8]
    fps = -(-taxa.num // taxa.den)
    total = horas * 3600 + minutos * 60 + segundos + (1 if 2 * quadros >= fps else 0)
    total %= 24 * 3600
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


# --- os fatos da fonte ---


def _facts(sessao: SessionIR, report: Report) -> list[dict]:
    """The facts of the source, in the order of the sheet."""
    arquivo = _primeiro_arquivo(sessao)
    info = _info(arquivo)
    takes, circulados = _takes(sessao, report)
    blocos = sessao.layout_blocks
    datas = [bloco.get("date") or str(bloco.get("day") or "") for bloco in blocos]
    return [
        _gravador(info),
        app_view.fact("Days", str(len(blocos)), f" {SEP} ".join(datas)),
        app_view.fact("Takes", str(takes), f"{circulados} circled"),
        app_view.fact("Inputs", str(len(sessao.tracks)), _faixa(sessao.tracks)),
        _audio(sessao, info, arquivo),
        _timecode(sessao),
    ]


def _primeiro_arquivo(sessao: SessionIR) -> Path | None:
    """The first recorder file of the read, the one the facts describe."""
    for asset in sessao.media:
        if asset.resolved_path:
            return Path(asset.resolved_path)
    return None


def _info(arquivo: Path | None) -> BwfInfo | None:
    """The metadata of the recorder file; None when it cannot be read again."""
    if arquivo is None:
        return None
    try:
        return read_bwf(arquivo)
    except (ValueError, OSError):
        return None


def _takes(sessao: SessionIR, report: Report) -> tuple[int, int]:
    """The takes of the folder and how many are circled, as the reader counted them."""
    for issue in report.issues:
        if issue.code == "PRODUCTION_TAKES":
            return int(issue.context.get("takes", 0)), int(issue.context.get("circled", 0))
    return sum(int(bloco.get("takes") or 0) for bloco in sessao.layout_blocks), 0


# Prefixos de fabricante que o bext abrevia no campo originator.
FABRICANTES = {"SoundDev": "Sound Devices"}


def nome_do_gravador(originator: str) -> str:
    """Nome legivel do gravador: fabricante por extenso e sem o numero de serie.

    O MixPre grava "SoundDev: MixPre-6 II TH13221523"; o ultimo termo com
    letras e digitos e o serial e sai do nome.
    """
    texto = originator.strip()
    if not texto:
        return "Unknown"
    marca, _, modelo = texto.partition(":")
    if not modelo:
        return texto
    termos = modelo.split()
    if (
        len(termos) > 1
        and any(c.isdigit() for c in termos[-1])
        and any(c.isalpha() for c in termos[-1])
        and len(termos[-1]) >= 6
    ):
        termos = termos[:-1]
    return " ".join([FABRICANTES.get(marca.strip(), marca.strip()), *termos])


def _gravador(info: BwfInfo | None) -> dict:
    """The Recorder fact: the bext originator and the metadata the file carries."""
    bext = (info.bext if info else None) or {}
    nome = nome_do_gravador(str(bext.get("originator") or ""))
    if info is not None and info.bext and info.ixml is not None:
        sub = "BWF + iXML"
    elif info is not None and info.bext:
        sub = "BWF"
    else:
        sub = "no metadata"
    return app_view.fact("Recorder", nome, sub)


def _faixa(tracks: list[TrackIR]) -> str:
    """The range of the inputs: "In 3 to In 7", or the one input."""
    nomes = [track.name.split(f" {SEP} ")[0] for track in tracks]
    if not nomes:
        return ""
    if len(nomes) == 1:
        return nomes[0]
    return f"{nomes[0]} to {nomes[-1]}"


def _audio(sessao: SessionIR, info: BwfInfo | None, arquivo: Path | None) -> dict:
    """The Audio fact: the sample rate, with the bit depth and the format."""
    khz = f"{sessao.sample_rate / 1000:g} kHz"
    if info is None or arquivo is None:
        return app_view.fact("Audio", khz)
    formato = "float" if _e_float(arquivo) else "PCM"
    return app_view.fact("Audio", khz, f"{info.bits}-bit {formato}")


def _e_float(arquivo: Path) -> bool:
    """True when the fmt of the file says float samples."""
    try:
        fmt = read_audio_metadata(arquivo)["fmt_payload"] or b""
    except (ValueError, OSError):
        return False
    if len(fmt) < 2:
        return False
    codigo = struct.unpack("<H", fmt[:2])[0]
    if codigo == FORMATO_EXTENSIVEL and len(fmt) >= 26:
        codigo = struct.unpack("<H", fmt[24:26])[0]
    return codigo == FORMATO_FLOAT


def _timecode(sessao: SessionIR) -> dict:
    """The Timecode fact: the rate and the drop flag, or none without timecode."""
    if all(bloco.get("timecode_samples") is None for bloco in sessao.layout_blocks):
        return app_view.fact("Timecode", "none")
    taxa = sessao.timecode_rate
    rotulo = app_view.rate_label(taxa).removesuffix(" DF")
    return app_view.fact("Timecode", rotulo, "drop" if taxa.drop_frame else "non-drop")


# --- as tracks ---


def _producao(clip) -> dict:
    return clip.raw.get("production") or {}


def _track(indice: int, track: TrackIR, sessao: SessionIR) -> dict:
    """One row of the sheet: the takes, the lanes of the circled and the clips."""
    simples = ENTRADA_SIMPLES.fullmatch(track.name) is not None
    estado = {"st": "ok", "label": "Mic varies by take" if simples else "Ready"}
    linha = app_view.track_row(indice, track, sessao, estado, [])
    circulados = [clip for clip in track.clips if _producao(clip).get("circled")]
    outros = [clip for clip in track.clips if not _producao(clip).get("circled")]
    linha["takes"] = len({_producao(clip).get("source") for clip in track.clips})
    linha["spans"] = app_view.spans(dataclasses.replace(track, clips=outros))
    linha["circled_spans"] = app_view.spans(dataclasses.replace(track, clips=circulados))
    linhas = [_clip(clip, sessao, simples) for clip in track.clips[:LIMITE_CLIPS]]
    linha["clip_rows"] = linhas
    linha["clips_more"] = max(len(track.clips) - len(linhas), 0)
    return linha


def _clip(clip, sessao: SessionIR, simples: bool) -> dict:
    """One row of the clip table: name, mic, day, real timecode and circled."""
    producao = _producao(clip)
    nome, mic = clip.name, ""
    marca = f" {SEP} "
    if simples and marca in nome:
        nome, mic = nome.rsplit(marca, 1)
        mic = f"{SEP} {mic}"
    tc = clip.raw.get("timecode_samples")
    entra = sai = app_view.EN_DASH
    if tc is not None:
        entra = app_view.tc(tc, sessao)
        sai = app_view.tc(tc + clip.duration_samples, sessao)
    return {
        "name": nome,
        "mic": mic,
        "day": str(producao.get("day") or ""),
        "tc_in": entra,
        "tc_out": sai,
        "circled": "circled" if producao.get("circled") else "",
    }


# --- a conversao ---


def convert(payload: dict) -> dict:
    """Converts the recorder folder into a Live set and verifies it."""
    entrada, saida = app_convert.caminhos(payload)
    if not entrada or not saida:
        return error("bad_request", "The recorder folder and the destination are required.")
    destino = Path(saida).resolve()
    options = payload.get("options") or {}
    try:
        bpm = app_convert._bpm(options.get("bpm"))
    except ValueError as exc:
        return error("bad_request", str(exc))
    problema = app_convert._problema_do_request(payload, entrada, destino)
    if problema is None:
        problema = _problema_da_fonte(entrada, destino)
    if problema is not None:
        return problema
    pedido = ImportOptions(
        input=entrada,
        output=str(destino),
        template=app_convert._template(app_convert._template_nomeado(payload)),
        bpm=bpm,
    )
    return _converte(pedido, options.get("circled_only") is True, destino)


def _problema_da_fonte(entrada: str, destino: Path) -> dict | None:
    """The error of a source that is not production audio, or of a set inside it."""
    fonte = Path(entrada)
    if not is_production_input(fonte):
        return error("unsupported", "Production audio is a folder or a .wav file.")
    try:
        check_production_output(fonte, destino)
    except ValueError as exc:
        return error("bad_request", str(exc))
    return None


def _converte(pedido: ImportOptions, circulados: bool, destino: Path) -> dict:
    """Runs the import of production audio and answers with what it was worth."""
    report = Report(strict=True)
    antes = app_convert.estado_do_destino(destino)
    try:
        import_session(pedido, report, circled_only=circulados)
    except StrictModeError as exc:
        app_convert.registra_falha(DIRECTION, report)
        parado = app_convert.veredito_parado(str(exc))
        return app_convert.resposta(False, destino, parado, False, report)
    except Exception as exc:  # noqa: BLE001 - the message of the engine is the answer
        app_convert.registra_falha(DIRECTION, report)
        fonte = Path(pedido.input)
        return error("failed", falha_da_conversao(exc, destino, report, fonte))
    if not app_convert.registra(DIRECTION, destino, report, antes):
        nao_escrito = app_convert.veredito_nao_escrito(report)
        return app_convert.resposta(False, destino, nao_escrito, False, report)
    medido = verdict(report, destino, pedido.input)
    return app_convert.resposta(True, destino, medido, False, report)


def verdict(report: Report, als: Path, source) -> dict:
    """The verdict of the set: what the verify of production audio measured."""
    try:
        resultado = verify_production(source, als)
    except Exception as exc:  # noqa: BLE001 - a verification that cannot run is said
        detalhe = ["The set was written.", falha_do_motor(exc, als, "read")]
        return {"state": "warn", "word": "Not verified", "detail": detalhe}
    linhas = [
        f"{resultado.compared} of {resultado.expected} audio clips compared",
        f"largest deviation {resultado.max_delta_samples} samples",
    ]
    if not resultado.ok:
        return {"state": "fail", "word": "Differs", "detail": linhas + _diferencas(resultado)}
    linhas.append("every channel equal to the recorder file")
    erros = report.count("error")
    if erros:
        perdas = [f"{app_view.plural(erros, 'error', 'errors')} reported", *linhas]
        return {"state": "warn", "word": "Written with losses", "detail": perdas}
    return {"state": "ok", "word": "Verified", "detail": linhas}


def _diferencas(resultado) -> list[str]:
    """The lines of a verify that did not pass."""
    linhas = [
        f"samples equal to the recorder file in {resultado.samples_equal} of {resultado.compared}"
    ]
    if resultado.missing or resultado.extra:
        linhas.append(f"{resultado.missing} missing, {resultado.extra} extra")
    if resultado.errors:
        linhas.append(resultado.errors[0])
    return linhas
