"""Aceitacao do reconform entre EDL, XML do FCP 7 e AAF.

O mesmo par sintetico de cortes de imagem e escrito nos tres formatos e a
lista de mudancas precisa ser a mesma em todos: e o que o operador ve quando
troca o formato da lista de corte no meio da producao.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aafton.cut_read import read_cut
from aafton.ltc import parse_ltc_rate
from aafton.reconform_diff import ChangeList, diff_cuts
from aafton.reconform_report import change_list_text
from aafton.report import Report
from aafton.timecode import TimecodeRate, frames_to_label
from tests.cut_factory import build_picture_cut_aaf

RATE_24 = TimecodeRate(24, 1)
RATE_23976 = parse_ltc_rate("23.976")
RATE_2997DF = parse_ltc_rate("29.97df")

# Inicio da linha de tempo: 01:00:00:00 na contagem nominal de 24 fps.
INICIO = 86400
# Quanto o clipitem do XML entra dentro do arquivo de midia.
ENTRADA_MIDIA = 12


def _clip(reel: str, source: int, record: int, length: int) -> dict:
    """Evento de clip do par sintetico, com origem e registro em frames."""
    return {"kind": "clip", "reel": reel, "source": source, "record": record, "length": length}


def _gap(record: int, length: int) -> dict:
    """Evento de lacuna do par sintetico, com registro em frames."""
    return {"kind": "gap", "reel": None, "source": None, "record": record, "length": length}


# O corte velho: R001, R002 e R003, uma lacuna de 24 frames, R004 e R005.
VELHO = [
    _clip("R001", 172800, 86400, 48),
    _clip("R002", 182400, 86448, 48),
    _clip("R003", 192000, 86496, 48),
    _gap(86544, 24),
    _clip("R004", 201600, 86568, 48),
    _clip("R005", 211200, 86616, 48),
]

# O corte novo: R002 removido, R006 depois de R001, R003 aparado em 10 frames
# na cabeca, R004 e R005 trocados de lugar e a lacuna com metade da duracao.
NOVO = [
    _clip("R001", 172800, 86400, 48),
    _clip("R006", 220800, 86448, 48),
    _clip("R003", 192010, 86496, 38),
    _gap(86534, 12),
    _clip("R005", 211200, 86546, 48),
    _clip("R004", 201600, 86594, 48),
]

# Eventos de cada corte, em (kind, source, source_in, source_out, record_in, record_out).
EVENTOS_VELHO = [
    ("clip", "R001", 172800, 172848, 86400, 86448),
    ("clip", "R002", 182400, 182448, 86448, 86496),
    ("clip", "R003", 192000, 192048, 86496, 86544),
    ("gap", None, None, None, 86544, 86568),
    ("clip", "R004", 201600, 201648, 86568, 86616),
    ("clip", "R005", 211200, 211248, 86616, 86664),
]

EVENTOS_NOVO = [
    ("clip", "R001", 172800, 172848, 86400, 86448),
    ("clip", "R006", 220800, 220848, 86448, 86496),
    ("clip", "R003", 192010, 192048, 86496, 86534),
    ("gap", None, None, None, 86534, 86546),
    ("clip", "R005", 211200, 211248, 86546, 86594),
    ("clip", "R004", 201600, 201648, 86594, 86642),
]

# Mudancas do par, em (kind, old_in, new_in, delta_frames, source).
# O delete cobre os 48 frames de R002 e os 10 frames de cabeca de R003: as
# duas partes sao vizinhas no corte velho, entao formam um trecho so e nao
# tem material de origem proprio.
MUDANCAS = [
    ("delete", 86448, None, None, None),
    ("insert", None, 86448, None, "R006"),
    ("trim_head", 86496, 86496, -10, "R003"),
    ("shift", 86506, 86496, -10, "R003"),
    ("move", 86568, 86594, 26, "R004"),
    ("shift", 86616, 86546, -70, "R005"),
]

CONTAGENS = {"delete": 1, "insert": 1, "trim_head": 1, "shift": 2, "move": 1}


def _edl(eventos: list[dict], rate: TimecodeRate, titulo: str) -> str:
    """Texto CMX 3600 do corte; a lacuna fica implicita no tempo de registro."""
    fcm = "FCM: DROP FRAME" if rate.drop_frame else "FCM: NON-DROP FRAME"
    linhas = [f"TITLE: {titulo}", fcm, ""]
    numero = 0
    for evento in eventos:
        if evento["kind"] != "clip":
            continue
        numero += 1
        origem = frames_to_label(evento["source"], rate)
        fim_origem = frames_to_label(evento["source"] + evento["length"], rate)
        registro = frames_to_label(evento["record"], rate)
        fim_registro = frames_to_label(evento["record"] + evento["length"], rate)
        linhas.append(
            f"{numero:03d}  {evento['reel']:<8} V     C        "
            f"{origem} {fim_origem} {registro} {fim_registro}"
        )
        linhas.append(f"* FROM CLIP NAME: {evento['reel']}.mov")
        linhas.append(f"* SOURCE FILE: {evento['reel']}.mov")
        linhas.append("")
    return "\n".join(linhas)


def _xml_rate(rate: TimecodeRate) -> str:
    """Bloco <rate> do XML do FCP 7 para a taxa dada."""
    ntsc = "TRUE" if rate.den == 1001 else "FALSE"
    return f"<rate><timebase>{rate.nominal_fps}</timebase><ntsc>{ntsc}</ntsc></rate>"


def _xml_clipitem(
    clip_id: str,
    reel: str,
    start: int,
    end: int,
    source: int,
    length: int,
    rate: TimecodeRate,
) -> str:
    """Bloco <clipitem> do XML do FCP 7, com o timecode de origem no arquivo."""
    rate_xml = _xml_rate(rate)
    display = "DF" if rate.drop_frame else "NDF"
    arquivo = f"{reel}.mov"
    url = f"file://localhost/C%3a/media/{arquivo}"
    tc_frame = source - ENTRADA_MIDIA
    entrada = source - tc_frame
    saida = entrada + length
    return (
        f'<clipitem id="{clip_id}"><name>{reel}</name><duration>1000</duration>{rate_xml}'
        f"<start>{start}</start><end>{end}</end><in>{entrada}</in><out>{saida}</out>"
        f'<file id="file-{clip_id}"><name>{arquivo}</name><pathurl>{url}</pathurl>'
        f"<reel><name>{reel}</name></reel>"
        f"{rate_xml}<duration>1000</duration><timecode>{rate_xml}"
        f"<string>{frames_to_label(tc_frame, rate)}</string><frame>{tc_frame}</frame>"
        f"<displayformat>{display}</displayformat></timecode></file></clipitem>"
    )


def _xml(eventos: list[dict], rate: TimecodeRate, nome: str) -> str:
    """Documento xmeml do corte; a lacuna fica implicita no espaco entre itens."""
    rate_xml = _xml_rate(rate)
    display = "DF" if rate.drop_frame else "NDF"
    corpo = []
    indice = 0
    for evento in eventos:
        if evento["kind"] != "clip":
            continue
        indice += 1
        inicio = evento["record"] - INICIO
        corpo.append(
            _xml_clipitem(
                f"v1-{indice}",
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


def _aaf(eventos: list[dict], rate: TimecodeRate, caminho: Path) -> Path:
    """AAF do corte; a lacuna vira Filler na Sequence de V1."""
    dicionario = []
    for evento in eventos:
        if evento["kind"] == "gap":
            dicionario.append({"kind": "gap", "length": evento["length"]})
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


def _escrever(caminho: Path, conteudo: str) -> Path:
    """Grava texto sintetico em arquivo UTF-8."""
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(conteudo, encoding="utf-8")
    return caminho


def _escrever_par(
    pasta: Path, nome: str, eventos: list[dict], rate: TimecodeRate
) -> dict[str, Path]:
    """Escreve o mesmo corte nos tres formatos e devolve os caminhos."""
    return {
        "edl": _escrever(pasta / f"{nome}.edl", _edl(eventos, rate, nome)),
        "xml": _escrever(pasta / f"{nome}.xml", _xml(eventos, rate, nome)),
        "aaf": _aaf(eventos, rate, pasta / f"{nome}.aaf"),
    }


def _ler(caminho: Path, rate: TimecodeRate):
    """Le um corte de imagem e devolve o CutIR, em modo estrito."""
    report = Report(strict=True)
    corte = read_cut(caminho, report, frame_rate=rate)
    assert corte is not None
    return corte


def _diff(velho: Path, novo: Path, rate: TimecodeRate) -> ChangeList:
    """Compara dois cortes e devolve a ChangeList."""
    cl = diff_cuts(_ler(velho, rate), _ler(novo, rate), Report(strict=True))
    assert cl is not None
    return cl


def _eventos(corte) -> list[tuple]:
    """Eventos do corte em (kind, source, source_in, source_out, record_in, record_out)."""
    return [
        (
            evento.kind,
            evento.source,
            evento.source_in,
            evento.source_out,
            evento.record_in,
            evento.record_out,
        )
        for evento in corte.events
    ]


def _tuplas(cl: ChangeList) -> list[tuple]:
    """Mudancas da ChangeList em (kind, old_in, new_in, delta_frames, source)."""
    return [(c.kind, c.old_in, c.new_in, c.delta_frames, c.source) for c in cl.changes]


def test_o_corte_sintetico_e_o_mesmo_nos_tres_formatos(tmp_path: Path):
    """EDL, XML do FCP 7 e AAF descrevem os mesmos eventos do mesmo corte."""
    velhos = _escrever_par(tmp_path / "velho", "velho", VELHO, RATE_24)
    novos = _escrever_par(tmp_path / "novo", "novo", NOVO, RATE_24)

    for formato in ("edl", "xml", "aaf"):
        assert _eventos(_ler(velhos[formato], RATE_24)) == EVENTOS_VELHO
        assert _eventos(_ler(novos[formato], RATE_24)) == EVENTOS_NOVO


def test_mudancas_identicas_nos_tres_formatos(tmp_path: Path):
    """A lista de mudancas do par e a mesma em EDL, XML do FCP 7 e AAF."""
    velhos = _escrever_par(tmp_path / "velho", "velho", VELHO, RATE_24)
    novos = _escrever_par(tmp_path / "novo", "novo", NOVO, RATE_24)

    listas = {}
    for formato in ("edl", "xml", "aaf"):
        cl = _diff(velhos[formato], novos[formato], RATE_24)
        assert cl.counts == CONTAGENS
        assert _tuplas(cl) == MUDANCAS
        assert cl.ambiguous is False
        assert cl.time_map is not None
        listas[formato] = _tuplas(cl)

    assert listas["edl"] == listas["xml"] == listas["aaf"]


def test_par_misturado_de_aaf_para_edl(tmp_path: Path):
    """Um par com o corte velho em AAF e o novo em EDL da a mesma lista."""
    velhos = _escrever_par(tmp_path / "velho", "velho", VELHO, RATE_24)
    novos = _escrever_par(tmp_path / "novo", "novo", NOVO, RATE_24)

    cl = _diff(velhos["aaf"], novos["edl"], RATE_24)
    assert cl.counts == CONTAGENS
    assert _tuplas(cl) == MUDANCAS


@pytest.mark.parametrize("taxa", [RATE_24, RATE_23976, RATE_2997DF])
def test_o_par_da_a_mesma_lista_de_mudancas_em_todas_as_taxas(tmp_path: Path, taxa):
    """O mesmo par da a mesma lista a 24 fps, a 23.976 e a 29.97 drop frame."""
    velhos = _escrever_par(tmp_path / "velho", "velho", VELHO, taxa)
    novos = _escrever_par(tmp_path / "novo", "novo", NOVO, taxa)

    listas = {}
    for formato in ("edl", "xml", "aaf"):
        cl = _diff(velhos[formato], novos[formato], taxa)
        assert cl.rate == taxa
        assert cl.counts == CONTAGENS
        assert _tuplas(cl) == MUDANCAS
        listas[formato] = _tuplas(cl)

    assert listas["edl"] == listas["xml"] == listas["aaf"]


def test_rotulo_de_drop_frame_no_relatorio_da_reconformacao(tmp_path: Path):
    """O relatorio da reconformacao escreve os rotulos de drop frame com ponto e virgula."""
    velhos = _escrever_par(tmp_path / "velho", "velho", VELHO, RATE_2997DF)
    novos = _escrever_par(tmp_path / "novo", "novo", NOVO, RATE_2997DF)

    texto = change_list_text(_diff(velhos["edl"], novos["edl"], RATE_2997DF))
    linha = next(linha for linha in texto.splitlines() if linha.startswith("trim_head"))
    rotulo = frames_to_label(86496, RATE_2997DF)
    assert ";" in rotulo
    assert f"old {rotulo} -> " in linha
    assert f"old {frames_to_label(86496, RATE_24)} -> " not in linha

    velhos24 = _escrever_par(tmp_path / "velho24", "velho", VELHO, RATE_24)
    novos24 = _escrever_par(tmp_path / "novo24", "novo", NOVO, RATE_24)
    texto24 = change_list_text(_diff(velhos24["edl"], novos24["edl"], RATE_24))
    linha24 = next(linha for linha in texto24.splitlines() if linha.startswith("trim_head"))
    assert f"old {frames_to_label(86496, RATE_24)} -> " in linha24


def test_reuso_do_mesmo_plano_no_corte_novo_marca_ambiguidade(tmp_path: Path):
    """O mesmo plano usado duas vezes no corte novo deixa a mudanca ambigua."""
    velho = [_clip("R001", 172800, 86400, 48)]
    novo = [_clip("R001", 172800, 86400, 48), _clip("R001", 172800, 86448, 48)]

    velhos = _escrever_par(tmp_path / "velho", "velho", velho, RATE_24)
    novos = _escrever_par(tmp_path / "novo", "novo", novo, RATE_24)

    for formato in ("edl", "xml", "aaf"):
        cl = _diff(velhos[formato], novos[formato], RATE_24)
        assert cl.ambiguous is True
        assert cl.time_map is None
        assert cl.counts == {"ambiguous": 2}


def test_plano_dividido_por_cutaway_nao_e_ambiguo(tmp_path: Path):
    """R005 partido em duas partes contiguas com um cutaway no meio nao e ambiguo."""
    velho = [_clip("R005", 211200, 86400, 48)]
    novo = [
        _clip("R005", 211200, 86400, 24),
        _clip("R009", 300000, 86424, 24),
        _clip("R005", 211224, 86448, 24),
    ]

    velhos = _escrever_par(tmp_path / "velho", "velho", velho, RATE_24)
    novos = _escrever_par(tmp_path / "novo", "novo", novo, RATE_24)

    for formato in ("edl", "xml", "aaf"):
        cl = _diff(velhos[formato], novos[formato], RATE_24)
        assert cl.ambiguous is False
        assert cl.time_map is not None
        assert cl.counts == {"insert": 1, "shift": 1}
