"""Ajustes no texto do EDL antes da leitura pelo adaptador do OpenTimelineIO.

O adaptador CMX 3600 ignora a linha FCM do arquivo e conta os timecodes como
nao drop frame, a nao ser que o rotulo use ';'. Aqui o texto do arquivo e
corrigido para que a contagem de drop frame aconteca, e o modo declarado no
FCM e lido do mesmo jeito.

Um arquivo que ja marca alguma label com ';' diz o que e: os rotulos sao
confiaveis como estao e nao sao reescritos.

As linhas FCM podem discordar entre si: o arquivo e lido como manda a
primeira delas e pela taxa da sessao, e o relatorio avisa que o arquivo
e contraditorio.

O adaptador cria uma track de audio por canal, na ordem do primeiro evento de
cada uma: uma EDL com os canais A, A3 e A2 chega com as tracks fora de ordem.
Aqui as tracks de audio voltam na ordem do numero do canal, sem mexer nos
eventos delas.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..report import Report
from ..timecode import DROP_FRAME_RATES, TimecodeRate

# Rotulo SMPTE de um timecode: hh:mm:ss:ff.
TEMPO = re.compile(r"(\d{2}:\d{2}:\d{2}):(\d{2})")
# Rotulo SMPTE com o separador de drop frame.
PONTO = re.compile(r"\d{2}:\d{2}:\d{2};\d{2}")
# Linha de evento do CMX 3600: numero do evento e os campos do evento.
EVENTO = re.compile(r"^\s*\d+\s+\S")
# Linha FCM, que declara o modo de contagem do arquivo.
FCM = re.compile(r"^\s*FCM:\s*(.+)$", re.IGNORECASE)
# O canal de audio de uma track do EDL: A1, A2, A3 ... A10.
CANAL = re.compile(r"^A(\d+)$")
# Marca de ordem de bytes que pode abrir um arquivo de texto.
BOM = chr(0xFEFF)


class ErroDropFrame(RuntimeError):
    """Rotulo de drop frame em sessao que nao e de drop frame."""


def texto(caminho: Path) -> str:
    """Texto do EDL, em utf-8 ou latin-1, com quebras de linha normais."""
    try:
        dados = caminho.read_bytes()
    except OSError:
        return ""
    try:
        bruto = dados.decode("utf-8")
    except UnicodeDecodeError:
        bruto = dados.decode("latin-1")
    return bruto.lstrip(BOM).replace("\r\n", "\n").replace("\r", "\n")


def preparar(bruto: str, taxa: TimecodeRate) -> str:
    """O texto do EDL pronto para o adaptador, com o modo de contagem resolvido.

    O FCM do arquivo manda: quando declara drop frame e a taxa conta drop
    frame, o EDL e lido como drop frame.
    """
    if taxa is not None and taxa.drop_frame:
        return reescrever_drop_frame(bruto)
    if fcm_drop_frame(bruto) is True:
        if (taxa.num, taxa.den) not in DROP_FRAME_RATES:
            raise ErroDropFrame("the EDL is drop frame and the session rate is not drop frame")
        return reescrever_drop_frame(bruto)
    if tem_ponto(bruto):
        raise ErroDropFrame("the EDL uses drop frame labels and the session is not drop frame")
    return bruto


def reescrever_drop_frame(bruto: str) -> str:
    """Troca por ';' o separador dos timecodes das linhas de evento.

    Um arquivo que ja marca alguma label com ';' diz o que e: reescrever
    todas as labels transformaria uma origem sem drop frame em uma origem
    de drop frame, e o registro do clip deixaria de bater com a origem.
    """
    if tem_ponto(bruto):
        return bruto
    linhas = []
    for linha in bruto.split("\n"):
        if EVENTO.match(linha):
            linha = TEMPO.sub(r"\1;\2", linha)
        linhas.append(linha)
    return "\n".join(linhas)


def tem_ponto(bruto: str) -> bool:
    """True quando alguma linha de evento usa o separador de drop frame."""
    return any(EVENTO.match(linha) and PONTO.search(linha) for linha in bruto.split("\n"))


def fcm_drop_frame(bruto: str) -> bool | None:
    """Modo de contagem declarado na linha FCM; None quando nao declara."""
    modos = fcm_modos(bruto)
    return modos[0] if modos else None


def fcm_modos(bruto: str) -> list[bool]:
    """Os modos de contagem declarados nas linhas FCM, na ordem do arquivo."""
    modos = []
    for linha in bruto.split("\n"):
        achado = FCM.match(linha)
        if achado is None:
            continue
        modo = achado.group(1).upper()
        if "NON-DROP" in modo:
            modos.append(False)
        elif "DROP FRAME" in modo:
            modos.append(True)
    return modos


def fcm_misturado(bruto: str) -> bool:
    """True quando as linhas FCM do arquivo discordam entre si."""
    return len(set(fcm_modos(bruto))) > 1


def avisar_fcm_misturado(bruto: str, report: Report, drop: bool) -> None:
    """Avisa quando o arquivo mistura linhas FCM de drop e de non-drop.

    O modo de leitura nao muda: o arquivo continua lido como manda a
    primeira linha FCM e a taxa da sessao. O aviso diz que o arquivo e
    contraditorio e em que modo ele foi lido.
    """
    if not fcm_misturado(bruto):
        return
    modo = "drop frame" if drop else "non-drop frame"
    report.warning(
        "FORMAT_EDL_FCM_MIXED",
        f"the file mixes drop frame and non-drop frame FCM lines; the EDL was read as {modo}",
        {"mode": modo},
    )


def tracks_por_canal(filhos) -> list:
    """Os filhos da timeline com as tracks de audio na ordem do canal.

    O nome da track de audio do EDL e o canal do arquivo: A1, A2, A3 ... A10.
    As tracks que nao tem canal ficam depois das outras, na ordem em que
    estavam, e os itens que nao sao track de audio nao mudam de lugar.
    """
    audio = [filho for filho in filhos if _eh_audio(filho)]
    ordenadas = sorted(audio, key=_chave_do_canal)
    fila = iter(ordenadas)
    return [next(fila) if _eh_audio(filho) else filho for filho in filhos]


def _numero_do_canal(nome: str) -> int | None:
    """O numero do canal de uma track de audio; None quando o nome nao diz."""
    achado = CANAL.match((nome or "").strip())
    return int(achado.group(1)) if achado else None


def _eh_audio(filho) -> bool:
    """True quando o filho da timeline e uma track de audio."""
    return str(getattr(filho, "kind", "")) == "Audio"


def _chave_do_canal(track) -> tuple[int, int]:
    """A chave da ordem: o numero do canal, e o sem numero vem depois."""
    numero = _numero_do_canal(getattr(track, "name", "") or "")
    return (1, 0) if numero is None else (0, numero)
