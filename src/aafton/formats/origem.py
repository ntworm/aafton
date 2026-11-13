"""Escolha do leitor pelo sufixo do arquivo de origem.

O AAF continua lendo como sempre. A EDL e o XML do FCP passam pelo
OpenTimelineIO e precisam do extra formats; o FCPXML e lido pelo codigo do
produto. O offset na fonte dos clips que so trazem timecode de origem sai do
chunk bext do arquivo de midia.

O verify usa o mesmo caminho de leitura. A unica diferenca e a taxa de
frames da EDL, que vem do sidecar do .als gerado, e o caminho da midia que
esse sidecar registra: quando o arquivo existe nele, ele e o arquivo do
asset. O leitor de AAF e reexportado aqui porque o verify e quem o expoe e
quem o troca. O FCPXML passa tambem pela conferencia de canais do import,
para que o clip que saiu da cena nao conte como ausente na verificacao.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..aaf_read import read_aaf
from ..ir import ClipIR, MediaAsset, SessionIR
from ..media_extract import AmbiguousMediaError, parse_bext, resolve_asset
from ..report import Report
from ..sidecar import read_anchor
from ..timecode import TimecodeRate
from . import ADAPTADORES, ExtraAusente
from .otio_read import read_timeline

# Formatos de troca que nao levam canal por canal: o leitor precisa saber.
APLICACOES_SEM_CANAL = ("edl", "fcp_xml")
# O FCPXML carrega o par stereo, mas nao um arquivo de tres ou mais canais.
APLICACAO_FCPXML = "fcpxml"
# Quantos canais cada formato de troca carrega.
MAXIMO_DE_CANAIS = {APLICACAO_FCPXML: 2}
# Codigo do erro de midia com mais de um canal.
CODIGO_MULTICANAL = "FORMAT_MULTICHANNEL_UNSUPPORTED"
# Codigo do erro de escolha de canal que o documento nao sustenta.
CODIGO_SELECAO = "FORMAT_CHANNEL_SELECTION_UNSUPPORTED"


@dataclass
class _Busca:
    """Onde a midia da origem e procurada.

    As raizes sao as pastas de busca: as que o sidecar do .als registra e a
    pasta do arquivo de origem. Os caminhos sao os arquivos absolutos que o
    sidecar registra: quando o arquivo existe, ele e o arquivo do asset e as
    pastas nao sao procuradas.
    """

    raizes: list[Path]
    caminhos: dict[str, Path] = field(default_factory=dict)


def read_origin(
    path,
    report: Report,
    extract_dir=None,
    fades: str = "absorb",
    edl_rate=None,
    media_roots=None,
    mxf_dir=None,
    media_paths=None,
) -> SessionIR:
    """Le o arquivo de origem pelo formato do sufixo e devolve o SessionIR."""
    caminho = Path(path)
    sufixo = caminho.suffix.lower()
    if sufixo == ".aaf":
        return read_aaf(caminho, report, extract_dir=extract_dir, fades=fades)
    if sufixo in ADAPTADORES:
        busca = _Busca(list(media_roots or [caminho.parent]), dict(media_paths or {}))
        return _sessao_de_formato(caminho, report, edl_rate, busca, mxf_dir)
    report.error("FORMAT_UNKNOWN_SUFFIX", f"format not supported: {sufixo}")
    return _sessao_vazia(caminho)


def ler_origem_verify(path, report: Report, fades: str, als_path, leitor_aaf=None) -> SessionIR:
    """Le a origem da verificacao: AAF, EDL, XML do FCP ou FCPXML.

    A EDL nao diz qual e a taxa de frames de um jeito que o leitor possa
    usar; ela vem do sidecar do .als gerado, que e a ancora da conversao.
    Sem sidecar a leitura da EDL falha como sempre, com a taxa exigida.

    A midia pode ter sido importada de uma pasta que nao e a da origem: o
    sidecar do .als registra onde cada arquivo estava, e essas pastas sao
    procuradas tambem. O caminho absoluto que o sidecar registra vale
    quando o arquivo existe: uma copia do mesmo nome na pasta da origem
    nao pode fazer o verify reprovar por ambiguidade.

    O FCPXML passa pela mesma conferencia de canais que o import: o clip do
    arquivo com mais de dois canais fica fora da cena dos dois lados, e o
    verify nao conta clip ausente. A midia e procurada com um relatorio
    descartavel, para que um arquivo que nao esta nas pastas nao mude o verify.

    O leitor de AAF chega como parametro, porque quem chama e o verify: e
    ele que expoe o leitor, e assim o troca continua valendo.
    """
    caminho = Path(path)
    sufixo = caminho.suffix.lower()
    if sufixo == ".aaf":
        return (leitor_aaf or read_aaf)(caminho, report, fades=fades)
    taxa = _taxa_do_sidecar(als_path) if sufixo == ".edl" else None
    raizes = _raizes_de_midia(caminho, als_path)
    caminhos = _caminhos_do_sidecar(als_path)
    sessao = read_origin(
        caminho,
        report,
        fades=fades,
        edl_rate=taxa,
        media_roots=raizes,
        media_paths=caminhos,
    )
    if sufixo == ".fcpxml":
        _conferir_canais_da_origem(sessao, report, _Busca(raizes, caminhos))
    return sessao


def _conferir_canais_da_origem(sessao: SessionIR, report: Report, busca: _Busca) -> None:
    """Roda na origem da verificacao a mesma conferencia de canais do import.

    A midia e procurada com um relatorio descartavel: um arquivo que nao esta
    nas pastas nao vira issue.
    """
    descartavel = Report(strict=False)
    for asset in sessao.media:
        _resolver_midia(asset, busca, descartavel, None, sessao.timecode_rate)
    conferir_canais(sessao, report)


def _raizes_de_midia(caminho: Path, als_path) -> list[Path]:
    """Pastas onde a midia e procurada: as do sidecar do .als e a da origem.

    As pastas do sidecar vem primeiro, na ordem em que o sidecar as
    registra: sao as pastas onde a midia foi importada. A pasta da origem
    vem depois, para que uma copia do mesmo nome na pasta da origem nao
    faca o verify reprovar por ambiguidade.
    """
    raizes = _pastas_do_sidecar(als_path)
    if caminho.parent not in raizes:
        raizes.append(caminho.parent)
    return raizes


def _pastas_do_sidecar(als_path) -> list[Path]:
    """Pastas dos arquivos de midia que o sidecar do .als registra."""
    ancora = _ancora_do_sidecar(als_path)
    if ancora is None:
        return []
    hashes = ancora.media_hashes or {}
    pastas: list[Path] = []
    for chave in hashes:
        arquivo = Path(chave)
        if arquivo.is_absolute() and arquivo.parent not in pastas:
            pastas.append(arquivo.parent)
    return pastas


def _caminhos_do_sidecar(als_path) -> dict[str, Path]:
    """Caminhos absolutos que o sidecar do .als registra, por nome de arquivo.

    O sidecar sabe onde cada arquivo de midia estava quando o .als foi feito:
    o verify usa esse arquivo quando ele existe, e nao procura as pastas. Um
    nome que o sidecar registra em mais de uma pasta fica de fora: o caminho
    nao diz qual dos arquivos e o do asset, e a busca de sempre decide.
    """
    ancora = _ancora_do_sidecar(als_path)
    if ancora is None:
        return {}
    por_nome: dict[str, set[Path]] = {}
    for chave in ancora.media_hashes or {}:
        arquivo = Path(chave)
        if arquivo.is_absolute():
            por_nome.setdefault(arquivo.name, set()).add(arquivo)
    return {nome: next(iter(achados)) for nome, achados in por_nome.items() if len(achados) == 1}


def _ancora_do_sidecar(als_path):
    """Ancora do sidecar do .als; None quando nao da para ler."""
    try:
        return read_anchor(als_path)
    except Exception:  # noqa: BLE001 - sidecar ausente ou invalido nao impede a leitura
        return None


def _taxa_do_sidecar(als_path) -> TimecodeRate | None:
    """Taxa de frames do sidecar do .als; None quando nao da para ler."""
    ancora = _ancora_do_sidecar(als_path)
    return ancora.timecode_rate if ancora is not None else None


def conferir_canais(session: SessionIR, report: Report) -> None:
    """Recusa clip de midia com mais canais do que o formato de troca carrega.

    A EDL e o XML do FCP nao levam canal por canal: um arquivo estereo
    entraria como duas tracks mono com o par inteiro em cada uma. O FCPXML
    leva o par stereo, mas um arquivo de tres ou mais canais nao cabe no Live
    como um par, e tambem e recusado. O AAF carrega os canais, entao nao e
    conferido aqui.

    No FCPXML o clip do arquivo com mais de dois canais fica fora da cena,
    como no leitor: o Live nao toca tres ou mais canais como um par stereo.
    A track que perde o clip e fica sem clip nenhum sai da cena tambem, e a
    que guarda clip ganha a quantidade de canais dos clips que ficaram. O
    clip que escolhe canais de um arquivo com mais canais do que o documento
    declara e erro: o Live tocaria o arquivo inteiro.
    """
    if session.source_application not in (*APLICACOES_SEM_CANAL, APLICACAO_FCPXML):
        return
    maximo = MAXIMO_DE_CANAIS.get(session.source_application, 1)
    motivo = (
        "that Live does not play as a stereo pair"
        if session.source_application == APLICACAO_FCPXML
        else "that the exchange format does not carry"
    )
    fora: list[ClipIR] = []
    for track in session.tracks:
        for clip in track.clips:
            asset = session.media_by_id(clip.media_id) if clip.media_id else None
            if asset is None or not asset.channels or asset.channels <= maximo:
                continue
            report.error(
                CODIGO_MULTICANAL,
                f"the clip {clip.name!r} uses the file {asset.filename!r}, which has "
                f"{asset.channels} channels, {motivo}",
                {"clip": clip.name, "filename": asset.filename},
            )
            fora.append(clip)
    if session.source_application == APLICACAO_FCPXML:
        _tirar_da_cena(session, fora)
        _conferir_selecao(session, report)


def _tirar_da_cena(session: SessionIR, fora: list[ClipIR]) -> None:
    """Tira da cena os clips de midia com mais de dois canais.

    A track que perde o clip e fica sem clip nenhum sai da cena; a track que
    guarda clip ganha a quantidade de canais dos clips que ficaram, como o
    leitor do FCPXML conta.
    """
    if not fora:
        return
    ids = {id(clip) for clip in fora}
    sobraram = []
    for track in session.tracks:
        tinha = len(track.clips)
        track.clips[:] = [clip for clip in track.clips if id(clip) not in ids]
        if len(track.clips) < tinha:
            if not track.clips:
                continue
            track.channel_count = max(
                [clip.raw.get("fcpxml_canais_declarados") or 1 for clip in track.clips] + [1]
            )
        sobraram.append(track)
    session.tracks[:] = sobraram


def _conferir_selecao(session: SessionIR, report: Report) -> None:
    """O clip que escolhe canais de um arquivo com mais canais do que o
    documento declara e erro: o Live tocaria o arquivo inteiro."""
    for clip in session.all_clips():
        declarados = clip.raw.get("fcpxml_canais_declarados")
        if not clip.raw.get("fcpxml_seleciona_canais") or not declarados:
            continue
        asset = session.media_by_id(clip.media_id) if clip.media_id else None
        if asset is None or not asset.channels or asset.channels <= declarados:
            continue
        report.error(
            CODIGO_SELECAO,
            f"the clip {clip.name!r} chooses channels of the file {asset.filename!r}, which has "
            f"{asset.channels} channels, and the document declares {declarados}: "
            "Live would play the whole file",
            {"clip": clip.name, "filename": asset.filename},
        )


def _sessao_de_formato(
    caminho: Path, report: Report, edl_rate, busca: _Busca, mxf_dir
) -> SessionIR:
    """Le a cena de intercambio e resolve o timecode de origem dos clips."""
    try:
        session = read_timeline(caminho, report, edl_rate=edl_rate)
    except ExtraAusente as exc:
        report.error("FORMAT_EXTRA_MISSING", str(exc))
        return _sessao_vazia(caminho)
    _resolver_tc_de_fonte(session, report, busca, mxf_dir)
    return session


def _sessao_vazia(caminho: Path) -> SessionIR:
    """Sessao vazia, devolvida quando o arquivo nao pode ser lido."""
    return SessionIR(
        name=caminho.stem,
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=0,
        source_path=str(caminho),
    )


def _resolver_tc_de_fonte(session: SessionIR, report: Report, busca: _Busca, mxf_dir) -> None:
    """Calcula o offset na fonte dos clips que so tem timecode de origem."""
    for track in session.tracks:
        for clip in track.clips:
            _tc_do_clip(clip, session, report, busca, mxf_dir)


def _tc_do_clip(clip: ClipIR, session: SessionIR, report: Report, busca: _Busca, mxf_dir) -> None:
    """Offset na fonte de um clip, a partir do bext do arquivo de midia."""
    origem = clip.raw.get("source_tc_samples")
    if clip.source_offset_samples is not None or origem is None:
        return
    asset = session.media_by_id(clip.media_id)
    if asset is None:
        return
    _resolver_midia(asset, busca, report, mxf_dir, session.timecode_rate)
    if asset.status != "resolved" or not asset.resolved_path:
        return
    _offset_do_bext(clip, session, report, asset, origem)


def _offset_do_bext(
    clip: ClipIR, session: SessionIR, report: Report, asset: MediaAsset, origem: int
) -> None:
    """Calcula o offset na fonte com o timecode de origem que vem do bext."""
    bext = parse_bext(asset.resolved_path)
    if not bext or bext.get("time_reference") is None:
        report.error(
            "FORMAT_SOURCE_TC_UNRESOLVED",
            f"the file {asset.filename!r} has no origin timecode (bext) for the clip {clip.name!r}",
            {"clip": clip.name, "filename": asset.filename},
        )
        return
    if asset.sample_rate is not None and asset.sample_rate != session.sample_rate:
        report.error(
            "FORMAT_SOURCE_TC_RATE_MISMATCH",
            f"the file {asset.filename!r} is at {asset.sample_rate} Hz and the "
            f"session at {session.sample_rate} Hz",
            {"clip": clip.name, "filename": asset.filename},
        )
        return
    asset.origin_timecode_samples = bext["time_reference"]
    offset = origem - bext["time_reference"]
    duracao = asset.duration_samples
    fora = duracao is not None and offset + clip.duration_samples > duracao
    if offset < 0 or fora:
        report.error(
            "FORMAT_SOURCE_TC_OUTSIDE_MEDIA",
            f"the clip {clip.name!r} has an origin timecode outside the file {asset.filename!r}",
            {"clip": clip.name, "filename": asset.filename},
        )
        return
    clip.source_offset_samples = offset


def _resolver_midia(asset: MediaAsset, busca: _Busca, report: Report, mxf_dir, taxa_tc) -> None:
    """Procura a midia em disco, com os mesmos erros do import do AAF."""
    if asset.status == "resolved":
        return
    try:
        if not _resolver_do_sidecar(asset, busca, report, mxf_dir, taxa_tc):
            resolve_asset(asset, busca.raizes, report, mxf_dir=mxf_dir, taxa_tc=taxa_tc)
    except AmbiguousMediaError as exc:
        report.error("MEDIA_AMBIGUOUS", str(exc), {"filename": asset.filename})
    except ValueError as exc:
        report.error("MEDIA_FORMAT_UNSUPPORTED", str(exc), {"filename": asset.filename})
    except Exception as exc:  # noqa: BLE001
        report.error("MEDIA_RESOLVE_ERROR", str(exc), {"filename": asset.filename})


def _resolver_do_sidecar(
    asset: MediaAsset, busca: _Busca, report: Report, mxf_dir, taxa_tc
) -> bool:
    """Resolve o asset pelo caminho absoluto que o sidecar do .als registra.

    O sidecar sabe onde o arquivo de midia estava quando o .als foi feito: uma
    copia do mesmo nome na pasta da origem nao pode fazer o verify reprovar
    por ambiguidade. Sem o arquivo no caminho do sidecar, a busca e a de
    sempre.
    """
    caminho = busca.caminhos.get(asset.filename)
    if caminho is None or not caminho.is_file():
        return False
    declarado = asset.declared_location
    asset.declared_location = str(caminho)
    try:
        resolve_asset(asset, [], report, mxf_dir=mxf_dir, taxa_tc=taxa_tc)
    finally:
        asset.declared_location = declarado
    return True
