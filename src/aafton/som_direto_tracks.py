"""Tracks e clips do som direto, a divisao em monos e o inspect com os dois tempos.

Uma track por entrada no set inteiro, sempre mono: ``In N`` pelo CHANNEL_INDEX
do iXML, ``Ch N`` sem iXML. A track vira ``In N . NOME`` quando a entrada tem um
nome so, sem diferenciar caixa, em todos os takes do set; senao fica ``In N`` e
cada clip leva o microfone daquele take. O clip se chama ``SCENE/TAKE`` do iXML,
literal; sem iXML, o nome do arquivo. Dois takes que se sobrepoem na mesma
entrada nunca ficam na mesma track: o seguinte vai para ``NOME (2)``, com nota.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .audio_split import split_channels
from .bwf_meta import Ixml
from .ir import ClipIR, MediaAsset, SessionIR, TrackIR
from .report import Report
from .timecode import samples_to_label

if TYPE_CHECKING:
    from .som_direto import Arquivo, Canal, Take

SEPARADOR = "·"
_CIRCLED = {True: "yes", False: "no"}


def anotacao(ixml: Ixml | None) -> str:
    """Texto do clip: cena, take, tape, circled e nota, so os que existem."""
    if ixml is None:
        return ""
    campos = (
        ("Scene", ixml.scene),
        ("Take", ixml.take),
        ("Tape", ixml.tape),
        ("Circled", _CIRCLED.get(ixml.circled)),
        ("Note", ixml.note),
    )
    return "\n".join(f"{rotulo}: {valor}" for rotulo, valor in campos if valor)


def _nome_fixo(chave: tuple[str, int], itens: list[tuple[Take, Canal]]) -> str | None:
    """Nome unico da entrada no set inteiro, ignorando caixa; None quando varia."""
    if chave[0] != "In":
        return None
    nomes = [canal.mic for _, canal in itens]
    if not all(nomes) or len({nome.casefold() for nome in nomes}) != 1:
        return None
    return nomes[0]


def _nome_da_track(chave: tuple[str, int], fixo: str | None) -> str:
    base = f"{chave[0]} {chave[1]}"
    return f"{base} {SEPARADOR} {fixo}" if fixo else base


def _nome_do_clip(canal: Canal, mostrar_mic: bool) -> str:
    """SCENE/TAKE literal do iXML, mais o microfone numa track In N simples."""
    ixml = canal.arquivo.ixml
    if ixml is None or not (ixml.scene or ixml.take):
        nome = canal.arquivo.path.stem
    else:
        nome = f"{ixml.scene or ''}/{ixml.take or ''}"
    if mostrar_mic and canal.mic:
        nome += f" {SEPARADOR} {canal.mic}"
    return nome


def _asset(media: dict[Path, MediaAsset], arq: Arquivo) -> MediaAsset:
    """Asset do arquivo do gravador, criado uma vez por arquivo."""
    if arq.path not in media:
        caminho = str(arq.path)
        media[arq.path] = MediaAsset(
            id=f"m{len(media)}",
            filename=arq.path.name,
            channels=arq.info.channels,
            sample_rate=arq.info.sample_rate,
            duration_samples=arq.info.frames,
            origin_timecode_samples=arq.timecode,
            status="resolved",
            declared_location=caminho,
            resolved_path=caminho,
            raw={"day": arq.day.name},
        )
    return media[arq.path]


def _clip(track: TrackIR, indice: int, take: Take, canal: Canal, asset: MediaAsset, mic: bool):
    arq = canal.arquivo
    inicio = take.start + arq.offset
    producao = {
        "source": str(arq.path),
        "channel": canal.posicao,
        "day": take.day.name,
        "circled": bool(take.circled),
        "annotation": anotacao(arq.ixml),
    }
    return ClipIR(
        id=f"{track.id}c{indice}",
        track_id=track.id,
        channel_index=0,
        event_index=indice,
        name=_nome_do_clip(canal, mic),
        media_id=asset.id,
        timeline_start_samples=inicio,
        timeline_end_samples=inicio + (arq.info.frames or 0),
        source_offset_samples=0,
        source_channel=canal.posicao,
        raw={"timecode_samples": arq.timecode, "production": producao},
    )


def _faixas(itens: list[tuple[Take, Canal]]) -> list[list[tuple[Take, Canal]]]:
    """Reparte os takes de uma entrada em faixas sem sobreposicao.

    O take que comeca antes do fim de um take ja colocado vai para a primeira
    faixa livre; sem faixa livre, abre a seguinte.
    """
    faixas: list[list[tuple[Take, Canal]]] = []
    fins: list[int] = []
    for take, canal in itens:
        inicio = take.start + canal.arquivo.offset
        livre = next((i for i, fim in enumerate(fins) if fim <= inicio), None)
        if livre is None:
            faixas.append([])
            fins.append(0)
            livre = len(faixas) - 1
        faixas[livre].append((take, canal))
        fins[livre] = inicio + (canal.arquivo.info.frames or 0)
    return faixas


def _tracks_da_entrada(
    chave: tuple[str, int], itens: list, tracks: list[TrackIR], media: dict, report: Report
) -> None:
    """A track da entrada e, quando takes se sobrepoem, as tracks ``NOME (2)`` em diante."""
    fixo = _nome_fixo(chave, itens)
    nome = _nome_da_track(chave, fixo)
    mostrar_mic = chave[0] == "In" and fixo is None
    for numero, faixa in enumerate(_faixas(itens), start=1):
        indice = len(tracks)
        track = TrackIR(
            id=f"t{indice}",
            source_index=indice,
            name=nome if numero == 1 else f"{nome} ({numero})",
            kind="audio",
            channel_count=1,
        )
        for evento, (take, canal) in enumerate(faixa):
            asset = _asset(media, canal.arquivo)
            track.clips.append(_clip(track, evento, take, canal, asset, mostrar_mic))
        tracks.append(track)
        if numero > 1:
            report.info(
                "PRODUCTION_OVERLAP",
                f"{track.name}: {len(faixa)} takes overlapped takes of {nome} "
                "and went to this track",
                {"track": track.name, "takes": len(faixa)},
            )


def montar_tracks(takes: list[Take], report: Report) -> tuple[list[TrackIR], list[MediaAsset]]:
    """Tracks mono por entrada, In N primeiro, sem clips sobrepostos, e os assets."""
    por_chave: dict[tuple[str, int], list[tuple[Take, Canal]]] = {}
    for take in takes:
        for canal in take.canais:
            por_chave.setdefault(canal.key, []).append((take, canal))
    media: dict[Path, MediaAsset] = {}
    tracks: list[TrackIR] = []
    for chave in sorted(por_chave, key=lambda k: (k[0] != "In", k[1])):
        itens = sorted(
            por_chave[chave],
            key=lambda item: (item[0].start + item[1].arquivo.offset, item[0].name_key),
        )
        _tracks_da_entrada(chave, itens, tracks, media, report)
    return tracks, list(media.values())


def _monos(asset: MediaAsset, media_dir: Path) -> list[MediaAsset]:
    """Divide o arquivo do gravador num mono por canal, com bext e iXML."""
    caminhos = split_channels(Path(asset.resolved_path), media_dir, preserve_metadata=True)
    return [
        MediaAsset(
            id=f"{asset.id}c{canal}",
            filename=caminho.name,
            channels=1,
            sample_rate=asset.sample_rate,
            duration_samples=asset.duration_samples,
            origin_timecode_samples=asset.origin_timecode_samples,
            status="resolved",
            declared_location=str(caminho),
            resolved_path=str(caminho),
            raw={"source": asset.resolved_path, "channel": canal},
        )
        for canal, caminho in enumerate(caminhos, start=1)
    ]


def dividir_em_monos(session: SessionIR, media_dir: Path, report: Report) -> None:
    """Troca cada arquivo do gravador pelos monos dele na pasta de midia do set.

    O arquivo que nao divide fica de fora com os clips dele, com uma nota.
    """
    monos: dict[tuple[str, int], MediaAsset] = {}
    falhas: set[str] = set()
    for asset in session.media:
        try:
            divididos = _monos(asset, Path(media_dir))
        except (ValueError, OSError) as exc:
            report.warning(
                "PRODUCTION_SPLIT_FAILED",
                f"could not split {asset.filename} into mono files ({exc}); "
                "its clips were left out",
                {"filename": asset.filename},
            )
            falhas.add(asset.id)
            continue
        for mono in divididos:
            monos[(asset.id, mono.raw["channel"])] = mono
    for track in session.tracks:
        track.clips = [clip for clip in track.clips if clip.media_id not in falhas]
        for clip in track.clips:
            clip.media_id = monos[(clip.media_id, clip.source_channel)].id
    session.media = list(monos.values())


def _tempo(samples: int | None, session: SessionIR) -> dict | None:
    if samples is None:
        return None
    rotulo = samples_to_label(samples, session.timecode_rate, session.sample_rate)
    return {"samples": samples, "label": rotulo}


def com_os_dois_tempos(session: SessionIR, dados: dict) -> dict:
    """Inspect do som direto: cada clip com o tempo no set e o timecode real."""
    dados["layout"] = session.layout
    dados["blocks"] = session.layout_blocks
    for track_dados, track in zip(dados["tracks"], session.tracks):
        for clip_dados, clip in zip(track_dados["clips"], track.clips):
            clip_dados["set"] = _tempo(clip.timeline_start_samples, session)
            clip_dados["timecode"] = _tempo(clip.raw.get("timecode_samples"), session)
    return dados
