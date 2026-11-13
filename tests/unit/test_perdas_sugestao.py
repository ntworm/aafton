"""Sugestao de origem do Live: o limite de cima e o frame que a importacao aceita.

O primeiro clip pode comeca no meio de um frame. O limite de cima da origem
sugerida e o maior frame que a importacao aceita, o mesmo que ela usa para
recusar a origem com CLIP_BEFORE_ORIGIN: nao o frame do clip arredondado
para baixo.
"""

from __future__ import annotations

import json
from pathlib import Path

from aafton.cli import main
from aafton.ltc_track import add_ltc_track
from aafton.report import Report
from tests.aaf_factory import build_linked_aaf
from tests.unit.test_acab_sugestao_origem import QUARTO_DE_FRAME, _sessao_df, _wav
from tests.unit.test_acomp_ltc_textos import SR, _issue, _sessao

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "templates" / "live12.als"

# A 29,97 fps e 48 kHz um frame tem 1601,6 samples. O clip comeca no sample
# 59259, e o frame 37 comeca em 59259,2: a importacao aceita a origem no
# frame 37 porque o inicio arredondado do frame cabe no clip.
INICIO = 59259
LIMITE = "00:00:01;07"
DEPOIS = "00:00:01;08"


def _aaf_df(tmp_path, wav, inicio):
    """AAF de um clip na posicao pedida, em 29,97 drop frame."""
    return build_linked_aaf(
        tmp_path / "cena_import.aaf",
        [(inicio, 15 * SR, 0)],
        wav,
        start_timecode_frames=0,
        fps=30,
        drop=True,
    )


def _importar(aaf, saida, relatorio, live_origin):
    """Importa o AAF com a origem pedida e devolve o codigo e o relatorio."""
    codigo = main(
        [
            "import",
            str(aaf),
            str(saida),
            "--template",
            str(TEMPLATE),
            "--media",
            "link",
            "--live-origin",
            live_origin,
            "--permissive",
            "--report",
            str(relatorio),
        ]
    )
    dados = json.loads(relatorio.read_text(encoding="utf-8"))
    return codigo, dados["issues"]


def test_o_limite_de_cima_da_sugestao_e_o_frame_que_a_importacao_aceita(tmp_path):
    """A origem sugerida para no frame que a importacao aceita."""
    wav = _wav(tmp_path)
    sessao = _sessao_df(tmp_path, wav, INICIO, 15 * SR)
    relatorio = Report(strict=False)

    add_ltc_track(sessao, -10 * SR, tmp_path / "midia", relatorio)

    aviso = _issue(relatorio, "MTC_CHASE_ORIGIN_NEGATIVE")
    assert LIMITE in aviso.message
    assert "00:00:01;06" not in aviso.message


def test_a_importacao_aceita_a_origem_no_limite_sugerido(tmp_path):
    """O --live-origin do limite de cima nao reprova a importacao."""
    wav = _wav(tmp_path)
    aaf = _aaf_df(tmp_path, wav, INICIO)
    saida = tmp_path / "cena.als"

    codigo, issues = _importar(aaf, saida, tmp_path / "relatorio.json", LIMITE)

    assert not [i for i in issues if i["code"] == "CLIP_BEFORE_ORIGIN"]
    assert codigo == 0


def test_o_frame_depois_do_limite_e_recusado_pela_importacao(tmp_path):
    """O limite de cima e o maior frame que a importacao aceita."""
    wav = _wav(tmp_path)
    aaf = _aaf_df(tmp_path, wav, INICIO)
    saida = tmp_path / "cena.als"

    codigo, issues = _importar(aaf, saida, tmp_path / "relatorio.json", DEPOIS)

    assert [i for i in issues if i["code"] == "CLIP_BEFORE_ORIGIN"]
    assert codigo == 1


def test_o_quarto_de_frame_continua_dando_o_frame_zero(tmp_path):
    """Um clip a 0,75 frame continua com o frame zero como limite de cima."""
    wav = _wav(tmp_path)
    sessao = _sessao(tmp_path, wav, QUARTO_DE_FRAME, 15 * SR)
    relatorio = Report(strict=False)

    add_ltc_track(sessao, -10 * SR, tmp_path / "midia", relatorio)

    aviso = _issue(relatorio, "MTC_CHASE_ORIGIN_NEGATIVE")
    assert "and 00:00:00:00 (the first clip)" in aviso.message
    assert "00:00:00:01" not in aviso.message
