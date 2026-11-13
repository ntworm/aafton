"""Leitura de FCPXML 1.9 para o SessionIR, com tempos exatos e sem o extra.

O adaptador instalado do OpenTimelineIO arredonda todo tempo para o frame
inteiro, ignora o ganho, os fades, os filtros e o audio da storyline
principal. Por isso o produto le o FCPXML com o proprio codigo: a biblioteca
padrao e a aritmetica exata de fracao. O leitor recusa o que ele nao sabe
ler: fade, efeito, melhoria de audio, ganho com curva, pan que nao e
neutro, escolha de parte dos canais, escolha de canais fora de ordem e
arquivo com mais de dois canais, cada um com o seu codigo no relatorio. O
clip de um arquivo com mais de dois canais fica fora da cena.

A cena vem do projeto do documento: os recursos vem antes da biblioteca e um
compound clip traz a propria sequencia nos recursos, que nao e a cena do
projeto. O percurso da spine e dos itens que ela contem esta em fcpxml_itens, e
as regras de audio do clip, em fcpxml_audio.

O clip guarda no ClipIR a quantidade de canais que o documento declara e o
fato de ele escolher canais do arquivo: a midia resolvida troca o numero
declarado pelo do arquivo de verdade.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote

from ..ir import ClipIR, MediaAsset, SessionIR, TrackIR
from ..report import Report
from ..timecode import DROP_FRAME_RATES, TimecodeRate
from .fcpxml_fix import tempo_fcpxml
from .fcpxml_itens import Asset, Clip, Contexto, amostras, percorrer_spine, tempo_atributo

APLICACAO = "fcpxml"
# Taxas de audio que o FCPXML nomeia.
TAXAS_AUDIO = {
    "44.1k": 44100,
    "48k": 48000,
    "88.2k": 88200,
    "96k": 96000,
    "192k": 192000,
}
TAXA_AUDIO_PADRAO = 48000


def read_fcpxml(path, report: Report) -> SessionIR:
    """Le um FCPXML e devolve o SessionIR da cena."""
    caminho = Path(path)
    raiz = _raiz(caminho)
    if raiz is None:
        report.error("FORMAT_FCPXML_INVALID", f"{caminho.name!r} is not a valid FCPXML")
        return _sessao_vazia(caminho)
    projeto, sequencia = _projeto(raiz, report)
    if sequencia is None:
        return _sessao_vazia(caminho)
    contexto = Contexto(report=report, sample_rate=_sample_rate(sequencia, report))
    taxa = _taxa_de_frames(raiz, sequencia, contexto)
    if taxa is None:
        return _sessao_vazia(caminho)
    _assets(raiz, contexto)
    percorrer_spine(sequencia, contexto)
    return _sessao(projeto, sequencia, caminho, taxa, contexto)


def _projeto(raiz: ET.Element, report: Report) -> tuple[ET.Element | None, ET.Element | None]:
    """Projeto do documento e a sequencia dele.

    A cena e a do projeto: os recursos vem antes da biblioteca e um compound
    clip traz a propria sequencia nos recursos, que nao e a cena do projeto.
    """
    projetos = list(raiz.iter("project"))
    if not projetos:
        report.error("FORMAT_FCPXML_INVALID", "the FCPXML has no project")
        return None, None
    if len(projetos) > 1:
        nomes = ", ".join(projeto.get("name") or "?" for projeto in projetos)
        report.error(
            "FORMAT_FCPXML_MULTIPLE_PROJECTS",
            f"the FCPXML has more than one project: {nomes}",
        )
    projeto = projetos[0]
    sequencia = projeto.find("sequence")
    if sequencia is None:
        report.error("FORMAT_FCPXML_INVALID", "the project of the FCPXML has no sequence")
        return projeto, None
    return projeto, sequencia


def _sessao_vazia(caminho: Path) -> SessionIR:
    """Sessao vazia devolvida quando o FCPXML nao pode ser lido."""
    return SessionIR(
        name=caminho.stem,
        sample_rate=TAXA_AUDIO_PADRAO,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=0,
        source_path=str(caminho),
        source_application=APLICACAO,
    )


def _sessao(projeto, sequencia, caminho: Path, taxa: TimecodeRate, contexto: Contexto) -> SessionIR:
    """Monta o SessionIR da cena lida."""
    return SessionIR(
        name=_nome_da_sessao(projeto, sequencia, caminho),
        sample_rate=contexto.sample_rate,
        timecode_rate=taxa,
        start_timecode_samples=_inicio_da_sessao(sequencia, contexto),
        tracks=_tracks(contexto),
        media=contexto.media,
        markers=contexto.markers,
        source_path=str(caminho),
        source_application=APLICACAO,
    )


def _raiz(caminho: Path) -> ET.Element | None:
    """Raiz do documento; None quando o arquivo falta ou nao e XML valido."""
    try:
        return ET.fromstring(caminho.read_text(encoding="utf-8").encode("utf-8"))
    except (OSError, ValueError, ET.ParseError):
        return None


def _taxa_de_frames(raiz, sequencia, contexto: Contexto) -> TimecodeRate | None:
    """Taxa de frames da sequencia, pelo frameDuration do seu formato."""
    duracoes = {f.get("id"): f.get("frameDuration") for f in raiz.iter("format")}
    bruto = duracoes.get(sequencia.get("format"))
    if not bruto:
        contexto.erro("FORMAT_RATE_UNSUPPORTED", "the sequence does not declare the frameDuration")
        return None
    try:
        fracao = 1 / tempo_fcpxml(bruto)
    except (ValueError, ZeroDivisionError):
        contexto.erro("FORMAT_RATE_UNSUPPORTED", f"invalid frameDuration: {bruto!r}")
        return None
    if fracao.denominator not in (1, 1001):
        contexto.erro("FORMAT_RATE_UNSUPPORTED", f"frame rate not supported: {fracao}")
        return None
    drop = (sequencia.get("tcFormat") or "").upper() == "DF"
    if drop and (fracao.numerator, fracao.denominator) not in DROP_FRAME_RATES:
        drop = False
    return TimecodeRate(fracao.numerator, fracao.denominator, drop)


def _sample_rate(sequencia, report: Report) -> int:
    """Taxa de amostras da sequencia; 48000 quando o FCPXML nao declara."""
    bruto = (sequencia.get("audioRate") or "").lower()
    if bruto in TAXAS_AUDIO:
        return TAXAS_AUDIO[bruto]
    report.info("FORMAT_SAMPLE_RATE_ASSUMED", f"sample rate assumed: {TAXA_AUDIO_PADRAO}")
    return TAXA_AUDIO_PADRAO


def _nome_da_sessao(projeto, sequencia, caminho: Path) -> str:
    """Nome da sessao: o do projeto, o da sequencia ou o do arquivo."""
    return projeto.get("name") or sequencia.get("name") or caminho.stem


def _inicio_da_sessao(sequencia, contexto: Contexto) -> int:
    """Inicio da sessao em samples, pelo tcStart da sequencia."""
    if sequencia.get("tcStart"):
        inicio = tempo_atributo(contexto, sequencia, "tcStart")
        return amostras(contexto, inicio, "the start of the session")
    inicios = [clip.inicio for clips in contexto.clips.values() for clip in clips]
    return min(inicios) if inicios else 0


def _assets(raiz, contexto: Contexto) -> None:
    """Le os assets dos recursos do FCPXML."""
    for elemento in raiz.iter("asset"):
        identificador = elemento.get("id")
        if not identificador:
            continue
        rep = elemento.find("media-rep")
        caminho = elemento.get("src") or (rep.get("src", "") if rep is not None else "")
        # A quantidade de canais que o documento declara; sem declaracao, o
        # leitor assume um canal so e nao sabe que a escolha e parcial.
        declarado = (elemento.get("audioChannels") or "").strip()
        canais = max(1, int(declarado)) if declarado.isdecimal() else 1
        asset = Asset(
            id=identificador,
            nome=elemento.get("name") or "",
            inicio=tempo_atributo(contexto, elemento, "start"),
            canais=canais,
            caminho=_caminho_local(caminho),
            tem_audio=(elemento.get("hasAudio") or "0") == "1",
            elemento=elemento,
        )
        contexto.assets[asset.id] = asset


def _caminho_local(url: str) -> str:
    """Caminho local de uma URL file://, com o prefixo do host removido."""
    bruto = unquote(url).replace("file://localhost", "").replace("file://", "")
    return bruto[1:] if bruto.startswith("/") and bruto[2:3] == ":" else bruto


def _tracks(contexto: Contexto) -> list[TrackIR]:
    """Tracks de audio: uma por lane, mais as extras dos clips que se sobrepoem.

    Um corte em L ou em J estende o audio de um clip por cima do vizinho: os
    dois clips ficam na mesma lane e se sobrepoem. Uma Live track toca um clip
    por vez, entao o clip que sobrepoe vai para uma track extra da lane.
    """
    tracks: list[TrackIR] = []
    for lane in sorted(contexto.clips, key=_chave_da_lane):
        tracks.extend(_tracks_da_lane(contexto, lane, len(tracks)))
    return tracks


def _tracks_da_lane(contexto: Contexto, lane: int, primeiro: int) -> list[TrackIR]:
    """Tracks de uma lane: os clips que se sobrepoem ganham tracks extras."""
    grupos = _grupos_da_lane(contexto, lane, primeiro)
    return [_track(primeiro + numero, grupo, contexto) for numero, grupo in enumerate(grupos)]


def _grupos_da_lane(contexto: Contexto, lane: int, primeiro: int) -> list[list[Clip]]:
    """Reparte os clips da lane em grupos que nao se sobrepoem.

    Os clips entram na ordem do tempo. Cada clip vai para o primeiro grupo
    onde ele cabe; quando nenhum cabe, ele ganha um grupo novo, que e a track
    extra da lane.
    """
    grupos: list[list[Clip]] = []
    fins: list[int] = []
    for clip in sorted(contexto.clips[lane], key=lambda c: (c.inicio, c.fim)):
        indice = _grupo_que_cabe(fins, clip)
        if indice is None:
            indice = len(grupos)
            grupos.append([])
            fins.append(clip.fim)
        else:
            fins[indice] = clip.fim
        grupos[indice].append(clip)
        if indice > 0:
            _clip_movido(contexto, clip, primeiro + indice)
    return grupos


def _grupo_que_cabe(fins: list[int], clip: Clip) -> int | None:
    """Primeiro grupo da lane onde o clip cabe; None quando nenhum cabe."""
    for indice, fim in enumerate(fins):
        if clip.inicio >= fim:
            return indice
    return None


def _clip_movido(contexto: Contexto, clip: Clip, indice: int) -> None:
    """Avisa que o clip foi para outra track por causa da sobreposicao."""
    contexto.report.info(
        "FORMAT_OVERLAP_MOVED_TO_TRACK",
        f"the clip {clip.nome!r} was moved to the track 'A{indice + 1}' because of an overlap",
        {"clip": clip.nome, "track": f"A{indice + 1}"},
    )


def _track(indice: int, grupo: list[Clip], contexto: Contexto) -> TrackIR:
    """TrackIR de um grupo de clips da lane."""
    track = TrackIR(
        id=f"t{indice}",
        source_index=indice,
        original_source_indices=[indice],
        name=f"A{indice + 1}",
        kind="audio",
        channel_count=max([clip.canais for clip in grupo] + [1]),
    )
    for numero, clip in enumerate(grupo):
        track.clips.append(_clip_ir(track, numero, clip, contexto))
    return track


def _chave_da_lane(lane: int) -> tuple[int, int]:
    """Chave de ordenacao das lanes: a zero primeiro, as negativas depois."""
    if lane == 0:
        return (0, 0)
    if lane < 0:
        return (1, -lane)
    return (2, lane)


def _clip_ir(track: TrackIR, numero: int, clip: Clip, contexto: Contexto) -> ClipIR:
    """ClipIR do clip lido, com o offset e o ganho que o FCPXML traz."""
    midia = _midia(contexto, clip.asset_id)
    return ClipIR(
        id=f"{track.id}c{numero}_ch0",
        track_id=track.id,
        channel_index=max(clip.canal, 0),
        event_index=numero,
        name=clip.nome,
        media_id=midia.id,
        timeline_start_samples=clip.inicio,
        timeline_end_samples=clip.fim,
        source_offset_samples=clip.offset,
        gain_linear=clip.ganho,
        raw=_bruto_do_clip(clip, contexto),
    )


def _bruto_do_clip(clip: Clip, contexto: Contexto) -> dict:
    """O que o clip guarda do FCPXML: os canais declarados e a escolha de canal.

    A quantidade que o documento declara fica no clip porque a midia resolvida
    troca esse numero pelo do arquivo de verdade.
    """
    asset = contexto.assets.get(clip.asset_id)
    bruto = (asset.elemento.get("audioChannels") or "").strip() if asset else ""
    return {
        "fcpxml_canais_declarados": max(1, int(bruto)) if bruto.isdecimal() else None,
        # O canal SEM_CANAL do leitor de audio significa que o clip nao
        # escolhe canal nenhum do arquivo.
        "fcpxml_seleciona_canais": clip.canal >= 0,
    }


def _midia(contexto: Contexto, asset_id: str) -> MediaAsset:
    """MediaAsset do asset, um por asset do FCPXML."""
    existente = contexto.midias.get(asset_id)
    if existente is not None:
        return existente
    asset = contexto.assets[asset_id]
    partes = asset.caminho.replace("\\", "/").rstrip("/").split("/")
    nome = partes[-1] if asset.caminho else asset.nome
    midia = MediaAsset(
        id=f"m{len(contexto.media)}",
        filename=nome,
        channels=asset.canais,
        status="missing",
        declared_location=asset.caminho or None,
    )
    contexto.midias[asset_id] = midia
    contexto.media.append(midia)
    return midia
