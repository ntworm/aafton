"""Expansao de canais na ida do AAF: as entradas que o editor deixou de fora.

Por take (um original do gravador), no AAF inteiro: as entradas do original
menos as entradas que algum clip daquele take usa, em qualquer track. A track
dona do take e a primeira track do editor, na ordem do AAF, com um clip dele.
As irmas sao da track dona, uma por entrada faltando (``DONA . In N``, mais o
microfone quando a entrada tem um nome so em todos os takes que alimentam a
irma). Todo clip do take gera na irma um clip com a mesma posicao, offset,
duracao, fades, ganho e mute; quando dois se sobrepoem na mesma irma, fica o
primeiro pela ordem do AAF e o trecho sobreposto do seguinte sai, com nota.
A irma copia da dona o mute, o ganho, o pan e as automacoes, e a dona e as
irmas vao para um grupo do Live com o nome da dona.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path

from .audio_split import _digest, split_channels
from .expandir_casamento import NOTA_DUPLICADO, Casamento, Notas, Original, casar, indexar
from .ir import EXPANDED_MARK, ClipIR, FadeIR, MediaAsset, SessionIR, TrackIR
from .media_extract import resolve_asset
from .report import Report
from .som_direto_tracks import SEPARADOR

NOTA_SOBREPOSTO = (
    "a sibling clip overlapped an earlier clip on the same sibling track; "
    "the overlapping part was left out"
)


@dataclass
class Uso:
    """Um clip do editor casado com um original do gravador."""

    track: TrackIR
    clip: ClipIR
    casamento: Casamento


@dataclass
class TakePlano:
    """Um take do gravador no AAF: a track dona, as entradas usadas e as faltando."""

    original: Original
    dona: TrackIR
    usadas: set[tuple[str, int]] = field(default_factory=set)
    faltando: list[tuple[str, int]] = field(default_factory=list)
    clips: int = 0


@dataclass
class Plano:
    """O casamento do AAF inteiro com a pasta do gravador, antes de mudar o IR."""

    folder: Path
    takes: dict[Path, TakePlano] = field(default_factory=dict)
    usos: list[Uso] = field(default_factory=list)
    notas: Notas = field(default_factory=Notas)
    originais: int = 0  # WAVs legiveis da pasta do gravador


def _ordem(chave: tuple[str, int]) -> tuple[bool, int]:
    return (chave[0] != "In", chave[1])


def rotulo(chave: tuple[str, int]) -> str:
    return f"{chave[0]} {chave[1]}"


def planejar(session: SessionIR, folder) -> Plano:
    """Casa cada clip de audio com o gravador e acha as entradas faltando por take."""
    plano = Plano(folder=Path(folder))
    indice = indexar(folder, plano.notas)
    plano.originais = indice.total
    cache: dict = {}
    for track in session.tracks:
        if track.is_video or track.kind != "audio":
            continue
        for clip in track.clips:
            asset = session.media_by_id(clip.media_id) if clip.media_id else None
            casamento = casar(clip, asset, indice, cache, plano.notas)
            if casamento is None:
                continue
            take = plano.takes.setdefault(
                casamento.original.path, TakePlano(original=casamento.original, dona=track)
            )
            take.usadas.add(casamento.key)
            take.clips += 1
            plano.usos.append(Uso(track, clip, casamento))
    for take in plano.takes.values():
        todas = {canal.key for canal in take.original.canais}
        take.faltando = sorted(todas - take.usadas, key=_ordem)
    return plano


def resumo(plano: Plano) -> dict:
    """Inspect: por take, o original casado, a track dona e as entradas acrescentadas."""
    return {
        "folder": str(plano.folder),
        "takes": [
            {
                "original": str(take.original.path),
                "owner": take.dona.name,
                "clips": take.clips,
                "inputs_used": [rotulo(c) for c in sorted(take.usadas, key=_ordem)],
                "inputs_added": [rotulo(c) for c in take.faltando],
            }
            for take in plano.takes.values()
        ],
        "notes": dict(plano.notas.contagem),
    }


# --- Irmas ---


@dataclass
class Irma:
    """Uma track irma em montagem: a track, a dona e os trechos ja ocupados."""

    track: TrackIR
    dona: TrackIR
    chave: tuple[str, int]
    mics: list[str | None] = field(default_factory=list)
    ocupados: list[tuple[int, int]] = field(default_factory=list)
    # Por trecho ocupado: o original e a ancora (posicao na linha do tempo do
    # inicio da fonte), para saber se uma sobreposicao repete o mesmo audio.
    fontes: list[tuple[int, int, str, int]] = field(default_factory=list)


def _mic(take: TakePlano, chave: tuple[str, int]) -> str | None:
    return next((c.mic for c in take.original.canais if c.key == chave), None)


def _nome_fixo(mics: list[str | None]) -> str | None:
    """Nome unico da entrada em todos os takes da irma, ignorando caixa."""
    if not mics or not all(mics) or len({m.casefold() for m in mics}) != 1:
        return None
    return mics[0]


def _criar_irmas(plano: Plano, extras: dict) -> dict[tuple[int, tuple], Irma]:
    """Uma irma por (track dona, entrada faltando), com os microfones dos takes."""
    irmas: dict[tuple[int, tuple], Irma] = {}
    for take in plano.takes.values():
        for chave in take.faltando:
            chave_irma = (id(take.dona), chave)
            if chave_irma not in irmas:
                track = _track_irma(take.dona, chave, len(irmas), extras)
                irmas[chave_irma] = Irma(track=track, dona=take.dona, chave=chave)
            irmas[chave_irma].mics.append(_mic(take, chave))
    for irma in irmas.values():
        fixo = _nome_fixo(irma.mics)
        base = f"{irma.dona.name} {SEPARADOR} {rotulo(irma.chave)}"
        irma.track.name = f"{base} {SEPARADOR} {fixo}" if fixo else base
    return irmas


def _track_irma(dona: TrackIR, chave: tuple[str, int], indice: int, extras: dict) -> TrackIR:
    """Track irma com o mix da dona: mute, ganho, pan e as automacoes."""
    track = TrackIR(
        id=f"{dona.id}x{indice}",
        source_index=-1,
        name=rotulo(chave),
        kind="audio",
        channel_count=1,
        muted=dona.muted,
        gain_linear=dona.gain_linear,
        pan=dona.pan,
        gain_points=list(dona.gain_points),
        pan_points=list(dona.pan_points),
        expansion_group=dona.name,
    )
    track.raw = {"expanded": EXPANDED_MARK, "owner": dona.name, **extras}
    return track


def _livres(inicio: int, fim: int, ocupados: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Trechos de [inicio, fim) fora dos trechos ja ocupados."""
    pecas = [(inicio, fim)]
    for a, b in ocupados:
        novas = []
        for x, y in pecas:
            if b <= x or a >= y:
                novas.append((x, y))
                continue
            if x < a:
                novas.append((x, a))
            if b < y:
                novas.append((b, y))
        pecas = novas
    return pecas


def _fade(fade: FadeIR | None, manter: bool, duracao: int) -> FadeIR | None:
    if fade is None or not manter:
        return None
    novo = copy.deepcopy(fade)
    novo.duration_samples = min(novo.duration_samples, duracao)
    return novo


def _clip_irma(irma: Irma, uso: Uso, peca: tuple[int, int], mono: MediaAsset) -> ClipIR:
    """Clip da irma no trecho ``peca`` do clip do editor."""
    clip, (a, b) = uso.clip, peca
    inicio, fim = clip.timeline_start_samples, clip.timeline_end_samples
    offset = (clip.source_offset_samples or 0) + (a - inicio)
    mic = _mic_do_clip(irma, uso)
    return ClipIR(
        id=f"{irma.track.id}c{len(irma.track.clips)}",
        track_id=irma.track.id,
        channel_index=0,
        event_index=len(irma.track.clips),
        name=f"{clip.name} {SEPARADOR} {mic}" if mic else clip.name,
        media_id=mono.id,
        timeline_start_samples=a,
        timeline_end_samples=b,
        source_offset_samples=offset,
        muted=clip.muted,
        gain_linear=clip.gain_linear,
        fade_in=_fade(clip.fade_in, a == inicio, b - a),
        fade_out=_fade(clip.fade_out, b == fim, b - a),
        crossfade_in_state=clip.crossfade_in_state and a == inicio,
        source_channel=1,
        raw={
            "expanded": {
                "source": str(uso.casamento.original.path),
                "input": rotulo(irma.chave),
                "channel": mono.raw["channel"],
                "editor_track": uso.track.name,
                "editor_start": inicio,
                "editor_end": fim,
                "start": a,
                "end": b,
                "offset": offset,
            }
        },
    )


def _mic_do_clip(irma: Irma, uso: Uso) -> str | None:
    """Microfone do take no nome do clip, quando a irma nao leva um nome fixo."""
    if _nome_fixo(irma.mics):
        return None
    return next(
        (c.mic for c in uso.casamento.original.canais if c.key == irma.chave and c.mic), None
    )


# --- Monos ---


def _monos(original: Original, media_dir: Path, session: SessionIR) -> dict[tuple, MediaAsset]:
    """Divide o original em monos, uma vez, e devolve o asset de cada entrada."""
    caminhos = split_channels(original.path, media_dir, preserve_metadata=True)
    info = original.arquivo.info
    assets = {}
    for canal, caminho in zip(original.canais, caminhos):
        asset = MediaAsset(
            id=f"x{len(session.media)}",
            filename=caminho.name,
            channels=1,
            sample_rate=info.sample_rate,
            duration_samples=info.frames,
            origin_timecode_samples=original.arquivo.timecode,
            status="resolved",
            declared_location=str(caminho),
            resolved_path=str(caminho),
            raw={"source": str(original.path), "channel": canal.posicao},
        )
        session.media.append(asset)
        assets[canal.key] = asset
    return assets


def _dividir(plano: Plano, media_dir: Path, session: SessionIR, report: Report) -> dict:
    """Monos de cada take com entrada faltando; o take que nao divide fica de fora."""
    monos: dict[Path, dict] = {}
    for caminho, take in plano.takes.items():
        if not take.faltando:
            continue
        try:
            monos[caminho] = _monos(take.original, Path(media_dir), session)
        except (ValueError, OSError) as exc:
            report.warning(
                "EXPANSION_SPLIT_FAILED",
                f"could not split {caminho.name} into mono files ({exc}); "
                "its missing inputs were not added",
                {"filename": caminho.name},
            )
    return monos


# --- Montagem ---


def _nota_da_sobreposicao(irma: Irma, inicio: int, fim: int, caminho: str, ancora: int) -> str:
    """Duplicado (mesmo original na mesma ancora) e informacao; o resto e aviso."""
    for a, b, outro, outra_ancora in irma.fontes:
        sobrepoe = a < fim and inicio < b
        if sobrepoe and (outro != caminho or outra_ancora != ancora):
            return NOTA_SOBREPOSTO
    return NOTA_DUPLICADO


def _colocar(irmas: dict, plano: Plano, monos: dict, notas: Notas) -> None:
    """Coloca os clips nas irmas na ordem do AAF, sem sobrepor."""
    vistos: set[tuple] = set()
    for uso in plano.usos:
        caminho = uso.casamento.original.path
        take = plano.takes[caminho]
        clip = uso.clip
        posicao = (id(uso.track), caminho, clip.timeline_start_samples, clip.timeline_end_samples)
        if caminho not in monos or posicao in vistos:
            continue
        vistos.add(posicao)
        for chave in take.faltando:
            irma = irmas[(id(take.dona), chave)]
            inicio, fim = clip.timeline_start_samples, clip.timeline_end_samples
            pecas = _livres(inicio, fim, irma.ocupados)
            ancora = inicio - (clip.source_offset_samples or 0)
            if pecas != [(inicio, fim)]:
                notas.add(_nota_da_sobreposicao(irma, inicio, fim, str(caminho), ancora))
            for peca in pecas:
                irma.track.clips.append(_clip_irma(irma, uso, peca, monos[caminho][chave]))
            irma.ocupados.append((inicio, fim))
            irma.fontes.append((inicio, fim, str(caminho), ancora))


def _nome_livre(nome: str, usados: set[str]) -> str:
    """O nome, ou ``NOME (2)`` em diante quando ele ja existe no set."""
    livre, numero = nome, 1
    while livre in usados:
        numero += 1
        livre = f"{nome} ({numero})"
    usados.add(livre)
    return livre


def _inserir(session: SessionIR, irmas: dict) -> int:
    """Poe as irmas com clips logo depois da dona, na ordem das entradas.

    O nome de cada irma e unico no set: duas donas com o mesmo nome dariam a
    mesma irma, e o sidecar, que guarda as irmas pelo nome, perderia uma.
    """
    por_dona: dict[int, list[Irma]] = {}
    for irma in irmas.values():
        if irma.track.clips:
            irma.track.clips.sort(key=lambda c: (c.timeline_start_samples, c.timeline_end_samples))
            por_dona.setdefault(id(irma.dona), []).append(irma)
    usados = {track.name for track in session.tracks}
    tracks = []
    for track in session.tracks:
        tracks.append(track)
        lista = sorted(por_dona.get(id(track), []), key=lambda i: _ordem(i.chave))
        if lista:
            track.expansion_group = track.name
        for irma in lista:
            irma.track.name = _nome_livre(irma.track.name, usados)
            tracks.append(irma.track)
    session.tracks = tracks
    return sum(len(lista) for lista in por_dona.values())


def _relatar(plano: Plano, total: int, groups: str, report: Report) -> None:
    incompletos = sum(1 for take in plano.takes.values() if take.faltando)
    report.info(
        "EXPANSION_SUMMARY",
        f"{len(plano.takes)} takes matched to the production audio, {incompletos} with "
        f"inputs left out by the editor; {total} sibling tracks added",
        {"takes": len(plano.takes), "expanded_takes": incompletos, "siblings": total},
    )
    if total and groups == "prefix":
        report.info(
            "EXPANSION_GROUPS_PREFIX",
            "tracks with siblings stay in their expansion group and out of the prefix groups",
        )


def expand_production_channels(
    session: SessionIR,
    folder,
    media_dir: Path,
    report: Report,
    *,
    groups: str = "none",
    editor: str | None = None,
) -> Plano:
    """Acrescenta ao IR as irmas das entradas que o editor deixou de fora."""
    plano = planejar(session, folder)
    extras = {
        "folder": str(Path(folder).resolve()),
        "editor": _absoluto(editor),
        "editor_sha256": _hash_do_editor(editor),
    }
    irmas = _criar_irmas(plano, extras)
    monos = _dividir(plano, media_dir, session, report)
    _colocar(irmas, plano, monos, plano.notas)
    total = _inserir(session, irmas)
    plano.notas.flush(report)
    _relatar(plano, total, groups, report)
    return plano


def preview_production_channels(
    session: SessionIR, folder, report: Report, *, groups: str = "none"
) -> Plano:
    """Leitura do app: as irmas com os clips no IR, sem dividir nem gravar nada.

    Recusa a pasta que nao existe e a pasta sem WAV legivel, com a mensagem do
    motor; as notas e o resumo vao para o report como na conversao.
    """
    if not Path(folder).is_dir():
        raise ValueError(f"the production audio folder {folder} does not exist")
    plano = planejar(session, folder)
    if not plano.originais:
        raise ValueError(f"no readable WAV file in {folder}")
    irmas = _criar_irmas(plano, {})
    monos = {
        caminho: _monos_previstos(take.original)
        for caminho, take in plano.takes.items()
        if take.faltando
    }
    _colocar(irmas, plano, monos, plano.notas)
    total = _inserir(session, irmas)
    plano.notas.flush(report)
    _relatar(plano, total, groups, report)
    return plano


def _monos_previstos(original: Original) -> dict[tuple, MediaAsset]:
    """O asset de cada entrada sem o arquivo mono: so o canal, para a leitura."""
    return {
        canal.key: MediaAsset(
            id=f"preview:{original.path.name}:{canal.posicao}",
            filename=original.path.name,
            channels=1,
            raw={"source": str(original.path), "channel": canal.posicao},
        )
        for canal in original.canais
    }


def _absoluto(caminho: str | None) -> str | None:
    return str(Path(caminho).resolve()) if caminho else None


def _hash_do_editor(caminho: str | None) -> str | None:
    """SHA-256 do AAF do editor, para o verify reconhecer uma copia dele."""
    if not caminho or not Path(caminho).is_file():
        return None
    return _digest(Path(caminho))


def resolve_for_inspect(session: SessionIR, roots: list[Path], report: Report) -> None:
    """Inspect: procura no disco a midia dos clips, sem extrair nada."""
    for asset in session.media:
        if asset.status == "resolved":
            continue
        try:
            resolve_asset(asset, roots, report, taxa_tc=session.timecode_rate)
        except Exception:  # noqa: BLE001 - midia que nao resolve so nao casa
            continue
