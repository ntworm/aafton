"""Setima revisao do reconform: emenda com lacuna, shift uniforme e [saiu] 2."""

from __future__ import annotations

import dataclasses
import xml.etree.ElementTree as ET
from pathlib import Path

from aafton.als_paths import TRACK_FIELDS
from aafton.als_xml import get_value, load_als
from aafton.cut_ir import CutEvent, CutIR
from aafton.reconform_apply import apply_reconform
from aafton.reconform_diff import diff_cuts
from aafton.reconform_map import Saiu
from aafton.reconform_report import change_list_text
from aafton.reconform_verify import verify_reconform
from aafton.report import Report
from tests.unit.test_reconform_apply import FRAME_25, HOUR, _xml_locators
from tests.unit.test_reconform_tracks import (
    HOUR_F25,
    RATE_25,
    SPF_25,
    _ev_pairs,
    _find_env,
    _make_set,
    _track_spec,
)


def _clip(idx: int, fonte: str, rec_in: int, rec_out: int, src_in: int) -> CutEvent:
    """Evento de clip do corte, com o TC de origem seguindo a duracao."""
    return CutEvent(idx, "clip", fonte, None, src_in, src_in + (rec_out - rec_in), rec_in, rec_out)


def _lacuna(idx: int, rec_in: int, rec_out: int) -> CutEvent:
    """Evento de lacuna de V1 do corte."""
    return CutEvent(idx, "gap", None, None, None, None, rec_in, rec_out)


def _tem_ponto(pontos: list[tuple[float, str]], tempo: float, valor: str) -> bool:
    """Verdadeiro quando o envelope tem um evento com o valor na posicao."""
    return any(abs(t - tempo) < 1e-6 and v == valor for t, v in pontos)


def _volume_dos_eventos(track_node: ET.Element) -> list[tuple[float, str]]:
    """Eventos do envelope de Volume de uma track, lidos do XML."""
    alvo = track_node.find("DeviceChain/Mixer/Volume/AutomationTarget")
    assert alvo is not None
    env = _find_env(track_node, alvo.get("Id", ""))
    assert env is not None
    return _ev_pairs(env)


def test_emenda_com_lacuna_de_v1_fica_no_fim_do_plano_mantido(tmp_path: Path) -> None:
    """Lacuna de V1 antes do plano removido: a emenda e o marker RC delete ficam no fim de A."""
    antigo = CutIR(
        "old.edl",
        RATE_25,
        HOUR_F25,
        [
            _clip(0, "A", HOUR_F25, HOUR_F25 + 100, 100),
            _lacuna(1, HOUR_F25 + 100, HOUR_F25 + 150),
            _clip(2, "B", HOUR_F25 + 150, HOUR_F25 + 250, 300),
        ],
    )
    novo = CutIR(
        "new.edl",
        RATE_25,
        HOUR_F25,
        [_clip(0, "A", HOUR_F25, HOUR_F25 + 100, 100)],
    )
    cl = diff_cuts(antigo, novo, Report())
    assert cl is not None
    assert cl.time_map is not None
    # A emenda do trecho removido fica no fim de A, e nao no fim da lacuna.
    assert cl.time_map.saiu == [Saiu(HOUR_F25 + 150, HOUR_F25 + 250, HOUR_F25 + 100)]

    set_path = _make_set(tmp_path, [_track_spec(0, "Voz", (0, 100))], bpm=60.0)
    res = apply_reconform(set_path, cl, Report())
    assert res.written
    assert res.output is not None
    resultado = verify_reconform(antigo, novo, set_path, res.output, Report(strict=False))
    assert resultado.ok, resultado.problems

    locators = _xml_locators(load_als(res.output))
    rc = [(nome, pos) for nome, pos, _ in locators if nome.startswith("RC ")]
    assert rc == [("RC delete 01:00:06:00", HOUR + 100 * FRAME_25)]


def test_corte_novo_deslocado_marca_um_shift_no_primeiro_plano() -> None:
    """Corte novo todo deslocado: um 'shift' no primeiro plano e a linha de inicio."""
    antigo = CutIR(
        "old.edl",
        RATE_25,
        HOUR_F25,
        [
            _clip(0, "A", HOUR_F25, HOUR_F25 + 100, 100),
            _clip(1, "B", HOUR_F25 + 100, HOUR_F25 + 200, 300),
        ],
    )
    novo = CutIR(
        "new.edl",
        RATE_25,
        HOUR_F25 + 50,
        [
            _clip(0, "A", HOUR_F25 + 50, HOUR_F25 + 150, 100),
            _clip(1, "B", HOUR_F25 + 150, HOUR_F25 + 250, 300),
        ],
    )
    cl = diff_cuts(antigo, novo, Report())
    assert cl is not None
    assert cl.counts == {"shift": 1}
    assert "Sequence start: 01:00:00:00 -> 01:00:02:00" in change_list_text(cl)


def test_automacao_de_um_trecho_removido_vai_para_a_sua_track_saiu(tmp_path: Path) -> None:
    """Dois trechos removidos colidindo: cada um leva a automacao para a sua track [saiu]."""
    antigo = CutIR(
        "old.edl",
        RATE_25,
        HOUR_F25,
        [
            _clip(0, "A", HOUR_F25, HOUR_F25 + 20, 100),
            _clip(1, "X1", HOUR_F25 + 20, HOUR_F25 + 40, 500),
            _clip(2, "B", HOUR_F25 + 40, HOUR_F25 + 50, 200),
            _clip(3, "X2", HOUR_F25 + 50, HOUR_F25 + 75, 600),
            _clip(4, "C", HOUR_F25 + 75, HOUR_F25 + 85, 300),
            _clip(5, "X3", HOUR_F25 + 85, HOUR_F25 + 95, 700),
        ],
    )
    novo = CutIR(
        "new.edl",
        RATE_25,
        HOUR_F25,
        [
            _clip(0, "A", HOUR_F25, HOUR_F25 + 20, 100),
            _clip(1, "B", HOUR_F25 + 20, HOUR_F25 + 30, 200),
            _clip(2, "C", HOUR_F25 + 30, HOUR_F25 + 40, 300),
        ],
    )
    ganhos = [(HOUR + 25 * SPF_25, 1.0), (HOUR + 60 * SPF_25, 0.5)]
    set_path = _make_set(tmp_path, [_track_spec(0, "Voz", (0, 95), gain_points=ganhos)], bpm=60.0)
    cl = diff_cuts(antigo, novo, Report())
    assert cl is not None
    res = apply_reconform(set_path, cl, Report())
    assert res.written
    assert res.output is not None

    tracks = load_als(res.output).findall("./LiveSet/Tracks/AudioTrack")
    assert [get_value(t, TRACK_FIELDS["name"]) for t in tracks] == [
        "Voz",
        "Voz [saiu]",
        "Voz [saiu] 2",
    ]
    primeiro = _volume_dos_eventos(tracks[1])
    segundo = _volume_dos_eventos(tracks[2])
    # O ponto do primeiro trecho removido fica na track que tem o clip dele.
    assert _tem_ponto(primeiro, 1.0, "1")
    assert not _tem_ponto(segundo, 1.0, "1")
    # O ponto do segundo trecho removido fica na track [saiu] 2.
    assert _tem_ponto(segundo, 1.6, "0.5")
    assert not _tem_ponto(primeiro, 1.6, "0.5")

    resultado = verify_reconform(antigo, novo, set_path, res.output, Report(strict=False))
    assert resultado.ok, resultado.problems


def test_automacao_segue_o_trecho_quando_o_clip_comeca_no_meio(tmp_path: Path) -> None:
    """O clip do trecho removido X2 comeca no meio dele: a automacao de X2 vai
    para a track [saiu] que recebeu o clip de X2, e nao para a primeira."""
    antigo = CutIR(
        "old.edl",
        RATE_25,
        HOUR_F25,
        [
            _clip(0, "A", HOUR_F25, HOUR_F25 + 20, 100),
            _clip(1, "X1", HOUR_F25 + 20, HOUR_F25 + 40, 500),
            _clip(2, "B", HOUR_F25 + 40, HOUR_F25 + 50, 200),
            _clip(3, "X2", HOUR_F25 + 50, HOUR_F25 + 75, 600),
            _clip(4, "C", HOUR_F25 + 75, HOUR_F25 + 85, 300),
            _clip(5, "X3", HOUR_F25 + 85, HOUR_F25 + 95, 700),
        ],
    )
    novo = CutIR(
        "new.edl",
        RATE_25,
        HOUR_F25,
        [
            _clip(0, "A", HOUR_F25, HOUR_F25 + 20, 100),
            _clip(1, "B", HOUR_F25 + 20, HOUR_F25 + 30, 200),
            _clip(2, "C", HOUR_F25 + 30, HOUR_F25 + 40, 300),
        ],
    )
    ganhos = [(HOUR + 25 * SPF_25, 1.0), (HOUR + 60 * SPF_25, 0.5)]
    spec = _track_spec(0, "Voz", (0, 50), gain_points=ganhos)
    primeiro = spec.clips[0]
    segundo = dataclasses.replace(
        primeiro,
        id="c0b",
        event_index=1,
        timeline_start_samples=HOUR + 55 * SPF_25,
        timeline_end_samples=HOUR + 95 * SPF_25,
    )
    set_path = _make_set(tmp_path, [dataclasses.replace(spec, clips=[primeiro, segundo])], bpm=60.0)
    res = apply_reconform(set_path, diff_cuts(antigo, novo, Report()), Report(strict=False))
    assert res.written
    doc = load_als(res.output)
    tracks = {
        get_value(t, TRACK_FIELDS["name"]): t for t in doc.findall("./LiveSet/Tracks/AudioTrack")
    }
    # O ponto 0.5 esta no trecho X2 e vai com o clip dele para a [saiu] 2.
    assert _tem_ponto(_volume_dos_eventos(tracks["Voz [saiu] 2"]), 1.6, "0.5")
    assert not _tem_ponto(_volume_dos_eventos(tracks["Voz [saiu]"]), 1.6, "0.5")
    assert verify_reconform(antigo, novo, set_path, res.output, Report(strict=False)).ok


def test_trechos_removidos_sobrepostos_no_corte_novo_ficam_em_tracks_diferentes(
    tmp_path: Path,
) -> None:
    """X1 e X2 se sobrepoem no corte novo e seus clips nao colidem: cada trecho
    vai inteiro para a sua track [saiu], com a sua automacao, sem misturar."""
    antigo = CutIR(
        "old.edl",
        RATE_25,
        HOUR_F25,
        [
            _clip(0, "A", HOUR_F25, HOUR_F25 + 20, 100),
            _clip(1, "X1", HOUR_F25 + 20, HOUR_F25 + 40, 500),
            _clip(2, "B", HOUR_F25 + 40, HOUR_F25 + 50, 200),
            _clip(3, "X2", HOUR_F25 + 50, HOUR_F25 + 75, 600),
            _clip(4, "C", HOUR_F25 + 75, HOUR_F25 + 85, 300),
        ],
    )
    novo = CutIR(
        "new.edl",
        RATE_25,
        HOUR_F25,
        [
            _clip(0, "A", HOUR_F25, HOUR_F25 + 20, 100),
            _clip(1, "B", HOUR_F25 + 20, HOUR_F25 + 30, 200),
            _clip(2, "C", HOUR_F25 + 30, HOUR_F25 + 40, 300),
        ],
    )
    ganhos = [
        (HOUR + 22 * SPF_25, 1.0),
        (HOUR + 38 * SPF_25, 0.2),
        (HOUR + 51 * SPF_25, 0.5),
        (HOUR + 70 * SPF_25, 0.9),
    ]
    spec = _track_spec(0, "Voz", (0, 25), gain_points=ganhos)
    c1 = spec.clips[0]
    c2 = dataclasses.replace(
        c1,
        id="c0b",
        event_index=1,
        timeline_start_samples=HOUR + 45 * SPF_25,
        timeline_end_samples=HOUR + 52 * SPF_25,
    )
    c3 = dataclasses.replace(
        c1,
        id="c0c",
        event_index=2,
        timeline_start_samples=HOUR + 60 * SPF_25,
        timeline_end_samples=HOUR + 85 * SPF_25,
    )
    set_path = _make_set(tmp_path, [dataclasses.replace(spec, clips=[c1, c2, c3])], bpm=60.0)
    res = apply_reconform(set_path, diff_cuts(antigo, novo, Report()), Report(strict=False))
    assert res.written
    doc = load_als(res.output)
    tracks = {
        get_value(t, TRACK_FIELDS["name"]): t for t in doc.findall("./LiveSet/Tracks/AudioTrack")
    }
    x1 = _volume_dos_eventos(tracks["Voz [saiu]"])
    x2 = _volume_dos_eventos(tracks["Voz [saiu] 2"])
    # O ponto 0.2 e de X1; os pontos 0.5 e 0.9 sao de X2.
    assert _tem_ponto(x1, 1.52, "0.2")
    assert not _tem_ponto(x1, 1.24, "0.5") and not _tem_ponto(x1, 2.0, "0.9")
    assert _tem_ponto(x2, 1.24, "0.5") and _tem_ponto(x2, 2.0, "0.9")
    assert not _tem_ponto(x2, 1.52, "0.2")
    assert len(tracks["Voz [saiu] 2"].findall(".//AudioClip")) == 2
    assert verify_reconform(antigo, novo, set_path, res.output, Report(strict=False)).ok
