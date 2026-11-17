"""Verify das irmas da expansao de canais, ao lado do verify AAF -> ALS.

O sidecar diz quais tracks do set sao irmas, pelo nome. Elas saem da
comparacao com o AAF. Comparando o set com o AAF do editor (reconhecido pelo
SHA-256 que o sidecar registra, ou pelo mesmo caminho), cada clip de irma e
conferido contra o gravador e contra o corte: o inicio e o fim sao os do
sidecar, as amostras do mono sao iguais as daquela entrada do original no
trecho do clip, e um clip do editor no AAF (mesmo take, na track dona ou na de
origem, sobreposto a irma) tem o offset que a irma implica e a midia dele, a
copia do AAF, igual ao canal do original que ele usa, na tolerancia do
casamento. Comparando com um AAF que o aafton exportou, a track desse AAF com
o nome de uma irma e pareada com ela e conferida em posicao como qualquer
track; a irma que nao foi exportada nao conta como faltando nem como sobrando.
Quando o AAF nao e nenhum dos dois, as irmas ficam sem conferir, e o verify diz.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path

from .als_read import read_session
from .audio_split import _digest
from .bwf_meta import BwfInfo, read_bwf
from .expandir_casamento import (
    Original,
    canal_perto,
    entrada_do_clip,
    mesmo_take,
    original_do_arquivo,
)
from .ir import ClipIR, MediaAsset, SessionIR, TrackIR
from .media_extract import read_audio_metadata, resolve_asset
from .report import Report
from .sidecar import read_anchor
from .som_direto_verify import FRAMES_POR_LEITURA, _canal_dos_dados
from .timecode import samples_to_label

MAX_NOMES = 5  # clips citados na mensagem de erro
NAO_CONFERIDAS = (
    "Siblings: not checked (the AAF is neither the editor AAF nor an AAF exported by aafton)"
)


@dataclass
class Irmas:
    """As irmas do set e como o verify confere cada uma."""

    dados: dict
    origem: SessionIR
    destino: SessionIR
    removidas: list[TrackIR] = field(default_factory=list)
    pareadas: list[tuple[TrackIR, TrackIR]] = field(default_factory=list)
    gravador: bool = False
    # Pastas onde a midia dos clips do editor e procurada.
    raizes: list[Path] = field(default_factory=list)


def _dados(als_path) -> dict | None:
    try:
        return read_anchor(als_path).expanded
    except Exception:  # noqa: BLE001 - sem sidecar legivel nao ha irmas
        return None


def _mesmo_arquivo(a, b) -> bool:
    if not b:
        return False
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return False


def _e_o_editor(aaf_path, dados: dict) -> bool:
    """O AAF e o do editor: o SHA-256 do sidecar ou, como reserva, o mesmo caminho."""
    esperado = dados.get("editor_sha256")
    caminho = Path(aaf_path)
    if esperado and caminho.is_file() and _digest(caminho) == esperado:
        return True
    return _mesmo_arquivo(aaf_path, dados.get("editor"))


def _raizes(aaf_path, als_path) -> list[Path]:
    from .formats.origem import _raizes_de_midia

    return _raizes_de_midia(Path(aaf_path), als_path)


def _indices(track: TrackIR) -> list[int]:
    return list(track.original_source_indices or [track.source_index])


def _compactar(origem: SessionIR) -> None:
    """Renumera os slots do AAF sem as irmas, na ordem, como se nao tivessem saido."""
    antigos = sorted({i for t in origem.tracks for i in _indices(t) if i >= 0})
    novo = {valor: posicao for posicao, valor in enumerate(antigos)}
    for track in origem.tracks:
        track.original_source_indices = [novo.get(i, i) for i in _indices(track)]
        track.source_index = novo.get(track.source_index, track.source_index)


def read_without_siblings(
    als_path, report: Report, origem: SessionIR, aaf_path
) -> tuple[SessionIR, Irmas | None]:
    """Le o set e tira as irmas da comparacao com o AAF; None quando nao ha irmas."""
    destino = read_session(als_path, report)
    dados = _dados(als_path)
    nomes = set((dados or {}).get("tracks") or {})
    if not nomes:
        return destino, None
    irmas = Irmas(dados=dados, origem=origem, destino=destino)
    do_aaf = _separar(irmas, nomes)
    irmas.gravador = not do_aaf and _e_o_editor(aaf_path, dados)
    if irmas.gravador:
        irmas.raizes = _raizes(aaf_path, als_path)
    return destino, irmas


def _separar(irmas: Irmas, nomes: set) -> bool:
    """Tira as irmas do set e, quando o AAF tem tracks com os nomes delas, do AAF."""
    destino, origem = irmas.destino, irmas.origem
    irmas.removidas = [t for t in destino.tracks if t.name in nomes]
    destino.tracks = [t for t in destino.tracks if t.name not in nomes]
    do_aaf = {t.name: t for t in origem.tracks if t.name in nomes}
    irmas.pareadas = [(do_aaf[t.name], t) for t in irmas.removidas if t.name in do_aaf]
    if do_aaf:
        origem.tracks = [t for t in origem.tracks if t.name not in nomes]
        _compactar(origem)
    return bool(do_aaf)


def check_siblings(resultado, irmas: Irmas | None):
    """Acrescenta ao resultado do verify a conferencia das irmas e o devolve."""
    if irmas is None:
        return resultado
    for track_aaf, irma in irmas.pareadas:
        _parear(resultado, track_aaf, irma, irmas.origem)
    linhas = []
    if irmas.pareadas:
        linhas.append(f"Siblings: {len(irmas.pareadas)} sibling tracks compared with the AAF")
    if irmas.gravador:
        linhas.append(_conferir_gravador(resultado, irmas))
    elif irmas.removidas and not irmas.pareadas:
        # Sem saber de onde vem o AAF, as irmas nao contam como conferidas.
        linhas.append(NAO_CONFERIDAS)
    resultado.expanded_lines = linhas
    if linhas:
        texto = "; ".join(linhas)
        resultado.coverage_msg = f"{resultado.coverage_msg}; {texto}".lstrip("; ")
    return resultado


# --- Irma pareada com uma track do AAF exportado ---


def _ordenados(track: TrackIR) -> list[ClipIR]:
    return sorted(track.clips, key=lambda c: (c.timeline_start_samples, c.timeline_end_samples))


def _parear(resultado, track_aaf: TrackIR, irma: TrackIR, origem: SessionIR) -> None:
    """Confere em posicao, como qualquer track, os clips da irma e da track do AAF."""
    esperados, reais = _ordenados(track_aaf), _ordenados(irma)
    resultado.audio_expected += len(esperados)
    for indice in range(max(len(esperados), len(reais))):
        esperado = esperados[indice] if indice < len(esperados) else None
        real = reais[indice] if indice < len(reais) else None
        resultado.rows.append(_linha(resultado, irma.name, esperado, real, origem))


def _linha(resultado, nome: str, esperado, real, origem: SessionIR):
    from .verify import VerifyRow

    def rotulo(clip, campo) -> str:
        return "-" if clip is None else _rotulo(getattr(clip, campo), origem)

    clip = esperado if esperado is not None else real
    inicio, fim = clip.duration_samples, None
    if esperado is not None and real is not None:
        inicio = real.timeline_start_samples - esperado.timeline_start_samples
        fim = real.timeline_end_samples - esperado.timeline_end_samples
    quadro = origem.sample_rate * origem.timecode_rate.den / origem.timecode_rate.num
    return VerifyRow(
        track=nome,
        clip=clip.name,
        aaf_in=rotulo(esperado, "timeline_start_samples"),
        aaf_out=rotulo(esperado, "timeline_end_samples"),
        als_in=rotulo(real, "timeline_start_samples"),
        als_out=rotulo(real, "timeline_end_samples"),
        delta_samples=inicio,
        delta_frames=inicio / quadro,
        delta_end_samples=fim,
        status=_status(resultado, esperado, real, inicio, fim),
    )


def _status(resultado, esperado, real, inicio: int, fim: int | None) -> str:
    """Estado da linha e as contagens do resultado, como o verify do AAF."""
    if real is None:
        resultado.audio_missing += 1
        return "missing in the ALS"
    if esperado is None:
        resultado.audio_extra += 1
        return "extra in the ALS"
    resultado.audio_compared += 1
    tol = resultado.tolerance_samples
    return "ok" if abs(inicio) <= tol and abs(fim or 0) <= tol else "out of tolerance"


def _rotulo(samples: int, sessao: SessionIR) -> str:
    return samples_to_label(samples, sessao.timecode_rate, sessao.sample_rate)


# --- Irma conferida contra o gravador ---


def trecho_igual(mono: Path, fonte: Path, canal: int, inicio: int, total: int) -> bool:
    """O mono tem, no trecho, as amostras do canal ``canal`` da fonte, byte a byte."""
    m, f = read_audio_metadata(mono), read_audio_metadata(fonte)
    if not _trecho_cabe(m, f, canal, inicio, total):
        return False
    largura = f["bits"] // 8
    quadro = largura * f["channels"]
    with mono.open("rb") as a, fonte.open("rb") as b:
        a.seek(m["data_offset"] + inicio * largura)
        b.seek(f["data_offset"] + inicio * quadro)
        while total:
            n = min(total, FRAMES_POR_LEITURA)
            bruto, lido = b.read(n * quadro), a.read(n * largura)
            if len(bruto) != n * quadro or _canal_dos_dados(bruto, canal, largura, quadro) != lido:
                return False
            total -= n
    return True


def _trecho_cabe(m: dict, f: dict, canal: int, inicio: int, total: int) -> bool:
    """O mono e a fonte se comparam e o trecho cabe nos dois."""
    if m["channels"] != 1 or m["bits"] != f["bits"] or not 1 <= canal <= f["channels"]:
        return False
    limite = min(m["duration_samples"], f["duration_samples"])
    return inicio >= 0 and total > 0 and inicio + total <= limite


def _clip_confere(esperado: dict, real: ClipIR, irmas: Irmas) -> bool:
    """Posicao igual a do sidecar e o mono igual a entrada do original no trecho."""
    if real.timeline_start_samples != esperado["start"]:
        return False
    if real.timeline_end_samples != esperado["end"]:
        return False
    asset = irmas.destino.media_by_id(real.media_id) if real.media_id else None
    if asset is None or not asset.resolved_path or not Path(asset.resolved_path).is_file():
        return False
    fonte = Path(esperado["source"])
    if not fonte.is_file():
        return False
    inicio = real.source_offset_samples or 0
    return trecho_igual(
        Path(asset.resolved_path), fonte, int(esperado["channel"]), inicio, real.duration_samples
    )


class _Corte:
    """O corte do editor no AAF lido pelo verify: clips por track, midia e originais.

    A midia de cada clip do editor e procurada como no verify da origem (as
    pastas do sidecar e a do AAF), numa copia do asset, sem mudar a leitura.
    """

    def __init__(self, irmas: Irmas) -> None:
        self.irmas = irmas
        self.por_track: dict[str, list[ClipIR]] = {}
        for track in irmas.origem.tracks:
            if track.kind == "audio" and not track.is_video:
                self.por_track.setdefault(track.name, []).extend(track.clips)
        self.infos: dict[str, BwfInfo | None] = {}
        self.originais: dict[str, Original | None] = {}
        self.pares: dict[tuple, bool] = {}

    def _info(self, clip: ClipIR) -> BwfInfo | None:
        """Metadados da midia do clip do editor, resolvida uma vez por asset."""
        origem = self.irmas.origem
        asset = origem.media_by_id(clip.media_id) if clip.media_id else None
        if asset is None:
            return None
        if asset.id not in self.infos:
            caminho = _midia_resolvida(asset, self.irmas.raizes, origem)
            self.infos[asset.id] = _bwf(caminho) if caminho is not None else None
        return self.infos[asset.id]

    def _original(self, fonte) -> Original | None:
        chave = str(fonte or "")
        if chave not in self.originais:
            info = _bwf(Path(chave)) if chave else None
            self.originais[chave] = original_do_arquivo(Path(chave), info) if info else None
        return self.originais[chave]

    def _candidatos(self, nomes: set) -> list[ClipIR]:
        """Clips das tracks dona e de origem; sem essas tracks no AAF, todos."""
        achados = [clip for nome in nomes for clip in self.por_track.get(nome, [])]
        return achados or [clip for lista in self.por_track.values() for clip in lista]

    def _amostras(self, clip: ClipIR, info: BwfInfo, original: Original) -> bool:
        """Mesmo take e a midia do editor igual ao canal usado do original."""
        chave = (id(clip), str(original.path))
        if chave not in self.pares:
            self.pares[chave] = _amostras_do_editor(clip, info, original)
        return self.pares[chave]

    def _par(self, clip: ClipIR, real: ClipIR, original: Original) -> bool:
        """O clip do editor produziu a irma: offset alinhado e amostras do original."""
        a, b = real.timeline_start_samples, real.timeline_end_samples
        if not (clip.timeline_start_samples < b and a < clip.timeline_end_samples):
            return False
        deslocado = (real.source_offset_samples or 0) - (a - clip.timeline_start_samples)
        if deslocado != (clip.source_offset_samples or 0):
            return False
        info = self._info(clip)
        return info is not None and self._amostras(clip, info, original)

    def confere(self, esperado: dict, real: ClipIR, dona) -> bool:
        """O clip da irma confere com o sidecar, com o gravador e com o corte do editor."""
        if not _clip_confere(esperado, real, self.irmas):
            return False
        original = self._original(esperado.get("source"))
        if original is None:
            return False
        nomes = {nome for nome in (esperado.get("editor_track"), dona) if nome}
        return any(self._par(clip, real, original) for clip in self._candidatos(nomes))


def _amostras_do_editor(clip: ClipIR, info: BwfInfo, original: Original) -> bool:
    """A midia do clip do editor e do take e tem, no trecho, o canal que ele usa."""
    if not mesmo_take(clip, info, original):
        return False
    posicao = entrada_do_clip(clip, info, original)
    return posicao is not None and canal_perto(clip, info, original.path, posicao)


def _bwf(caminho: Path) -> BwfInfo | None:
    try:
        return read_bwf(caminho)
    except (ValueError, OSError):
        return None


def _midia_resolvida(asset: MediaAsset, raizes: list[Path], sessao: SessionIR) -> Path | None:
    """Caminho da midia do asset, procurada numa copia dele; None quando nao acha."""
    copia = copy.deepcopy(asset)
    if copia.status != "resolved" or not copia.resolved_path:
        try:
            resolve_asset(copia, raizes, Report(strict=False), taxa_tc=sessao.timecode_rate)
        except Exception:  # noqa: BLE001 - midia que nao resolve nao confere
            return None
    if copia.status != "resolved" or not copia.resolved_path:
        return None
    caminho = Path(copia.resolved_path)
    return caminho if caminho.is_file() else None


def _conferir_gravador(resultado, irmas: Irmas) -> str:
    """Confere cada clip das irmas contra o gravador e diz quantos foram conferidos."""
    corte = _Corte(irmas)
    total, falhas = 0, []
    for track in irmas.removidas:
        dados = irmas.dados["tracks"][track.name]
        esperados = sorted(dados.get("clips") or [], key=lambda c: (c["start"], c["end"]))
        reais = _ordenados(track)
        for indice in range(max(len(esperados), len(reais))):
            total += 1
            ok = indice < len(esperados) and indice < len(reais)
            if not (ok and corte.confere(esperados[indice], reais[indice], dados.get("owner"))):
                falhas.append(f"{track.name} #{indice + 1}")
    texto = None
    if falhas:
        citados = ", ".join(falhas[:MAX_NOMES])
        texto = f"{len(falhas)} sibling clips differ from the production audio: {citados}"
        resultado.errors.append(texto)
    # A contagem medida, para o veredito do app.
    resultado.sibling_clips = {"checked": total, "differ": len(falhas), "text": texto}
    return (
        f"Siblings: {total} sibling clips checked against the production audio "
        f"({len(falhas)} differ)"
    )
