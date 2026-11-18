"""As pecas do verify: o unico predicado de correspondencia dos dois cortes.

O verify nao importa o diff: ele monta as proprias pecas, casa os planos por
frame (com a velocidade do plano) e resolve por ordem o material repetido.
Este conjunto e o que diz para onde vai cada pedaco do set de origem: o plano
que casa vai para o corte novo, o que nao casa vai para a track [saiu].
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from fractions import Fraction
from pathlib import Path

from aafton.als_xml import get_value, load_als
from aafton.cut_ir import CutEvent, CutIR
from aafton.reconform_apply import apply_reconform
from aafton.reconform_diff import diff_cuts
from aafton.reconform_verify import _Ctx, _oraculo, verify_reconform
from aafton.reconform_verify_pecas import montar_pecas
from aafton.reconform_verify_velocidade import Peca
from aafton.report import Report
from tests.unit.test_reconform_fuzz import (
    _confere,
    _faixas as _faixas_do_set,
    _gen,
    _problemas,
    _set_do_fuzz,
    _wav,
)
from tests.unit.test_reconform_revisao12 import _cortes_aleatorios, _tracks_aleatorios
from tests.unit.test_reconform_tracks import HOUR_F25, RATE_25, _make_set, _track_spec

# Samples por frame na taxa de 25 fps com 48 kHz.
SPF = 1920


def _plano(
    idx: int,
    fonte: str,
    rec_in: int,
    rec_out: int,
    tc_in: int,
    speed: Fraction | None = Fraction(1),
    whole: bool = False,
) -> CutEvent:
    """Evento de clip do corte: o TC de origem anda com a velocidade do plano."""
    fator = int(speed) if speed is not None and speed.denominator == 1 else 1
    return CutEvent(
        idx,
        "clip",
        fonte,
        None,
        tc_in,
        tc_in + (rec_out - rec_in) * fator,
        rec_in,
        rec_out,
        speed=speed,
        whole_only=whole,
    )


def _lacuna(idx: int, rec_in: int, rec_out: int) -> CutEvent:
    """Evento de lacuna de V1 do corte: o som do set nessa parte nao tem imagem."""
    return CutEvent(idx, "gap", None, None, None, None, rec_in, rec_out)


def _corte(eventos: list[CutEvent], start: int = 0) -> CutIR:
    """Corte em memoria na taxa de 25 fps."""
    return CutIR("cut.edl", RATE_25, start, eventos)


def _contexto(antigo: CutIR, novo: CutIR) -> _Ctx:
    """Contexto do verify com as pecas montadas a partir dos dois cortes."""
    return _Ctx(old=antigo, new=novo, sample_rate=48000, pecas=montar_pecas(antigo, novo))


def _faixas(pecas: Sequence[Peca]) -> list[tuple[int, int, int]]:
    """As faixas das pecas: inicio e fim no corte antigo, inicio no novo."""
    return [(peca.old_in, peca.old_out, peca.new_in) for peca in pecas]


def _samples(frame: int) -> int:
    """A posicao em samples de um frame do corte."""
    return frame * SPF


def _aplica(
    tmp_path: Path, antigo: CutIR, novo: CutIR, frames: tuple[int, int]
) -> tuple[Path, Path]:
    """Aplica o reconform sobre um set de teste e devolve o set e a copia."""
    set_path = _make_set(tmp_path, [_track_spec(0, "Voz", frames)], bpm=60.0)
    mudancas = diff_cuts(antigo, novo, Report(strict=False))
    res = apply_reconform(set_path, mudancas, Report(strict=False))
    assert res.written
    return set_path, res.output


def _arredondado(clips: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Clips com as posicoes arredondadas: a grade de ticks do Live tem resto."""
    return [(round(inicio, 4), round(fim, 4)) for inicio, fim in clips]


def test_plano_a_2x_casa_o_trecho_inteiro() -> None:
    """O plano a 2x casa pelos frames: a origem anda dois frames por frame."""
    antigo = _corte([_plano(0, "A", 0, 100, 1000, speed=Fraction(2))])
    novo = _corte([_plano(0, "A", 0, 100, 1000, speed=Fraction(2))])

    ctx = _contexto(antigo, novo)
    assert _faixas(ctx.pecas.pecas) == [(0, 100, 0)]
    peca = ctx.pecas.pecas[0]
    # A origem do frame de gravacao 10 e o frame 1020 do material de origem.
    assert peca.tc_in == 1000
    assert peca.tc_out == 1200
    assert ctx.pecas.novo_de(10) == 10


def test_2x_contra_1x_nao_casa_e_o_trecho_vai_para_o_saiu() -> None:
    """Mesma origem com velocidades diferentes nao casa: o trecho vai para [saiu]."""
    antigo = _corte([_plano(0, "A", 0, 100, 1000, speed=Fraction(2))])
    novo = _corte([_plano(0, "A", 0, 100, 1000)])

    ctx = _contexto(antigo, novo)
    assert ctx.pecas.pecas == ()
    assert ctx.pecas.novo_de(10) is None
    assert _oraculo(ctx, _samples(10))[0] == "saiu"


def test_plano_inteiro_casa_com_outro_plano_inteiro() -> None:
    """O plano inteiro so casa com outro plano inteiro da mesma origem."""
    antigo = _corte([_plano(0, "A", 0, 100, 1000, speed=None, whole=True)])
    novo = _corte([_plano(0, "A", 50, 150, 1000, speed=None, whole=True)])

    ctx = _contexto(antigo, novo)
    assert _faixas(ctx.pecas.pecas) == [(0, 100, 50)]


def test_plano_inteiro_nao_casa_com_plano_normal() -> None:
    """Plano inteiro contra plano normal nao casa: o trecho vai para [saiu]."""
    antigo = _corte([_plano(0, "A", 0, 100, 1000, speed=None, whole=True)])
    novo = _corte([_plano(0, "A", 0, 100, 1000)])

    ctx = _contexto(antigo, novo)
    assert ctx.pecas.pecas == ()
    assert _oraculo(ctx, _samples(10))[0] == "saiu"


def test_empate_fica_sem_correspondencia_e_listado() -> None:
    """Os candidatos empatados nao casam: o trecho fica sem correspondencia."""
    antigo = _corte([_plano(0, "X", 0, 10, 0), _plano(1, "Z", 100, 200, 0)])
    novo = _corte(
        [
            _plano(0, "X", 250, 260, 0),
            _plano(1, "Z", 300, 400, 0),
            _plano(2, "Z", 400, 500, 0),
        ]
    )

    ctx = _contexto(antigo, novo)
    # So o X casa: os dois Z empatados nao tem correspondencia.
    assert _faixas(ctx.pecas.pecas) == [(0, 10, 250)]
    assert ctx.pecas.novo_de(150) is None
    assert _oraculo(ctx, _samples(150))[0] == "saiu"
    nao_resolvidos = ctx.pecas.nao_resolvidos
    assert len(nao_resolvidos) == 1
    assert (nao_resolvidos[0].old_in, nao_resolvidos[0].old_out) == (100, 200)
    assert (nao_resolvidos[0].new_in, nao_resolvidos[0].new_out) == (300, 500)
    assert nao_resolvidos[0].source == "Z"


def test_plano_repetido_no_antigo_perde_a_segunda_vez_e_vai_para_o_saiu(tmp_path: Path) -> None:
    """O mesmo plano duas vezes no corte antigo: a segunda vez vai para [saiu]."""
    antigo = _corte(
        [
            _plano(0, "A", HOUR_F25, HOUR_F25 + 20, 0),
            _plano(1, "A", HOUR_F25 + 20, HOUR_F25 + 40, 0),
        ],
        HOUR_F25,
    )
    novo = _corte([_plano(0, "A", HOUR_F25, HOUR_F25 + 20, 0)], HOUR_F25)

    set_path, aplicado = _aplica(tmp_path, antigo, novo, (0, 40))
    resultado = verify_reconform(antigo, novo, set_path, aplicado, Report(strict=False))
    assert resultado.ok, resultado.problems
    # A segunda vez do plano vai para a emenda da primeira: 20 frames.
    faixas = _faixas_do_set(aplicado)
    assert _arredondado(faixas["Voz [saiu]"]) == [(0.8, 1.6)]


def test_audio_ao_lado_de_um_plano_empatado_vai_para_o_saiu(tmp_path: Path) -> None:
    """O audio que cobre o plano empatado vai para [saiu], na emenda do plano casado."""
    antigo = _corte(
        [
            _plano(0, "X", HOUR_F25, HOUR_F25 + 5, 0),
            _lacuna(1, HOUR_F25 + 5, HOUR_F25 + 25),
            _plano(2, "Z", HOUR_F25 + 25, HOUR_F25 + 45, 0),
        ],
        HOUR_F25,
    )
    novo = _corte(
        [
            _plano(0, "X", HOUR_F25 + 50, HOUR_F25 + 55, 0),
            _plano(1, "Z", HOUR_F25 + 65, HOUR_F25 + 85, 0),
            _plano(2, "Z", HOUR_F25 + 85, HOUR_F25 + 105, 0),
        ],
        HOUR_F25,
    )

    set_path, aplicado = _aplica(tmp_path, antigo, novo, (0, 45))
    resultado = verify_reconform(antigo, novo, set_path, aplicado, Report(strict=False))
    assert resultado.ok, resultado.problems
    faixas = _faixas_do_set(aplicado)
    # O Z vai para a emenda: o fim da peca do X. O X e a lacuna de V1 ficam
    # juntos na track de origem, no lugar do X no corte novo.
    assert _arredondado(faixas["Voz [saiu]"]) == [(2.2, 3.0)]
    assert _arredondado(faixas["Voz"]) == [(2.0, 3.0)]


def test_marcador_rc_delete_fica_na_emenda_do_ultimo_plano(tmp_path: Path) -> None:
    """O marcador 'RC delete' do trecho removido fica na emenda do ultimo plano.

    A lacuna de V1 entre o plano guardado e o trecho removido nao muda a
    emenda: ela e o fim da ultima peca do verify, como no mapa de tempo.
    """
    antigo = _corte(
        [
            _plano(0, "A", HOUR_F25, HOUR_F25 + 100, 100),
            _lacuna(1, HOUR_F25 + 100, HOUR_F25 + 150),
            _plano(2, "B", HOUR_F25 + 150, HOUR_F25 + 250, 300),
        ],
        HOUR_F25,
    )
    novo = _corte([_plano(0, "A", HOUR_F25, HOUR_F25 + 100, 100)], HOUR_F25)

    set_path, aplicado = _aplica(tmp_path, antigo, novo, (0, 100))
    resultado = verify_reconform(antigo, novo, set_path, aplicado, Report(strict=False))
    assert resultado.ok, resultado.problems
    marcadores = [
        (get_value(loc, "Name") or "", round(float(get_value(loc, "Time") or "0"), 4))
        for loc in load_als(aplicado).findall(".//LiveSet/Locators/Locators/Locator")
        if (get_value(loc, "Name") or "").startswith("RC ")
    ]
    # A emenda e o fim do plano A: 100 frames depois do inicio do set.
    assert marcadores == [("RC delete 01:00:06:00", 4.0)]


def test_cortes_aleatorios_passam_no_verify(tmp_path: Path) -> None:
    """Cortes aleatorios com lacuna, trim e reordem passam no verify."""
    ruins = []
    wav = _wav(tmp_path)
    for seed in range(60):
        rng = random.Random(seed)
        eventos_antigos, eventos_novos, total = _gen(rng)
        a = rng.randint(1, max(2, total - 4))
        b = rng.randint(a + 1, total + 3)
        set_path = _set_do_fuzz(tmp_path, wav, [_track_spec(0, "Voz", (a, b))], f"u{seed}.als")
        antigo = CutIR("o", RATE_25, HOUR_F25, eventos_antigos)
        novo = CutIR("n", RATE_25, HOUR_F25, eventos_novos)
        problemas = _confere(antigo, novo, set_path, (b - a) / 25.0)
        if problemas:
            ruins.append((seed, (a, b), problemas))
    assert not ruins, ruins[:5]


def test_cortes_aleatorios_com_plano_cortado_passam_no_verify(tmp_path: Path) -> None:
    """Cortes aleatorios com o mesmo plano em dois pedacos passam no verify."""
    ruins = []
    wav = _wav(tmp_path)
    for seed in range(60):
        rng = random.Random(seed)
        eventos_antigos, eventos_novos, total = _cortes_aleatorios(rng)
        tracks, esperado = _tracks_aleatorios(rng, total)
        try:
            set_path = _set_do_fuzz(tmp_path, wav, tracks, f"s{seed}.als")
        except Exception:
            continue
        antigo = CutIR("o", RATE_25, HOUR_F25, eventos_antigos)
        novo = CutIR("n", RATE_25, HOUR_F25, eventos_novos)
        try:
            mudancas = diff_cuts(antigo, novo, Report(strict=False))
            if mudancas is None or mudancas.time_map is None:
                continue
            res = apply_reconform(set_path, mudancas, Report(strict=False))
            if not res.written:
                continue
            problemas = _problemas(_faixas_do_set(res.output), esperado / 48000)
            verificado = verify_reconform(antigo, novo, set_path, res.output, Report(strict=False))
            if not verificado.ok:
                problemas.append(("verify", verificado.problems[:2]))
            if problemas:
                ruins.append((seed, problemas))
        except Exception as exc:  # noqa: BLE001 - o fuzz reporta a falha do caso
            ruins.append((seed, "EXC", repr(exc)[:200]))
    assert not ruins, ruins[:5]
