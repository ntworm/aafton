"""Aba Collect media do Studio e suporte de coleta no export das duas abas."""

from __future__ import annotations

from pathlib import Path

from ..als_xml import load_als
from ..collect import (
    CollectError,
    CollectPlan,
    CollectReport,
    collect_into_output,
    plan_collect,
    run_collect,
)
from ..collect_als import find_project_dir
from ..collect_copy import assign_dest_names
from ..media_extract import read_audio_metadata
from ..report import ConversionIssue, Report
from . import app_convert, app_read, app_view
from .app_messages import falha_do_motor, mensagem_de_arquivo
from .app_read import error

DIRECTION = "collect"


def read(payload: dict) -> dict:
    """Inspeciona o .als ou .aaf de origem e devolve fatos, tabela de midia e notas."""
    caminho, erro = _validar_origem(str(payload.get("input") or "").strip())
    if erro is not None:
        return erro
    try:
        plan = plan_collect(caminho)
    except CollectError as exc:
        return error("unreadable", str(exc))
    except Exception as exc:  # noqa: BLE001
        return error("unreadable", falha_do_motor(exc, caminho, "read"))

    sub_sessao = _sub_da_sessao(plan)
    linhas, renomeados = _linhas_da_midia(plan)
    fatos = _fatos(plan, sub_sessao, include_video=True)
    notas = _notas_do_plano(plan)
    sugestao = str(plan.source_path.parent / f"{plan.source_path.stem} collected")
    return {
        "ok": True,
        "direction": DIRECTION,
        "source": {
            "path": str(plan.source_path),
            "name": plan.source_path.stem,
            "format": plan.source_format,
            "session_sub": sub_sessao,
        },
        "facts": fatos,
        "items": linhas,
        "renamed": renomeados,
        "notes": notas,
        "suggest": {"output": sugestao},
    }


def run(payload: dict) -> dict:
    """Executa a coleta para a pasta de destino e grava LAST['collect']."""
    origem = str(payload.get("input") or "").strip()
    destino_str = str(payload.get("output") or "").strip()
    if not origem or not destino_str:
        return error("bad_request", "The source and the destination folder are required.")
    caminho, erro = _validar_origem(origem)
    if erro is not None:
        return erro
    destino = Path(destino_str).resolve()
    opcoes = payload.get("options") or {}
    include_video = opcoes.get("include_video") is not False
    nome = str(opcoes.get("name") or "").strip() or None
    res, falha = _coleta(caminho, destino, nome, include_video)
    if falha is not None:
        return falha

    formato = caminho.suffix.lower().lstrip(".")
    app_convert.LAST[DIRECTION] = {"output": res.session_path, "report": res.report}
    verdict = _veredito_collect(res.report, plan_format=formato)
    notas = _notas_do_relatorio(res.report)
    return {
        "ok": True,
        "written": True,
        "output": str(res.session_path),
        "media_dir": str(res.media_dir),
        "format": formato,
        "verdict": verdict,
        "notes": notas,
    }


def _coleta(caminho: Path, destino: Path, nome: str | None, include_video: bool):
    """Roda o collect; numa falha limpa LAST['collect'] e devolve a resposta de erro."""
    try:
        return run_collect(caminho, destino, name=nome, include_video=include_video), None
    except CollectError as exc:
        app_convert.LAST.pop(DIRECTION, None)
        return None, error("failed", str(exc))
    except Exception as exc:  # noqa: BLE001 - a mensagem do motor e a resposta
        app_convert.LAST.pop(DIRECTION, None)
        return None, error("failed", falha_do_motor(exc, destino, "write"))


def _validar_origem(origem: str) -> tuple[Path, dict | None]:
    """Valida se o caminho de origem existe e e .als ou .aaf."""
    if not origem:
        return Path(), error("bad_request", "The source path is required.")
    caminho = Path(origem)
    if not caminho.exists() or not caminho.is_file():
        return caminho, error(
            "not_found", mensagem_de_arquivo(FileNotFoundError(), caminho, "read")
        )
    sufixo = caminho.suffix.lower()
    if sufixo not in {".als", ".aaf"}:
        return caminho, error(
            "unsupported", f"The format {sufixo or '<none>'} is not a Live set or AAF."
        )
    return caminho, None


def format_size(size_bytes: int) -> str:
    """Formata tamanho em bytes como KB, MB ou GB legivel."""
    if size_bytes <= 0:
        return "0 MB"
    gb = 1024 * 1024 * 1024
    mb = 1024 * 1024
    kb = 1024
    if size_bytes >= gb:
        return f"{size_bytes / gb:.2f} GB"
    if size_bytes >= 100 * kb:
        return f"{size_bytes / mb:.1f} MB"
    return f"{max(1, round(size_bytes / kb))} KB"


def _sub_da_sessao(plan: CollectPlan) -> str | None:
    """Sublinha do fato Session: Creator do Live ou programa do AAF."""
    if plan.source_format == "als":
        try:
            creator = (load_als(plan.source_path).get("Creator") or "").strip()
            return creator or None
        except Exception:  # noqa: BLE001
            return None
    programa, versao, _ = app_read._cabecalho(plan.source_path)
    if versao:
        return f"{programa} {versao}"
    return programa if programa != "AAF" else "Linked media"


def _sub_dos_arquivos(plan: CollectPlan) -> tuple[str, str | None]:
    """Sublinha e estado do fato Files."""
    missing = sum(1 for it in plan.items if it.state == "missing")
    library = sum(1 for it in plan.items if it.state == "library")
    embedded = sum(1 for it in plan.items if it.state == "embedded")
    partes: list[str] = []
    if missing:
        partes.append(f"{missing} missing")
    if library:
        partes.append(f"{library} in Live's library")
    if embedded:
        partes.append(f"{embedded} embedded")
    sub = ", ".join(partes) if partes else "all found"
    return sub, ("err" if missing else None)


def _fatos(plan: CollectPlan, sub_sessao: str | None, *, include_video: bool) -> list[dict]:
    """Os tres fatos da aba Collect media: Session, Files e To copy."""
    rotulo_sessao = "Live set" if plan.source_format == "als" else "AAF"
    sub_files, st_files = _sub_dos_arquivos(plan)
    copiaveis = [
        it for it in plan.items if it.state == "ready" and (include_video or it.kind != "video")
    ]
    bytes_copia = sum(it.size_bytes for it in copiaveis)
    videos = [it for it in plan.items if it.state == "ready" and it.kind == "video"]
    if videos:
        bytes_video = sum(it.size_bytes for it in videos)
        sub_copy = f"picture {format_size(bytes_video)}" if include_video else "picture left out"
    else:
        sub_copy = f"{len(copiaveis)} to copy"

    return [
        app_view.fact("Session", rotulo_sessao, sub_sessao),
        app_view.fact("Files", str(len(plan.items)), sub_files, st_files),
        app_view.fact("To copy", format_size(bytes_copia), sub_copy),
    ]


def _rotulo_kind(item) -> str:
    """Rotulo da coluna Kind: Picture, Mono, Stereo, N ch ou Audio."""
    if item.kind == "video":
        return "Picture"
    if item.state == "ready":
        try:
            meta = read_audio_metadata(Path(item.source))
            ch = int(meta.get("channels") or 0)
            if ch == 1:
                return "Mono"
            if ch == 2:
                return "Stereo"
            if ch > 2:
                return f"{ch} ch"
        except Exception:  # noqa: BLE001
            pass
    return "Audio"


def _rotulo_pasta(item, base_dir: Path) -> str:
    """Rotulo da coluna Folder."""
    if item.state == "library":
        return "Core Library"
    if item.state == "embedded":
        return "Inside the AAF"
    try:
        parent = Path(item.source).parent
    except (OSError, ValueError):
        return ""
    try:
        rel = parent.resolve().relative_to(base_dir.resolve())
        texto = rel.as_posix()
        return "." if texto == "." else texto
    except (OSError, ValueError):
        partes = parent.parts
        if len(partes) >= 2:
            return f"{partes[-2]}/{partes[-1]}"
        return parent.name or str(parent)


def _estado_linha(item, dest_name: str | None) -> tuple[dict, bool]:
    """Estado da linha na tabela e se ela foi renomeada por colisao."""
    if item.state == "missing":
        return {"st": "err", "label": "Missing"}, False
    if item.state == "library":
        return {"st": "off", "label": "Stays in Live's library"}, False
    if item.state == "embedded":
        return {"st": "off", "label": "Embedded in the AAF"}, False
    nome_orig = Path(item.source).name
    if dest_name and dest_name != nome_orig:
        return {"st": "warn", "label": "Copy, renamed"}, True
    return {"st": "ok", "label": "Copy"}, False


def _linhas_da_midia(plan: CollectPlan) -> tuple[list[dict], list[dict]]:
    """Monta as linhas da tabela 02 Media e a lista de arquivos renomeados."""
    prontos = [it for it in plan.items if it.state == "ready"]
    mapa_nomes = assign_dest_names([it.source for it in prontos])
    base_dir = (
        find_project_dir(plan.source_path.parent)
        if plan.source_format == "als"
        else plan.source_path.parent
    )
    linhas: list[dict] = []
    renomeados: list[dict] = []
    for idx, item in enumerate(plan.items):
        nome_arq = Path(item.source).name or item.source
        dest_name = mapa_nomes.get(item.resolved_key)
        estado, foi_renomeado = _estado_linha(item, dest_name)
        if foi_renomeado and dest_name:
            renomeados.append({"original": nome_arq, "renamed": dest_name})
        linhas.append(
            {
                "index": idx,
                "name": nome_arq,
                "kind": _rotulo_kind(item),
                "item_kind": item.kind,
                "size": format_size(item.size_bytes) if item.size_bytes > 0 else app_view.EN_DASH,
                "size_bytes": item.size_bytes,
                "folder": _rotulo_pasta(item, base_dir),
                "state": estado,
                "raw_state": item.state,
                "renamed_to": dest_name if foi_renomeado else None,
            }
        )
    return linhas, renomeados


def _notas_do_plano(plan: CollectPlan) -> dict:
    """Notas de aviso quando faltam arquivos na leitura."""
    issues: list[ConversionIssue] = []
    for item in plan.items:
        if item.state == "missing":
            nome = Path(item.source).name or item.source
            issues.append(
                ConversionIssue(
                    "warning",
                    "COLLECT_MEDIA_MISSING",
                    f"{nome} was not found. Its clip keeps pointing to the original place.",
                    {"path": item.source},
                )
            )
    return app_view.notes(issues)


def _notas_do_relatorio(report: CollectReport) -> dict:
    """Notas do relatorio apos a coleta."""
    issues: list[ConversionIssue] = []
    for item in report.items:
        if item.state == "missing":
            nome = Path(item.source).name or item.source
            issues.append(
                ConversionIssue(
                    "warning",
                    "COLLECT_MEDIA_MISSING",
                    f"{nome} was not found. Its clip keeps pointing to the original place.",
                    {"path": item.source},
                )
            )
    return app_view.notes(issues)


def _extras_do_veredito(report: CollectReport) -> list[str]:
    """Frases adicionais do veredito de coleta (missing, library, embedded, video)."""
    faltando = report.missing_count
    extras: list[str] = []
    if faltando == 1:
        faltantes = [
            Path(it.source).name or it.source for it in report.items if it.state == "missing"
        ]
        extras.append(f"{faltantes[0]} was missing")
    elif faltando > 1:
        extras.append(f"{faltando} files were missing")
    if report.count("library"):
        extras.append("Live's library samples stay where they are")
    if report.count("embedded"):
        extras.append("embedded media stays inside the AAF")
    if report.count("video_skipped"):
        extras.append("picture was left out")
    return extras


def _veredito_collect(report: CollectReport, *, plan_format: str) -> dict:
    """Veredito da aba Collect media depois que a coleta terminou."""
    _ = plan_format
    copiados = report.count("copied") + report.count("already_there")
    faltando = report.missing_count
    frase_copia = (
        f"{app_view.plural(copiados, 'file', 'files')} copied, "
        f"{format_size(report.total_bytes)}, every copy checked against its original."
    )
    extras = _extras_do_veredito(report)
    detalhe = [frase_copia]
    if extras:
        detalhe.append("; ".join(extras) + ".")
    if faltando:
        return {"state": "warn", "word": f"Collected, {faltando} missing", "detail": detalhe}
    return {"state": "ok", "word": "Collected", "detail": detalhe}


def collect_for_aaf_export(
    destino: Path, opcoes: dict, media: str, report: Report
) -> tuple[CollectReport | None, str | None]:
    """Roda a coleta no export Live to AAF somente quando Format=AAF e Media=link."""
    formato = str(opcoes.get("format") or "").strip().lower() or destino.suffix.lower().lstrip(".")
    if not (
        opcoes.get("collect_media") is True
        and destino.suffix.lower() == ".aaf"
        and formato == "aaf"
        and media == "link"
    ):
        return None, None
    return collect_for_export(destino, report)


def collect_for_export(destino: Path, report: Report) -> tuple[CollectReport | None, str | None]:
    """Executa collect_into_output no destino recem-escrito pelo export.

    Devolve ``(collect_report, collect_error_str)``. Em caso de CollectError,
    a saida do export fica como foi escrita e o erro volta como texto.
    """
    try:
        res = collect_into_output(destino)
    except CollectError as exc:
        report.warning("COLLECT_FAILED", str(exc), {"output": str(destino)})
        return None, str(exc)
    except Exception as exc:  # noqa: BLE001
        msg = falha_do_motor(exc, destino, "write")
        report.warning("COLLECT_FAILED", msg, {"output": str(destino)})
        return None, msg

    for item in res.report.items:
        if item.state == "missing":
            nome = Path(item.source).name or item.source
            report.warning(
                "COLLECT_MEDIA_MISSING",
                f"{nome} was not found and could not be collected.",
                {"path": item.source},
            )
    return res.report, None


def append_collect_verdict(verdict: dict, collect_report: CollectReport | None) -> dict:
    """Acrescenta a nota de midia faltando do collect ao detalhe do veredito do export."""
    if collect_report is None:
        return verdict
    faltando = collect_report.missing_count
    if faltando <= 0:
        return verdict
    detalhe = list(verdict.get("detail") or [])
    substantivo = app_view.plural(faltando, "missing media file", "missing media files")
    detalhe.append(f"{substantivo} not collected")
    estado = verdict.get("state")
    novo_estado = "warn" if estado == "ok" else estado
    return {**verdict, "state": novo_estado, "detail": detalhe}


def attach_collect_error(ans: dict, collect_err: str | None) -> dict:
    """Anexa o erro da coleta ao dicionario da resposta do export, se houver."""
    if collect_err:
        ans["collect_error"] = collect_err
    return ans
