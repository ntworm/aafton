"""Suporte do Studio para sets do Live sem registro do aafton (.aafton.json)."""

from __future__ import annotations

from pathlib import Path

from ..als_ancora import INICIO_PADRAO, _locator_tc
from ..als_preflight import ScannedTrack
from ..als_render import MISTA
from ..als_xml import load_als
from ..ir import SessionIR, TrackIR
from ..report import ConversionIssue, Report
from ..sidecar import Anchor
from . import app_view

# Origem do set exibida nos fatos da folha.
MADE_IN_AAFTON = "aafton"
MADE_IN_LIVE = "Live (no aafton record)"

# Estado e motivo da track que carrega apenas clips de video.
VIDEO_STATE = f"video {app_view.MIDDLE_DOT} not in the AAF"
VIDEO_REASON = "Video clips are not included in the AAF."

# Orientacao exibida no detalhe da track que precisa de render no Live.
CONSELHO_RENDER = (
    "In Live, select them and Consolidate (Ctrl+J), or freeze and flatten the track, "
    "save the set, then read it again."
)


def made_in(anchor: Anchor | None) -> str:
    """Origem do set para o fato Made in."""
    return MADE_IN_AAFTON if anchor is not None else MADE_IN_LIVE


def start_do_set(caminho: Path, anchor: Anchor | None) -> dict | None:
    """Campos iniciais de Start timecode e Frame rate quando falta o registro."""
    if anchor is not None:
        return None
    root = load_als(caminho)
    locator = _locator_tc(root)
    start_tc = locator[1] if locator is not None else INICIO_PADRAO
    return {"start_tc": start_tc, "frame_rate": "24", "from_locator": locator is not None}


def cabecalho(caminho: Path, anchor: Anchor | None, session: SessionIR, pode_deixar: bool) -> dict:
    """Campos de origem, ancora inicial e taxa para a resposta do read."""
    return {
        "made_in": made_in(anchor),
        "start": start_do_set(caminho, anchor),
        "can_leave_out": pode_deixar,
        "sample_rate": session.sample_rate,
        "origin_samples": session.start_timecode_samples,
    }


def render_por_track(report: Report) -> dict[str, list[dict]]:
    """Mapa ordenado por nome das tracks citadas em ALS_RENDER_IN_LIVE."""
    por_nome: dict[str, list[dict]] = {}
    for issue in report.issues:
        if issue.code != "ALS_RENDER_IN_LIVE":
            continue
        for entry in issue.context.get("tracks") or []:
            nome = str(entry.get("track_name") or "")
            por_nome.setdefault(nome, []).append(entry)
    return por_nome


def proximo_render(item: ScannedTrack, render_map: dict[str, list[dict]]) -> dict | None:
    """Consome a entrada de render que casa com a track de audio escaneada."""
    if item.kind != "audio" or item.audio_index is None:
        return None
    fila = render_map.get(item.name)
    if not fila:
        return None
    track_id = f"t{item.audio_index}"
    for pos, entry in enumerate(fila):
        entry_id = entry.get("track_id")
        if entry_id == track_id or entry_id is None:
            return fila.pop(pos)
    return None


def _causas_contadas(clips: list[dict]) -> dict[str, int]:
    """Conta quantos clips da track carregam cada causa de render."""
    contagem: dict[str, int] = {}
    for item in clips:
        for causa in item.get("causes") or []:
            contagem[causa] = contagem.get(causa, 0) + 1
    return contagem


def _rotulo_render(counts: dict[str, int], mixed: bool) -> str:
    """Rotulo curto de prontidao na linha da track que pede render."""
    partes = [f"{causa} {n}" for causa, n in counts.items()]
    if mixed:
        partes.append("mono+stereo")
    resumo = ", ".join(partes) if partes else "render"
    return f"render first {app_view.MIDDLE_DOT} {resumo}"


def _partes_causa(counts: dict[str, int]) -> list[str]:
    """Frases de contagem de clips por causa de render."""
    return [f"{app_view.plural(n, 'clip', 'clips')} with {causa}" for causa, n in counts.items()]


def _motivos_render(entry: dict) -> list[str]:
    """Frases de motivo para o painel expansivel da track."""
    clips = entry.get("clips") or []
    linhas = _partes_causa(_causas_contadas(clips))
    if entry.get("mixed_channels"):
        linhas.append(MISTA)
    if clips:
        linhas.append(CONSELHO_RENDER)
    return linhas


def _juntar_motivos(primarios: list[str], extras: list[str]) -> list[str]:
    """Junta motivos preservando a ordem e sem repetir linhas identicas."""
    saida = list(primarios)
    for linha in extras:
        if linha not in saida:
            saida.append(linha)
    return saida


def texto_render_guia(nome: str, entry: dict) -> str:
    """Linha da caixa Render in Live first montada do contexto estruturado."""
    partes = _partes_causa(_causas_contadas(entry.get("clips") or []))
    if entry.get("mixed_channels"):
        partes.append(MISTA)
    return f"{nome}: {', '.join(partes)}"


def aplicar_na_linha(
    linha: dict, track: TrackIR | None, render_info: dict | None, video_clips: int = 0
) -> None:
    """Ajusta uma linha da planilha para video ou clips que pedem render."""
    linha["render_clips"] = 0
    linha["mixed_channels"] = False
    if track is not None and track.is_video:
        linha["kind"] = "video"
        linha["state"] = {"st": "off", "label": VIDEO_STATE}
        linha["pick"] = None
        linha["reasons"] = [VIDEO_REASON]
        return
    if video_clips:
        linha["clips"] = max(0, int(linha.get("clips") or 0) - video_clips)
    if render_info is None:
        return
    clips = render_info.get("clips") or []
    mixed = bool(render_info.get("mixed_channels"))
    counts = _causas_contadas(clips)
    linha["state"] = {"st": "err", "label": _rotulo_render(counts, mixed)}
    linha["pick"] = None
    linha["reasons"] = _juntar_motivos(_motivos_render(render_info), linha["reasons"])
    linha["render_clips"] = len(clips)
    linha["mixed_channels"] = mixed


def _audio_track(item: ScannedTrack, por_id: dict[str, TrackIR]) -> TrackIR | None:
    """Track da sessao para um item escaneado de audio."""
    if item.kind != "audio" or item.audio_index is None:
        return None
    return por_id.get(f"t{item.audio_index}")


def _videos_da_track(
    item: ScannedTrack,
    agrupadas: tuple[dict[str, list[ConversionIssue]], dict[str, list[ConversionIssue]]],
) -> int:
    """Quantos clips de video ficaram de fora nesta track de audio."""
    return sum(1 for i in issues_da_track(item, agrupadas) if i.code == "ALS_VIDEO_LEFT_OUT")


def aplicar_no_lote(
    scanned: list[ScannedTrack],
    linhas: list[dict],
    por_id: dict[str, TrackIR],
    report: Report,
    anchor: Anchor | None,
    midi_nomes: list[str],
) -> tuple[list[str], bool]:
    """Completa as linhas para video/render sem sidecar e monta a caixa do guia."""
    render_map = render_por_track(report) if anchor is None else {}
    agrupadas = agrupar_issues(report.issues)
    render_itens: list[tuple[str, dict]] = []
    for item, linha in zip(scanned, linhas):
        track = _audio_track(item, por_id)
        render_info = proximo_render(item, render_map) if anchor is None else None
        video_clips = _videos_da_track(item, agrupadas)
        aplicar_na_linha(linha, track, render_info, video_clips=video_clips)
        if render_info is not None:
            render_itens.append((linha["name"], render_info))
    return guia_completo(render_itens, midi_nomes)


def guia_completo(
    render_itens: list[tuple[str, dict]], midi_nomes: list[str]
) -> tuple[list[str], bool]:
    """Lista da caixa Render in Live first e flag que libera Leave them out."""
    linhas = [texto_render_guia(nome, entry) for nome, entry in render_itens]
    linhas.extend(midi_nomes)
    pode_deixar_fora = any(bool(entry.get("clips")) for _, entry in render_itens)
    return linhas, pode_deixar_fora


def agrupar_issues(
    issues: list[ConversionIssue],
) -> tuple[dict[str, list[ConversionIssue]], dict[str, list[ConversionIssue]]]:
    """Agrupa issues por track_id quando ha um, e por nome sem kind como fallback."""
    por_id: dict[str, list[ConversionIssue]] = {}
    por_nome: dict[str, list[ConversionIssue]] = {}
    for issue in issues:
        track_id = issue.context.get("track_id")
        if track_id is not None:
            por_id.setdefault(str(track_id), []).append(issue)
            continue
        if "kind" in issue.context:
            continue
        nome = issue.context.get("track")
        if nome is not None:
            por_nome.setdefault(str(nome), []).append(issue)
    return por_id, por_nome


def issues_da_track(
    item: ScannedTrack,
    agrupadas: tuple[dict[str, list[ConversionIssue]], dict[str, list[ConversionIssue]]],
) -> list[ConversionIssue]:
    """Devolve as issues de leitura que pertencem a uma linha do scan."""
    if item.kind != "audio":
        return []
    por_id, por_nome = agrupadas
    if item.audio_index is not None:
        return list(por_id.get(f"t{item.audio_index}", [])) + list(por_nome.get(item.name, []))
    return list(por_nome.get(item.name, []))


def modo_da_midia(opcoes: dict) -> str:
    """The mode of the media of the export: link or embed."""
    modo = str(opcoes.get("media") or "link").strip().lower()
    return modo if modo in ("link", "embed") else "link"
