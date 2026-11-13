"""Read step of the app page, AAF to Live.

The read never writes a file: it reads the source with a report that does not
raise, looks for the media without extracting anything and gives back the JSON
that the page renders. The Live to AAF direction lives in ``app_set``, which
this module calls when the payload asks for it.
"""

from __future__ import annotations

from pathlib import Path

import aaf2

from ..formats import ADAPTADORES
from ..formats.origem import read_origin
from ..ir import MediaAsset, SessionIR
from ..ltc import parse_ltc_rate
from ..media_extract import AmbiguousMediaError, resolve_asset
from ..pipeline import media_roots
from ..report import Report
from ..session_text import find_session_text
from ..timecode import TimecodeRate
from ..video import find_video_file
from . import app_view

# The name each format shows in the sheet.
FORMAT_NAMES = {
    ".aaf": "AAF",
    ".edl": "EDL",
    ".xml": "FCP 7 XML",
    ".fcpxml": "FCPXML",
}

# The version some programs write when they do not say their version.
VERSAO_DESCONHECIDA = "Unknown version"


def read(payload: dict) -> dict:
    """Answers the read request in the direction the payload names."""
    direction = str(payload.get("direction") or "").strip()
    if direction == "to_live":
        return read_source(payload)
    if direction == "to_aaf":
        # The import is inside the function: app_set imports this module.
        from . import app_set

        return app_set.read_set(payload)
    return error("bad_request", "The direction must be to_live or to_aaf.")


def error(code: str, message: str) -> dict:
    """The answer of a request that could not be answered."""
    return {"ok": False, "error": {"code": code, "message": message}}


def taxa_edl(valor) -> tuple[TimecodeRate | None, str | None]:
    """The frame rate of an EDL, and the message of the error when it is bad."""
    if valor is None or not str(valor).strip():
        return None, None
    try:
        return parse_ltc_rate(str(valor)), None
    except ValueError as exc:
        return None, str(exc)


def read_source(payload: dict) -> dict:
    """Reads the source of the AAF to Live direction and describes it."""
    origem = str(payload.get("input") or "").strip()
    if not origem:
        return error("bad_request", "The source path is required.")
    caminho = Path(origem)
    if not caminho.exists():
        return error("not_found", f"The file {caminho} was not found.")
    sufixo = caminho.suffix.lower()
    if sufixo != ".aaf" and sufixo not in ADAPTADORES:
        return error("unsupported", f"The format {sufixo} is not read by this direction.")
    taxa, problema = taxa_edl(payload.get("edl_rate"))
    if problema:
        return error("bad_request", problema)

    programa, versao, essencias = _cabecalho(caminho)
    report = Report(strict=False)
    pasta_audio = str(payload.get("audio_dir") or "").strip() or None
    raizes = media_roots(origem, pasta_audio)
    try:
        sessao = read_origin(
            caminho,
            report,
            extract_dir=None,
            fades="absorb",
            edl_rate=taxa,
            media_roots=raizes,
            mxf_dir=None,
        )
    except Exception as exc:  # noqa: BLE001 - the message of the engine is the answer
        return error("unreadable", str(exc))

    midia = _midia(sessao, raizes, report, essencias)
    _texto_da_sessao(sessao, caminho, report)
    return {
        "ok": True,
        "direction": "to_live",
        "source": _origem(caminho, sufixo),
        "facts": _facts(sessao, programa, versao, midia),
        "timeline": app_view.timeline(sessao),
        "tracks": _tracks(sessao, report),
        "notes": app_view.notes(report.issues),
        "media": midia,
        "guide": [],
        "suggest": _sugestao(caminho),
    }


def _origem(caminho: Path, sufixo: str) -> dict:
    """The source as the page shows it: the path, the name and the format."""
    return {
        "path": str(caminho.resolve()),
        "name": caminho.stem,
        "format": sufixo.lstrip("."),
    }


def _facts(sessao: SessionIR, programa: str, versao: str | None, midia: dict) -> list[dict]:
    """The facts of the sheet, in the order the spec gives them."""
    linha = app_view.timeline(sessao)
    clips = sum(len(track.clips) for track in sessao.tracks)
    return [
        app_view.fact("Program", programa, versao),
        app_view.fact("Sample rate", app_view.hz_label(sessao.sample_rate)),
        app_view.fact(
            "Timecode",
            app_view.rate_label(sessao.timecode_rate),
            f"starts {app_view.tc(sessao.start_timecode_samples, sessao)}",
        ),
        app_view.fact(
            "Duration",
            app_view.tc(linha["end_samples"] - linha["start_samples"], sessao),
        ),
        app_view.fact(
            "Tracks",
            str(len(sessao.tracks)),
            _sub_das_tracks(clips, sessao),
        ),
        app_view.fact(
            "Media",
            f"{midia['found']} of {midia['total']}",
            _sub_da_midia(midia),
            "err" if midia["missing"] else None,
        ),
    ]


def _sub_das_tracks(clips: int, sessao: SessionIR) -> str:
    """The second line of the Tracks fact: the clips and the markers, when there are any."""
    texto = f"{clips} clips"
    if sessao.markers:
        texto += f" {app_view.MIDDLE_DOT} {len(sessao.markers)} markers"
    return texto


def _texto_da_sessao(sessao: SessionIR, caminho: Path, report: Report) -> None:
    """Applies the Pro Tools session text found next to the source and checks the frames.

    These are the two calls the import pipeline makes, in the same order: the
    markers of the text and the notes of the alignment show at the read.
    """
    from ..aaf_read import check_frame_alignment
    from ..session_text import process_session_text

    process_session_text(sessao, caminho, None, report)
    check_frame_alignment(sessao, report)


def _cabecalho(caminho: Path) -> tuple[str, str | None, set[str]]:
    """The program that wrote the file, its version and the ids of the mobs with essence.

    The AAF is opened once: the identification says who wrote it and the
    SourceMobs that carry essence are the media the conversion extracts.
    """
    sufixo = caminho.suffix.lower()
    if sufixo != ".aaf":
        return FORMAT_NAMES.get(sufixo, sufixo.upper()), None, set()
    try:
        with aaf2.open(str(caminho), "r") as arquivo:
            identificacao = _identificacao(arquivo)
            essencias = _essencias(arquivo)
    except Exception:  # noqa: BLE001 - an AAF without identification shows its format
        return FORMAT_NAMES[".aaf"], None, set()
    if identificacao is None:
        return FORMAT_NAMES[".aaf"], None, essencias
    nome = _texto_da_propriedade(identificacao, "ProductName")
    if not nome:
        return FORMAT_NAMES[".aaf"], None, essencias
    versao = _texto_da_propriedade(identificacao, "ProductVersionString")
    if not versao or versao == VERSAO_DESCONHECIDA:
        return nome, None, essencias
    return nome, versao, essencias


def _identificacao(arquivo) -> object | None:
    """The last identification of the header of the AAF, when it has one."""
    identificacoes = arquivo.header["IdentificationList"].value
    return identificacoes[-1] if identificacoes else None


def _essencias(arquivo) -> set[str]:
    """The ids of the SourceMobs that carry the essence inside the AAF."""
    ids = set()
    for mob in arquivo.content.mobs:
        if type(mob).__name__ != "SourceMob":
            continue
        if getattr(mob, "essence", None) is None:
            continue
        mob_id = str(getattr(mob, "mob_id", "") or "")
        if mob_id:
            ids.add(mob_id.lower())
    return ids


def _texto_da_propriedade(objeto, nome: str) -> str | None:
    """The text of a property of the identification, when it is there."""
    for fonte in (objeto, getattr(objeto, "header", None)):
        if fonte is None:
            continue
        try:
            valor = fonte[nome].value
        except Exception:  # noqa: BLE001 - a property that is not there is not an error
            continue
        texto = str(valor).strip()
        if texto:
            return texto
    atributo = getattr(objeto, nome, None)
    if atributo:
        return str(atributo).strip() or None
    return None


def _tracks(sessao: SessionIR, report: Report) -> list[dict]:
    """The rows of the track sheet, with the state each track has."""
    por_track = app_view.issues_by_track(report.issues)
    linhas = []
    for indice, track in enumerate(sessao.tracks):
        issues = por_track.get(track.name, [])
        if track.is_video:
            estado = {"st": "off", "label": "picture"}
        else:
            estado = app_view.issue_state(issues)
        linhas.append(app_view.track_row(indice, track, sessao, estado, app_view.reasons(issues)))
    return linhas


def _midia(sessao: SessionIR, raizes: list[Path], report: Report, essencias: set[str]) -> dict:
    """The media of the session, looked for without extracting anything."""
    total = len(sessao.media)
    embutida = 0
    faltando = 0
    for asset in sessao.media:
        if _e_embutido(asset, essencias):
            embutida += 1
            continue
        sonda = Report(strict=False)
        _resolver(asset, raizes, sonda)
        _copiar_issues(sonda, report)
        if any(issue.code == "MEDIA_MISSING" for issue in sonda.issues):
            faltando += 1
    texto = ""
    if faltando:
        texto = f"{faltando} of {total} audio files were not found."
    return {
        "total": total,
        "embedded": embutida,
        "found": total - faltando,
        "missing": faltando,
        "missing_text": texto,
    }


def _e_embutido(asset: MediaAsset, essencias: set[str]) -> bool:
    """True when the media of the asset is inside the file, not on the disk.

    The ``embedded_mob_id`` alone does not say: a linked file has the id of
    the SourceMob that describes it too. What tells the embedded media apart
    is the essence that the mob carries inside the AAF.
    """
    if not asset.embedded_mob_id:
        return False
    return asset.embedded_mob_id.lower() in essencias


def _resolver(asset: MediaAsset, raizes: list[Path], sonda: Report) -> None:
    """Looks for one file of the media, in a report of its own."""
    try:
        resolve_asset(asset, raizes, sonda)
    except AmbiguousMediaError as exc:
        sonda.error("MEDIA_AMBIGUOUS", str(exc), {"filename": asset.filename})
    except ValueError as exc:
        sonda.error("MEDIA_FORMAT_UNSUPPORTED", str(exc), {"filename": asset.filename})
    except Exception as exc:  # noqa: BLE001 - the message of the engine is the answer
        sonda.error("MEDIA_RESOLVE_ERROR", str(exc), {"filename": asset.filename})


def _copiar_issues(sonda: Report, report: Report) -> None:
    """Copies the issues of the probe into the read report, without repeats.

    The MXF notice is left out: the probe only asks whether the file is on the
    disk, and the conversion extracts an MXF without trouble.
    """
    vistos = {(issue.code, issue.message) for issue in report.issues}
    for issue in sonda.issues:
        if issue.code == "MEDIA_MXF_UNSUPPORTED":
            continue
        chave = (issue.code, issue.message)
        if chave in vistos:
            continue
        vistos.add(chave)
        report.add(issue.severity, issue.code, issue.message, issue.context)


def _sub_da_midia(midia: dict) -> str:
    """The second line of the Media fact."""
    if midia["missing"]:
        return f"{midia['missing']} missing"
    if midia["embedded"]:
        return "embedded, extracted on convert"
    return "linked, all found"


def _sugestao(caminho: Path) -> dict:
    """The destination of the conversion and the files found beside the source."""
    pasta = caminho.resolve().parent
    projeto = pasta / f"{caminho.stem} Project"
    return {
        "output": str(projeto / f"{caminho.stem}.als"),
        "video": _detectado(find_video_file(caminho, None)),
        "session_text": _detectado(find_session_text(caminho, None)),
    }


def _detectado(resultado) -> str | None:
    """The path of a file the engine found, or None when there is none or many."""
    if isinstance(resultado, list):
        return None
    return str(resultado) if resultado else None
