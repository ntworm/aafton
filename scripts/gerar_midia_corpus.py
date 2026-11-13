"""Gera a midia sintetica que um AAF referencia e nao traz.

Uso:
    python scripts/gerar_midia_corpus.py AAF PASTA_DESTINO [--json RELATORIO]

Cada asset que o AAF referencia sem midia embutida vira um arquivo na pasta de
destino, com o nome exato que o AAF pede, na taxa do asset e com o numero de
canais que os clips usam. O conteudo e um clique de 5 ms de seno de 1 kHz no
comeco de cada segundo, com o canal N deslocado N ms, e silencio no resto: da
para ouvir onde cada canal entra, sem material de terceiros.

Assets que apontam para o mesmo arquivo sao atendidos pelo mesmo arquivo: os
canais sao os distintos entre os clips que o usam; o comprimento e o do maior clip.

O script nao conserta nada no produto: ele so escreve os arquivos que faltam
para o import estrito rodar de ponta a ponta sobre AAFs de terceiros.
"""

from __future__ import annotations

import argparse
import array
import json
import math
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from aafton.aaf_read import Report, read_aaf  # noqa: E402

TAXA_PADRAO = 48000
FOLGA_SEGUNDOS = 1
CLIQUE_MS = 5
CLIQUE_HZ = 1000
AMPLITUDE_CLIQUE = 9000

STATUS_GERADO = "gerado"
STATUS_EXISTENTE = "existente"
STATUS_PULADO = "pulado"

FORMATO_WAV = "wav"
FORMATO_AIFF = "aiff"
FORMATO_MXF = "mxf"
FORMATO_MP3 = "mp3"

SUFIXO_AIFF = {".aif", ".aiff"}


# --- o conteudo do clique -------------------------------------------------


def _amostras_do_canal(taxa: int, canal: int) -> array.array:
    """Um segundo do canal: um clique de 5 ms no comeco, deslocado pelo canal."""
    amostras = array.array("h", bytes(2 * taxa))
    inicio = int(round(canal * taxa / 1000))
    tamanho = max(1, int(round(CLIQUE_MS * taxa / 1000)))
    if inicio + tamanho > taxa:
        return amostras
    for i in range(tamanho):
        valor = AMPLITUDE_CLIQUE * math.sin(2 * math.pi * CLIQUE_HZ * i / taxa)
        amostras[inicio + i] = int(valor)
    return amostras


def _segundo_intercalado(taxa: int, canais: int) -> array.array:
    """Um segundo com os canais intercalados, como o PCM de um arquivo."""
    bloco = array.array("h", bytes(2 * taxa * canais))
    for canal in range(canais):
        bloco[canal::canais] = _amostras_do_canal(taxa, canal)
    return bloco


def _bytes_do_bloco(bloco: array.array, grande: bool) -> bytes:
    """Os bytes do bloco; em AIFF o PCM e grande endian."""
    if not grande:
        return bloco.tobytes()
    copia = array.array("h", bloco)
    if sys.byteorder == "little":
        copia.byteswap()
    return copia.tobytes()


def _escrever_wav(caminho: Path, canais: int, taxa: int, frames: int) -> None:
    """WAV PCM 16 bits: o clique de cada segundo e silencio no resto."""
    bloco = _segundo_intercalado(taxa, canais)
    with wave.open(str(caminho), "wb") as handle:
        handle.setnchannels(canais)
        handle.setsampwidth(2)
        handle.setframerate(taxa)
        segundos, resto = divmod(frames, taxa)
        for _ in range(segundos):
            handle.writeframes(bloco.tobytes())
        if resto:
            handle.writeframes(bloco[: resto * canais].tobytes())


def _escrever_aiff(caminho: Path, canais: int, taxa: int, frames: int) -> str | None:
    """AIFF PCM 16 bits: o mesmo conteudo do WAV, em bytes grandes."""
    try:
        import aifc
    except ImportError:
        return "modulo aifc indisponivel"
    bloco = _segundo_intercalado(taxa, canais)
    with aifc.open(str(caminho), "wb") as handle:
        handle.setnchannels(canais)
        handle.setsampwidth(2)
        handle.setframerate(taxa)
        handle.setcomptype(b"NONE", b"")
        segundos, resto = divmod(frames, taxa)
        for _ in range(segundos):
            handle.writeframes(_bytes_do_bloco(bloco, True))
        if resto:
            handle.writeframes(_bytes_do_bloco(bloco[: resto * canais], True))
    return None


# --- os formatos que precisam de ffmpeg ------------------------------------


def _opcoes_do_ffmpeg(formato: str) -> list[str]:
    """Opcoes do ffmpeg por formato: MXF em OP-Atom com 24 bits, MP3 com lame."""
    if formato == FORMATO_MXF:
        return ["-f", "mxf_opatom", "-c:a", "pcm_s24le"]
    return ["-c:a", "libmp3lame"]


def _gerar_com_ffmpeg(
    caminho: Path, formato: str, canais: int, taxa: int, frames: int
) -> str | None:
    """MXF ou MP3 pelo ffmpeg do PATH, a partir de um WAV temporario."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return "ffmpeg nao esta no PATH"
    with tempfile.TemporaryDirectory() as pasta:
        origem = Path(pasta) / "portadora.wav"
        _escrever_wav(origem, canais, taxa, frames)
        comando = [ffmpeg, "-y", "-loglevel", "error", "-i", str(origem)]
        comando += _opcoes_do_ffmpeg(formato)
        comando.append(str(caminho))
        resultado = subprocess.run(comando, capture_output=True, text=True)
    if resultado.returncode != 0:
        caminho.unlink(missing_ok=True)
        return f"ffmpeg falhou: {resultado.stderr.strip()[:200]}"
    return None


# --- a midia que cada asset pede -------------------------------------------


def _formato_do_nome(nome: str) -> str:
    """O formato pedido pelo sufixo do nome do arquivo."""
    sufixo = Path(nome).suffix.lower()
    if sufixo == ".wav":
        return FORMATO_WAV
    if sufixo in SUFIXO_AIFF:
        return FORMATO_AIFF
    if sufixo == ".mxf":
        return FORMATO_MXF
    if sufixo == ".mp3":
        return FORMATO_MP3
    return ""


def _escrever(destino: Path, formato: str, canais: int, taxa: int, frames: int) -> str | None:
    """Escreve o arquivo no formato pedido; o motivo quando nao da."""
    if formato == FORMATO_WAV:
        _escrever_wav(destino, canais, taxa, frames)
        return None
    if formato == FORMATO_AIFF:
        return _escrever_aiff(destino, canais, taxa, frames)
    if formato in (FORMATO_MXF, FORMATO_MP3):
        return _gerar_com_ffmpeg(destino, formato, canais, taxa, frames)
    return f"sufixo sem suporte: {destino.suffix}"


def _grupos_por_nome(assets: list) -> dict[str, list]:
    """Os assets agrupados pelo nome exato do arquivo que pedem."""
    grupos: dict[str, list] = {}
    for asset in assets:
        grupos.setdefault(asset.filename, []).append(asset)
    return grupos


def _clips_do_grupo(sessao, grupo: list) -> list:
    """Os clips que usam qualquer asset do grupo."""
    ids = {asset.id for asset in grupo}
    return [clip for clip in sessao.all_clips() if clip.media_id in ids]


def _canais_do_grupo(grupo: list, clips: list) -> int:
    """Canais do arquivo: os declarados, ou os canais distintos dos clips.

    O channel_index do clip e o canal dentro da track do AAF, nao o do
    arquivo: um arquivo so usado pelo canal direito e mono. O MXF e mono.
    """
    formato = _formato_do_nome(grupo[0].filename) if grupo else ""
    if formato == FORMATO_MXF:
        return 1
    declarados = [asset.channels for asset in grupo if asset.channels]
    if declarados:
        return int(max(declarados))
    if not clips:
        return 1
    return max(1, len({clip.channel_index for clip in clips}))


def _frames_do_grupo(sessao, grupo: list, clips: list, taxa: int) -> int:
    """Frames que os clips pedem na taxa do asset, com um segundo de folga."""
    fim = 0
    for clip in clips:
        fim = max(fim, (clip.source_offset_samples or 0) + clip.duration_samples)
    if not fim:
        declarados = [asset.duration_samples for asset in grupo if asset.duration_samples]
        fim = max(declarados) if declarados else 0
    if sessao.sample_rate:
        frames = math.ceil(fim * taxa / sessao.sample_rate)
    else:
        frames = fim
    return frames + taxa * FOLGA_SEGUNDOS


# --- a midia embutida no proprio AAF ---------------------------------------


def _mobs_com_essencia(caminho: Path) -> set[str]:
    """Os mob_ids dos SourceMobs que carregam a midia dentro do AAF."""
    try:
        import aaf2
    except ImportError:
        return set()
    ids: set[str] = set()
    try:
        with aaf2.open(str(caminho), "r") as arquivo:
            for mob in arquivo.content.mobs:
                if getattr(mob, "essence", None) is None:
                    continue
                mob_id = getattr(mob, "mob_id", None)
                if mob_id:
                    ids.add(str(mob_id).lower())
    except Exception as exc:  # noqa: BLE001
        print(f"gerar_midia_corpus: essencia embutida desconhecida: {exc}", file=sys.stderr)
    return ids


def _tem_essencia_embutida(asset, ids_com_essencia: set[str]) -> bool:
    """O asset traz a midia dentro do AAF: nao precisa de arquivo em disco."""
    if getattr(asset, "status", None) == "embedded":
        return True
    mob_id = (asset.embedded_mob_id or "").lower()
    return bool(mob_id) and mob_id in ids_com_essencia


# --- o relatorio -----------------------------------------------------------


def _campos(
    nome: str, formato: str, canais: int, taxa: int, frames: int, status: str, motivo: str
) -> dict:
    """Os campos de uma linha do relatorio."""
    return {
        "filename": nome,
        "sample_rate": taxa,
        "channels": canais,
        "duration_frames": frames,
        "formato": formato,
        "status": status,
        "motivo": motivo,
    }


def _escrever_relatorio(destino: Path, aaf: Path, pasta: Path, linhas: list[dict]) -> None:
    """Grava o relatorio JSON: uma linha por asset e o resumo da rodada."""
    resumo = {
        status: sum(1 for linha in linhas if linha["status"] == status)
        for status in (STATUS_GERADO, STATUS_EXISTENTE, STATUS_PULADO)
    }
    payload = {"aaf": str(aaf), "pasta": str(pasta), "assets": linhas, "resumo": resumo}
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _resumir(linhas: list[dict]) -> None:
    """Imprime o resumo da rodada e o motivo de cada pulo."""
    pulados = [linha for linha in linhas if linha["status"] == STATUS_PULADO]
    gerados = sum(1 for linha in linhas if linha["status"] == STATUS_GERADO)
    existentes = sum(1 for linha in linhas if linha["status"] == STATUS_EXISTENTE)
    print(f"gerar_midia_corpus: {gerados} gerados, {existentes} existentes, {len(pulados)} pulados")
    for linha in pulados:
        print(f"  pulado: {linha['filename']}: {linha['motivo']}")


# --- a rodada --------------------------------------------------------------


def _gerar_grupo(grupo: list, clips: list, sessao, pasta: Path) -> dict:
    """Gera o arquivo do grupo e devolve os campos que o relatorio repete."""
    nome = grupo[0].filename
    destino = pasta / nome
    formato = _formato_do_nome(nome)
    taxa = int(grupo[0].sample_rate or sessao.sample_rate or TAXA_PADRAO)
    canais = _canais_do_grupo(grupo, clips)
    frames = _frames_do_grupo(sessao, grupo, clips, taxa)
    if destino.exists():
        status, motivo = STATUS_EXISTENTE, "arquivo ja existe"
    else:
        motivo = _escrever(destino, formato, canais, taxa, frames) or ""
        status = STATUS_PULADO if motivo else STATUS_GERADO
    return _campos(nome, formato, canais, taxa, frames, status, motivo)


def _parser() -> argparse.ArgumentParser:
    """O parser da linha de comando."""
    parser = argparse.ArgumentParser(
        prog="gerar_midia_corpus",
        description="Gera a midia sintetica que um AAF referencia e nao traz.",
    )
    parser.add_argument("aaf", help="AAF de origem")
    parser.add_argument("pasta", help="pasta onde a midia e escrita")
    parser.add_argument("--json", default=None, help="caminho do relatorio JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Gera a midia que o AAF pede e devolve o codigo de saida."""
    args = _parser().parse_args(argv)
    aaf = Path(args.aaf)
    pasta = Path(args.pasta)
    if not aaf.is_file():
        print(f"gerar_midia_corpus: AAF nao encontrado: {aaf}", file=sys.stderr)
        return 2
    try:
        sessao = read_aaf(aaf, Report(strict=False))
    except Exception as exc:  # noqa: BLE001
        print(f"gerar_midia_corpus: AAF ilegivel: {exc}", file=sys.stderr)
        return 1
    ids_com_essencia = _mobs_com_essencia(aaf)
    assets = [
        asset for asset in sessao.media if not _tem_essencia_embutida(asset, ids_com_essencia)
    ]
    pasta.mkdir(parents=True, exist_ok=True)
    linhas: list[dict] = []
    for grupo in _grupos_por_nome(assets).values():
        campos = _gerar_grupo(grupo, _clips_do_grupo(sessao, grupo), sessao, pasta)
        linhas.extend(dict(campos) for _ in grupo)
    if args.json:
        _escrever_relatorio(Path(args.json), aaf, pasta, linhas)
    _resumir(linhas)
    return 1 if any(linha["status"] == STATUS_PULADO for linha in linhas) else 0


if __name__ == "__main__":
    raise SystemExit(main())
