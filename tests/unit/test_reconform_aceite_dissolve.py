"""Aceitacao do reconform de um par com dissolve entre EDL, XML do FCP 7 e AAF.

O corte velho tem um dissolve de 24 frames entre dois planos; no corte novo o
dissolve cai para 12 frames no mesmo ponto de corte e um plano troca de lugar.
A lista de mudancas precisa ser a mesma nos tres formatos e no par misturado,
com o tempo do ponto de corte sem mudanca.
"""

from __future__ import annotations

from pathlib import Path

from aafton.reconform_diff import ChangeList, diff_cuts
from aafton.report import Report
from aafton.timecode import TimecodeRate, frames_to_label
from tests.cut_factory import build_picture_cut_aaf
from tests.unit.test_reconform_aceite import (
    INICIO,
    RATE_24,
    _clip,
    _escrever,
    _eventos,
    _ler,
    _tuplas,
    _xml_clipitem,
    _xml_rate,
)

# O dissolve do par: 24 frames no corte velho, 12 no corte novo.
DISSOLVE_VELHO = 24
DISSOLVE_NOVO = 12
# O ponto de corte do dissolve, em frames absolutos da linha do tempo.
PONTO_DE_CORTE = 86448
# O indice do plano que recebe o dissolve: e o segundo do par.
INDICE_DISSOLVE = 1
# Os tres formatos em que o mesmo corte e escrito.
FORMATOS = ("edl", "xml", "aaf")

# O corte velho: R001, R002 (com dissolve), R003 e R004.
VELHO = [
    _clip("R001", 86400, 86400, 48),
    _clip("R002", 172800, 86448, 72),
    _clip("R003", 259200, 86520, 48),
    _clip("R004", 302400, 86568, 48),
]

# O corte novo: o dissolve encolhe no mesmo ponto e R003 e R004 trocam de lugar.
NOVO = [
    _clip("R001", 86400, 86400, 48),
    _clip("R002", 172800, 86448, 72),
    _clip("R004", 302400, 86520, 48),
    _clip("R003", 259200, 86568, 48),
]

# Mudancas do par, em (kind, old_in, new_in, delta_frames, source).
MUDANCAS = [
    ("transition", 86448, 86448, -12, "R002"),
    ("move", 86520, 86568, 48, "R003"),
    ("shift", 86568, 86520, -48, "R004"),
]

CONTAGENS = {"transition": 1, "move": 1, "shift": 1}


def _edl(eventos: list[dict], dissolve: int, rate: TimecodeRate, titulo: str) -> str:
    """Texto CMX 3600 do corte; o dissolve entra como par C + D do mesmo evento.

    No EDL o dissolve e um par de linhas do mesmo evento: a linha C marca o
    ponto de corte e a linha D traz a duracao do dissolve.
    """
    fcm = "FCM: DROP FRAME" if rate.drop_frame else "FCM: NON-DROP FRAME"
    linhas = [f"TITLE: {titulo}", fcm, ""]
    for numero, evento in enumerate(eventos, start=1):
        origem = frames_to_label(evento["source"], rate)
        fim_origem = frames_to_label(evento["source"] + evento["length"], rate)
        registro = frames_to_label(evento["record"], rate)
        fim_registro = frames_to_label(evento["record"] + evento["length"], rate)
        if numero - 1 == INDICE_DISSOLVE:
            anterior = eventos[numero - 2]
            corte = frames_to_label(evento["record"], rate)
            linhas.append(
                f"{numero:03d}  {anterior['reel']:<8} V     C        "
                f"{frames_to_label(anterior['source'], rate)} "
                f"{frames_to_label(anterior['source'] + anterior['length'], rate)} "
                f"{corte} {corte}"
            )
            linhas.append(
                f"{numero:03d}  {evento['reel']:<8} V     D    {dissolve:03d} "
                f"{origem} {fim_origem} {registro} {fim_registro}"
            )
        else:
            linhas.append(
                f"{numero:03d}  {evento['reel']:<8} V     C        "
                f"{origem} {fim_origem} {registro} {fim_registro}"
            )
        linhas.append(f"* FROM CLIP NAME: {evento['reel']}.mov")
        linhas.append(f"* SOURCE FILE: {evento['reel']}.mov")
        linhas.append("")
    return "\n".join(linhas)


def _transitionitem(inicio: int, fim: int, rate_xml: str) -> str:
    """Bloco <transitionitem> do XML do FCP 7, com o dissolve centrado no corte."""
    return (
        f"<transitionitem><start>{inicio}</start><end>{fim}</end><alignment>center</alignment>"
        f"{rate_xml}"
        "<effect><name>Cross Dissolve</name><effectid>Cross Dissolve</effectid>"
        "<effectcategory>Dissolve</effectcategory><effecttype>transition</effecttype>"
        "</effect></transitionitem>"
    )


def _xml(eventos: list[dict], dissolve: int, rate: TimecodeRate, nome: str) -> str:
    """Documento xmeml do corte; o dissolve entra como transitionitem."""
    rate_xml = _xml_rate(rate)
    display = "DF" if rate.drop_frame else "NDF"
    corpo = []
    for indice, evento in enumerate(eventos):
        inicio = evento["record"] - INICIO
        if indice == INDICE_DISSOLVE:
            # O dissolve fica centrado no ponto de corte do plano que entra.
            corte = inicio - dissolve // 2
            corpo.append(_transitionitem(corte, corte + dissolve, rate_xml))
        corpo.append(
            _xml_clipitem(
                f"v1-{indice + 1}",
                evento["reel"],
                inicio,
                inicio + evento["length"],
                evento["source"],
                evento["length"],
                rate,
            )
        )
    duracao = max(evento["record"] + evento["length"] for evento in eventos) - INICIO
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="4">'
        f'<sequence id="seq-{nome}"><name>{nome}</name><duration>{duracao}</duration>{rate_xml}'
        f"<timecode>{rate_xml}<string>{frames_to_label(INICIO, rate)}</string>"
        f"<frame>{INICIO}</frame>"
        f"<displayformat>{display}</displayformat></timecode>"
        f"<media><video><track>{''.join(corpo)}</track></video></media>"
        "</sequence></xmeml>\n"
    )


def _aaf(eventos: list[dict], dissolve: int, rate: TimecodeRate, caminho: Path) -> Path:
    """AAF do corte; o dissolve vira um Transition com o cutpoint no meio.

    No AAF o dissolve cobre o fim do plano que sai e a cabeca do que entra: o
    plano anterior e aparado no ponto de corte e o seguinte comeca nele.
    """
    metade = dissolve // 2
    dicionario = []
    for indice, evento in enumerate(eventos):
        if indice == INDICE_DISSOLVE:
            dicionario.append({"kind": "transition", "length": dissolve, "cutpoint": metade})
        if indice == INDICE_DISSOLVE - 1:
            # O plano que sai do dissolve da o fim que a transicao cobre.
            dicionario.append(
                {
                    "kind": "clip",
                    "reel": evento["reel"],
                    "tape_start": evento["source"],
                    "start": 0,
                    "length": evento["length"] + metade,
                }
            )
        elif indice == INDICE_DISSOLVE:
            # O plano que entra do dissolve cede a cabeca que a transicao cobre.
            dicionario.append(
                {
                    "kind": "clip",
                    "reel": evento["reel"],
                    "tape_start": evento["source"] - metade,
                    "start": 0,
                    "length": evento["length"] + metade,
                }
            )
        else:
            dicionario.append(
                {
                    "kind": "clip",
                    "reel": evento["reel"],
                    "tape_start": evento["source"],
                    "start": 0,
                    "length": evento["length"],
                }
            )
    return build_picture_cut_aaf(
        caminho,
        dicionario,
        rate=(rate.num, rate.den),
        start_tc_frames=INICIO,
        drop_frame=rate.drop_frame,
    )


def _escrever_par(
    pasta: Path, nome: str, eventos: list[dict], dissolve: int, rate: TimecodeRate
) -> dict[str, Path]:
    """Escreve o mesmo corte nos tres formatos e devolve os caminhos."""
    return {
        "edl": _escrever(pasta / f"{nome}.edl", _edl(eventos, dissolve, rate, nome)),
        "xml": _escrever(pasta / f"{nome}.xml", _xml(eventos, dissolve, rate, nome)),
        "aaf": _aaf(eventos, dissolve, rate, pasta / f"{nome}.aaf"),
    }


def _diff(velho: Path, novo: Path, rate: TimecodeRate) -> ChangeList:
    """Compara dois cortes e devolve a ChangeList."""
    cl = diff_cuts(_ler(velho, rate), _ler(novo, rate), Report(strict=True))
    assert cl is not None
    return cl


def test_o_dissolve_e_lido_com_a_mesma_duracao_nos_tres_formatos(tmp_path: Path):
    """Cada formato le o dissolve velho com 24 frames e o novo com 12."""
    velhos = _escrever_par(tmp_path / "velho", "velho", VELHO, DISSOLVE_VELHO, RATE_24)
    novos = _escrever_par(tmp_path / "novo", "novo", NOVO, DISSOLVE_NOVO, RATE_24)

    for formato in FORMATOS:
        velho = _ler(velhos[formato], RATE_24)
        novo = _ler(novos[formato], RATE_24)
        evento_velho = velho.events[INDICE_DISSOLVE]
        evento_novo = novo.events[INDICE_DISSOLVE]
        assert evento_velho.transition_frames == DISSOLVE_VELHO
        assert evento_novo.transition_frames == DISSOLVE_NOVO


def test_o_corte_com_dissolve_tem_os_mesmos_eventos_nos_tres_formatos(tmp_path: Path):
    """Os tres formatos descrevem os mesmos eventos do corte com dissolve."""
    velhos = _escrever_par(tmp_path / "velho", "velho", VELHO, DISSOLVE_VELHO, RATE_24)
    novos = _escrever_par(tmp_path / "novo", "novo", NOVO, DISSOLVE_NOVO, RATE_24)

    for caminhos in (velhos, novos):
        limites = [_eventos(_ler(caminhos[formato], RATE_24)) for formato in FORMATOS]
        assert limites[0] == limites[1] == limites[2]


def test_mudancas_identicas_nos_tres_formatos(tmp_path: Path):
    """A lista de mudancas do par com dissolve e a mesma em EDL, XML e AAF."""
    velhos = _escrever_par(tmp_path / "velho", "velho", VELHO, DISSOLVE_VELHO, RATE_24)
    novos = _escrever_par(tmp_path / "novo", "novo", NOVO, DISSOLVE_NOVO, RATE_24)

    listas = {}
    for formato in FORMATOS:
        cl = _diff(velhos[formato], novos[formato], RATE_24)
        assert cl.counts == CONTAGENS
        assert _tuplas(cl) == MUDANCAS
        assert cl.ambiguous is False
        assert cl.time_map is not None
        listas[formato] = _tuplas(cl)

    assert listas["edl"] == listas["xml"] == listas["aaf"]


def test_par_misturado_de_aaf_para_edl(tmp_path: Path):
    """Um par com o corte velho em AAF e o novo em EDL da a mesma lista."""
    velhos = _escrever_par(tmp_path / "velho", "velho", VELHO, DISSOLVE_VELHO, RATE_24)
    novos = _escrever_par(tmp_path / "novo", "novo", NOVO, DISSOLVE_NOVO, RATE_24)

    cl = _diff(velhos["aaf"], novos["edl"], RATE_24)
    assert cl.counts == CONTAGENS
    assert _tuplas(cl) == MUDANCAS


def test_a_mudanca_de_dissolve_fica_no_mesmo_ponto_de_corte(tmp_path: Path):
    """A mudanca de dissolve sai uma vez, com o ponto de corte sem mudanca."""
    velhos = _escrever_par(tmp_path / "velho", "velho", VELHO, DISSOLVE_VELHO, RATE_24)
    novos = _escrever_par(tmp_path / "novo", "novo", NOVO, DISSOLVE_NOVO, RATE_24)

    for formato in ("edl", "xml", "aaf"):
        cl = _diff(velhos[formato], novos[formato], RATE_24)
        transicoes = [mudanca for mudanca in cl.changes if mudanca.kind == "transition"]
        assert len(transicoes) == 1
        mudanca = transicoes[0]
        assert mudanca.source == "R002"
        assert (mudanca.old_in, mudanca.new_in) == (PONTO_DE_CORTE, PONTO_DE_CORTE)
        assert mudanca.delta_frames == DISSOLVE_NOVO - DISSOLVE_VELHO
        # O tempo do ponto de corte nao muda: o mapa leva o frame a ele mesmo.
        assert cl.time_map is not None
        assert cl.time_map.map_frame(PONTO_DE_CORTE) == PONTO_DE_CORTE
