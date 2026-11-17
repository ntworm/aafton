"""Convert step of the app page, AAF to Live, and the verdict of the result.

The conversion calls the shared pipeline of the CLI: the app does not copy a
single step of it. The verdict says only what was measured, the report of the
engine and the verification of the written set. The Live to AAF direction
lives in ``app_set``, which this module calls when the payload asks for it.
"""

from __future__ import annotations

from pathlib import Path

from ..bundled import default_template
from ..cli_organize import SINAIS, com_compact
from ..pipeline import ImportOptions, import_session
from ..report import Report, StrictModeError
from ..verify import verify_conversion
from . import app_collect, app_expandir, app_view
from .app_messages import falha_da_conversao, falha_do_motor, mensagem_de_arquivo, pasta_impossivel
from .app_read import error, taxa_edl

# The last file written per direction, with the report of that conversion.
LAST: dict[str, dict] = {}

# The sentence of a verdict that says the destination file is not on the disk.
DESTINO_NAO_ESCRITO = "The destination file was not written."


def convert(payload: dict) -> dict:
    """Answers the convert request in the direction the payload names."""
    direction = str(payload.get("direction") or "").strip()
    if direction == "to_live":
        return convert_to_live(payload)
    if direction == "to_aaf":
        # The import is inside the function: app_set imports this module.
        from . import app_set

        return app_set.convert_to_aaf(payload)
    if direction == "production":
        # O import fica dentro da funcao: app_pa importa este modulo.
        from . import app_pa

        return app_pa.convert(payload)
    return error("bad_request", "The direction must be to_live or to_aaf.")


def convert_to_live(payload: dict) -> dict:
    """Converts the source into a Live set and tells what the result was worth."""
    entrada, saida = caminhos(payload)
    if not entrada or not saida:
        return error("bad_request", "The source and the destination are required.")
    destino = Path(saida).resolve()
    try:
        pedido = _opcoes_do_import(payload, entrada, destino)
    except ValueError as exc:
        return error("bad_request", str(exc))
    problema = _problema_do_request(payload, entrada, destino)
    if problema is not None:
        return problema
    opcoes = payload.get("options") or {}
    coletar = opcoes.get("collect_media") is True
    return _converte(pedido, entrada, destino, app_expandir.folder(payload), collect_media=coletar)


def _problema_do_request(payload: dict, entrada: str, destino: Path) -> dict | None:
    """The error of a request that cannot be converted; None when it can."""
    if destino.exists() and not payload.get("overwrite"):
        return error("exists", f"The file {destino} already exists.")
    return problema_dos_arquivos(destino, entrada, _template_nomeado(payload))


def problema_dos_arquivos(destino: Path, *lidos: str) -> dict | None:
    """The error of a destination folder that cannot be made or of a file that is gone.

    A missing destination folder is not an error: the writer makes it.
    """
    impossivel = pasta_impossivel(destino)
    if impossivel is not None:
        return error("bad_request", impossivel)
    # A fonte e o template que sumiram dizem que sumiram, sem o erro do sistema.
    for nomeado in lidos:
        if nomeado and not Path(nomeado).exists():
            return error("not_found", sumiu(nomeado))
    return None


def sumiu(caminho) -> str:
    """The message of a file to read that is no longer where it was."""
    return mensagem_de_arquivo(FileNotFoundError(), caminho, "read")


def _converte(
    pedido: ImportOptions,
    entrada: str,
    destino: Path,
    gravador: str | None,
    *,
    collect_media: bool = False,
) -> dict:
    """Runs the import of the source and answers with what it was worth.

    ``gravador`` is the production audio folder; without it the import is
    called exactly as it always was.
    """
    report = Report(strict=not pedido.permissive)
    antes = estado_do_destino(destino)
    extra = {"production_audio": gravador} if gravador else {}
    try:
        import_session(pedido, report, **extra)
    except StrictModeError as exc:
        LAST["to_live"] = {"output": None, "report": report}
        return resposta(False, destino, veredito_parado(str(exc)), True, report)
    except Exception as exc:  # noqa: BLE001 - the message of the engine is the answer
        registra_falha("to_live", report)
        return error("failed", falha_da_conversao(exc, destino, report, Path(entrada)))

    escrito = registra("to_live", destino, report, antes)
    if not escrito:
        return resposta(False, destino, veredito_nao_escrito(report), False, report)
    collect_rep, collect_err = (
        app_collect.collect_for_export(destino, report) if collect_media else (None, None)
    )
    verdict = app_collect.append_collect_verdict(
        verdict_live(report, destino, Path(entrada)), collect_rep
    )
    return app_collect.attach_collect_error(
        resposta(escrito, destino, verdict, False, report), collect_err
    )


def estado_do_destino(destino: Path) -> tuple[bool, int, int]:
    """Existence, moment of the last change and size of the destination file.

    The moment is the mtime of the file in nanoseconds: a rewrite that keeps
    the size is still told apart from the file that was there before.
    """
    try:
        if not destino.exists():
            return False, 0, 0
        dados = destino.stat()
    except OSError:
        return False, 0, 0
    return True, dados.st_mtime_ns, dados.st_size


def _foi_escrito(destino: Path, antes: tuple[bool, int, int]) -> bool:
    """True when this attempt created or replaced the destination file.

    The file that was already there before the attempt does not count: only
    the file this attempt left on disk does.
    """
    depois = estado_do_destino(destino)
    if not depois[0]:
        return False
    return depois != antes


def registra(direction: str, destino: Path, report: Report, antes) -> bool:
    """Keeps the result of the attempt and tells whether it wrote the file."""
    escrito = _foi_escrito(destino, antes)
    LAST[direction] = {"output": destino if escrito else None, "report": report}
    return escrito


def registra_falha(direction: str, report: Report) -> None:
    """Keeps the report of a failed attempt and offers no file.

    The engine may have left a partial file on the disk before it failed: it
    is not a result, and the open and the reveal must answer nothing.
    """
    LAST[direction] = {"output": None, "report": report}


def veredito_parado(motivo: str) -> dict:
    """The verdict of a conversion that stopped before writing the destination.

    The sentence speaks of the destination file only: the engine may have
    written audio beside it before it stopped.
    """
    return {"state": "fail", "word": "Stopped", "detail": [motivo, DESTINO_NAO_ESCRITO]}


def veredito_nao_escrito(report: Report) -> dict:
    """The verdict of an attempt that left no destination file on the disk.

    The engine returned without an exception, so the only thing measured is
    the file that is not there: the first error of the report, when there is
    one, and the sentence of the destination.
    """
    detalhe = []
    erro = primeiro_erro(report)
    if erro:
        detalhe.append(erro)
    detalhe.append(DESTINO_NAO_ESCRITO)
    return {"state": "fail", "word": "Not written", "detail": detalhe}


def primeiro_erro(report: Report) -> str:
    """The first error of the report, as the engine writes it: CODE: message."""
    for issue in report.issues:
        if issue.severity == "error":
            return f"{issue.code}: {issue.message}"
    return ""


def caminhos(payload: dict) -> tuple[str, str]:
    """The source and the destination of the request, as text."""
    return (
        str(payload.get("input") or "").strip(),
        str(payload.get("output") or "").strip(),
    )


def _primeiro(options: dict, *nomes: str):
    """O primeiro valor presente entre os nomes, ou None."""
    return next((options[n] for n in nomes if options.get(n) is not None), None)


def _opcoes_de_organizacao(options: dict) -> dict:
    """Campos de organizacao do ImportOptions; groups prefix vale organize prefix."""
    organize = str(options.get("organize") or "").strip() or "off"
    if options.get("groups") == "prefix":
        organize = "prefix"
    sinais = _primeiro(options, "signals", "organize_signals")
    edicoes = _primeiro(options, "edits", "organize_edits")
    return {
        "groups": "prefix" if organize == "prefix" else "none",
        "organize": organize,
        "certainty": str(options.get("certainty") or "permissive"),
        "organize_signals": com_compact(sinais) if sinais is not None else SINAIS,
        "organize_edits": edicoes if isinstance(edicoes, dict) else None,
    }


def _opcoes_do_import(payload: dict, entrada: str, destino: Path) -> ImportOptions:
    """The options of the import, from the payload of the request.

    Raises ValueError when the request carries a value the engine cannot use.
    """
    options = payload.get("options") or {}
    bpm = _bpm(options.get("bpm"))
    taxa, problema = taxa_edl(payload.get("edl_rate"))
    if problema:
        raise ValueError(problema)
    organizacao = _opcoes_de_organizacao(options)
    return ImportOptions(
        input=entrada,
        output=str(destino),
        template=_template(_template_nomeado(payload)),
        live_origin=_texto(options.get("live_origin")),
        bpm=bpm,
        stereo=str(options.get("stereo") or "pair"),
        fades=str(options.get("fades") or "absorb"),
        groups=organizacao.pop("groups"),
        audio_dir=_texto(payload.get("audio_dir")),
        video=_texto(options.get("video")),
        video_start=_texto(options.get("video_start")),
        video_no_burn=True,
        session_text=_texto(options.get("session_text")),
        permissive=str(options.get("mode") or "strict").lower() == "permissive",
        ltc=options.get("ltc") is True,
        edl_rate=taxa,
        **organizacao,
    )


def verdict_live(report: Report, als: Path, source: Path) -> dict:
    """The verdict of an AAF to Live conversion: what the report and the verify say."""
    resultado = None
    motivo = ""
    try:
        resultado = verify_conversion(source, als)
    except Exception as exc:  # noqa: BLE001 - a verification that cannot run is said
        motivo = falha_do_motor(exc, als, "read")

    erros = report.count("error")
    if erros:
        detalhe = [f"{app_view.plural(erros, 'error', 'errors')} reported"]
        if resultado is None:
            detalhe.append("The verification could not run.")
        else:
            detalhe.extend(verify_lines(resultado))
        return {"state": "warn", "word": "Written with losses", "detail": detalhe}
    if resultado is None:
        return {
            "state": "warn",
            "word": "Not verified",
            "detail": ["The set was written.", motivo],
        }
    if not resultado.ok:
        return {"state": "fail", "word": "Differs", "detail": detalhe_da_diferenca(resultado)}
    return {"state": "ok", "word": "Positions verified", "detail": verify_lines(resultado)}


def verify_lines(result) -> list[str]:
    """The lines of the verification, as the spec writes them."""
    linhas = [
        f"{result.audio_compared} of {result.audio_expected} audio clips compared",
        f"largest deviation {result.max_delta_samples} samples",
    ]
    if result.tolerance_samples:
        linhas.append(f"tolerance {result.tolerance_samples} samples (frame-exact source)")
    # As irmas da expansao de canais, conferidas a parte do AAF.
    linhas.extend(app_expandir.verdict_lines(result))
    return linhas


def detalhe_da_diferenca(resultado) -> list[str]:
    """The lines of a verification that did not pass."""
    detalhe = verify_lines(resultado)
    if resultado.audio_missing or resultado.audio_extra:
        detalhe.append(f"{resultado.audio_missing} missing, {resultado.audio_extra} extra")
    # A falha das irmas ja pode estar no detalhe: nao se repete.
    if resultado.errors and resultado.errors[0] not in detalhe:
        detalhe.append(resultado.errors[0])
    return detalhe


def resposta(written: bool, destino: Path, verdict: dict, can_force: bool, report: Report) -> dict:
    """The answer of a conversion: what was written and what it was worth."""
    return {
        "ok": True,
        "written": written,
        "output": str(destino),
        "verdict": verdict,
        "can_force": can_force,
        "notes": app_view.notes(report.issues),
    }


def _bpm(valor) -> float:
    """The tempo of the import, as a number."""
    if valor is None or not str(valor).strip():
        return 60.0
    try:
        return float(valor)
    except (TypeError, ValueError):
        raise ValueError(f"The tempo {valor!r} is not a number.") from None


def _template(valor) -> str:
    """The template of the import: the one of the payload or the default one."""
    texto = str(valor or "").strip()
    return texto if texto else default_template()


def _template_nomeado(payload: dict) -> str:
    """The template the request names, empty when the request names none."""
    options = payload.get("options") or {}
    return str(options.get("template") or "").strip()


def _texto(valor) -> str | None:
    """The text of an option, or None when it is empty."""
    if valor is None:
        return None
    texto = str(valor).strip()
    return texto or None
