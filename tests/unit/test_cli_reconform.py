"""Subcomando reconform da CLI: change list, CSV, locators, aplicacao e verify."""

from __future__ import annotations

from pathlib import Path

import pytest

from aafton.cli import main
from aafton.ltc import parse_ltc_rate
from aafton.sidecar import read_anchor
from aafton.timecode import TimecodeRate, frames_to_label, label_to_frames
from tests.unit.test_reconform_apply import _NEW_EDL_25, _OLD_EDL_25, _build_spanning_set, _sha256
from tests.unit.test_review_robustness import make_set

pytest.importorskip("opentimelineio")

RATE_23_976 = parse_ltc_rate("23.976")
RATE_25 = parse_ltc_rate("25")
RATE_29_97DF = parse_ltc_rate("29.97df")
# Samples por frame a 23.976: 48000 * 1001 / 24000.
FRAME_23_976 = 2002
# A origem do set e o 01:00:00:00 do corte na taxa de 23.976, em samples.
ORIGEM_23_976 = 86400 * FRAME_23_976
# O par de EDL do teste mora sobre uma grade de 25 frames.
GRADE = 25

# O par empatado: o mesmo plano (R002) aparece duas vezes no corte novo e a
# ordem dos planos nao decide entre os dois candidatos. O plano R001, que sai
# do corte novo, fica no corte antigo antes dos dois.
_ANTIGO_EMPATE = "\n".join(
    [
        "TITLE: OLDTIE",
        "FCM: NON-DROP FRAME",
        "",
        "001  R001     V     C        06:00:00:00 06:00:01:00 01:00:01:00 01:00:02:00",
        "* FROM CLIP NAME: shotx.mov",
        "",
        "002  R002     V     C        02:00:00:00 02:00:02:00 01:00:02:00 01:00:04:00",
        "* FROM CLIP NAME: shota.mov",
        "",
    ]
)

_NOVO_EMPATE = "\n".join(
    [
        "TITLE: NEWTIE",
        "FCM: NON-DROP FRAME",
        "",
        "001  R002     V     C        02:00:00:00 02:00:02:00 01:00:01:00 01:00:03:00",
        "* FROM CLIP NAME: shota.mov",
        "",
        "002  R002     V     C        02:00:00:00 02:00:02:00 01:00:03:00 01:00:05:00",
        "* FROM CLIP NAME: shota.mov",
        "",
    ]
)


def _edls(tmp_path: Path, novo: str = _NEW_EDL_25) -> tuple[str, str]:
    old_p, new_p = tmp_path / "old.edl", tmp_path / "new.edl"
    old_p.write_text(_OLD_EDL_25, encoding="utf-8")
    new_p.write_text(novo, encoding="utf-8")
    return str(old_p), str(new_p)


def _edls_empate(tmp_path: Path) -> tuple[str, str]:
    """Grava o par de EDLs empatado: o mesmo plano duas vezes no corte novo."""
    old_p, new_p = tmp_path / "old.edl", tmp_path / "new.edl"
    old_p.write_text(_ANTIGO_EMPATE, encoding="utf-8")
    new_p.write_text(_NOVO_EMPATE, encoding="utf-8")
    return str(old_p), str(new_p)


def _edl_na_taxa(taxa: TimecodeRate, eventos: list[tuple[str, int, int, int, int]]) -> str:
    """Texto CMX 3600 dos eventos na taxa dada.

    Cada evento e (reel, origem, fim da origem, registro, fim do registro), em
    frames absolutos da taxa.
    """
    fcm = "FCM: DROP FRAME" if taxa.drop_frame else "FCM: NON-DROP FRAME"
    linhas = ["TITLE: CUT", fcm, ""]
    for numero, (reel, origem, fim_origem, registro, fim_registro) in enumerate(eventos, start=1):
        linhas.append(
            f"{numero:03d}  {reel:<8} V     C        "
            f"{frames_to_label(origem, taxa)} {frames_to_label(fim_origem, taxa)} "
            f"{frames_to_label(registro, taxa)} {frames_to_label(fim_registro, taxa)}"
        )
        linhas.append(f"* FROM CLIP NAME: {reel}.mov")
        linhas.append("")
    return "\n".join(linhas)


def _par_na_taxa(taxa: TimecodeRate) -> tuple[str, str]:
    """Par de EDLs na taxa dada: o novo insere um plano e apaga outro.

    Os eventos ficam sobre uma grade de 25 frames: o par desloca o corte em
    multiplos de 25 frames, e assim o tempo do Live e o do corte caem na
    mesma amostra, em qualquer taxa.
    """
    inicio = label_to_frames("01:00:00:00", taxa)
    horas = [label_to_frames(f"{hora:02d}:00:00:00", taxa) for hora in range(1, 6)]
    dois = 2 * GRADE
    velho = [
        ("R001", horas[0], horas[0] + dois, inicio, inicio + dois),
        ("R002", horas[1], horas[1] + dois, inicio + dois, inicio + 2 * dois),
        ("R003", horas[2], horas[2] + dois, inicio + 2 * dois, inicio + 3 * dois),
        ("R004", horas[3], horas[3] + dois, inicio + 3 * dois, inicio + 4 * dois),
    ]
    novo = [
        ("R001", horas[0], horas[0] + dois, inicio, inicio + dois),
        ("R005", horas[4], horas[4] + GRADE, inicio + dois, inicio + dois + GRADE),
        ("R002", horas[1], horas[1] + dois, inicio + dois + GRADE, inicio + 2 * dois + GRADE),
        ("R004", horas[3], horas[3] + dois, inicio + 2 * dois + GRADE, inicio + 3 * dois + GRADE),
    ]
    return _edl_na_taxa(taxa, velho), _edl_na_taxa(taxa, novo)


def _contagens(saida: str) -> dict[str, int]:
    """Contagens da linha Counts da lista de mudancas impressa."""
    linha = next(linha for linha in saida.splitlines() if linha.startswith("Counts: "))
    partes = linha.removeprefix("Counts: ").split(", ")
    return {parte.split("=")[0]: int(parte.split("=")[1]) for parte in partes}


def _set_em_23_976(tmp_path: Path, monkeypatch) -> Path:
    """Monta o set de 25 fps com o tempo de 23.976.

    E o mesmo construtor do set de 25 fps, com a taxa, a origem e o frame do
    Live trocados pelos de 23.976.
    """
    monkeypatch.setattr("tests.unit.test_reconform_apply.HOUR", ORIGEM_23_976)
    monkeypatch.setattr("tests.unit.test_reconform_apply.FRAME_25", FRAME_23_976)
    monkeypatch.setattr("tests.unit.test_reconform_apply.RATE_25", RATE_23_976)
    return _build_spanning_set(tmp_path)


def test_so_change_list(tmp_path: Path, capsys) -> None:
    old, new = _edls(tmp_path)
    assert main(["reconform", old, new, "--frame-rate", "25"]) == 0
    saida = capsys.readouterr().out
    assert "delete" in saida and "insert" in saida


def test_csv(tmp_path: Path) -> None:
    old, new = _edls(tmp_path)
    csv = tmp_path / "lista.csv"
    assert main(["reconform", old, new, "--frame-rate", "25", "--csv", str(csv)]) == 0
    primeira = csv.read_text(encoding="utf-8").splitlines()[0]
    assert primeira == "kind,old_in,old_out,new_in,new_out,delta_frames,source,detail"


def test_locators_numa_copia(tmp_path: Path) -> None:
    old, new = _edls(tmp_path)
    set_path = _build_spanning_set(tmp_path)
    antes = _sha256(set_path)
    assert main(["reconform", old, new, "--set", str(set_path)]) == 0
    assert (tmp_path / "review reconform locators.als").exists()
    assert _sha256(set_path) == antes


def test_apply_e_verify(tmp_path: Path) -> None:
    old, new = _edls(tmp_path)
    set_path = _build_spanning_set(tmp_path)
    antes = _sha256(set_path)
    assert main(["reconform", old, new, "--set", str(set_path), "--apply", "--verify"]) == 0
    assert (tmp_path / "review reconform.als").exists()
    assert _sha256(set_path) == antes


def test_empate_com_apply_escreve(tmp_path: Path, capsys) -> None:
    """O par empatado e aplicado e o trecho sem correspondencia fica listado."""
    old, new = _edls_empate(tmp_path)
    set_path = make_set(tmp_path)
    csv = tmp_path / "lista.csv"
    argumentos = [
        "reconform",
        old,
        new,
        "--set",
        str(set_path),
        "--apply",
        "--verify",
        "--csv",
        str(csv),
    ]
    assert main(argumentos) == 0
    assert (tmp_path / "review reconform.als").exists()
    saida = capsys.readouterr().out
    assert "unresolved | old 01:00:02:00 -> 01:00:04:00" in saida
    assert "RECONFORM_REUSE_UNRESOLVED" in saida
    linhas = csv.read_text(encoding="utf-8").splitlines()
    assert any(linha.startswith("unresolved,") for linha in linhas)


@pytest.mark.parametrize("extra", [["--apply"], ["--out", "x.als"], ["--verify"]])
def test_opcao_de_set_sem_set(tmp_path: Path, extra: list[str]) -> None:
    old, new = _edls(tmp_path)
    with pytest.raises(SystemExit) as exc:
        main(["reconform", old, new, "--frame-rate", "25", *extra])
    assert exc.value.code == 2


def test_verify_sem_apply(tmp_path: Path) -> None:
    old, new = _edls(tmp_path)
    set_path = _build_spanning_set(tmp_path)
    with pytest.raises(SystemExit) as exc:
        main(["reconform", old, new, "--set", str(set_path), "--verify"])
    assert exc.value.code == 2


def test_edl_sem_taxa(tmp_path: Path, capsys) -> None:
    old, new = _edls(tmp_path)
    assert main(["reconform", old, new]) == 1
    assert "RECONFORM_EDL_RATE_REQUIRED" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("taxa", "rotulo"),
    [(RATE_23_976, "23.976"), (RATE_25, "25"), (RATE_29_97DF, "29.97df")],
)
def test_change_list_na_taxa_do_edl(tmp_path: Path, capsys, taxa, rotulo) -> None:
    """A lista de mudancas sai com as contagens certas na taxa do EDL."""
    antigo, novo = _par_na_taxa(taxa)
    old_p, new_p = tmp_path / "old.edl", tmp_path / "new.edl"
    old_p.write_text(antigo, encoding="utf-8")
    new_p.write_text(novo, encoding="utf-8")

    assert main(["reconform", str(old_p), str(new_p), "--frame-rate", rotulo]) == 0
    saida = capsys.readouterr().out
    assert _contagens(saida) == {"insert": 1, "shift": 2, "delete": 1}
    if taxa.drop_frame:
        # O drop frame escreve os rotulos com ponto e virgula.
        assert ";" in saida
        inicio = label_to_frames("01:00:00:00", taxa)
        assert f"old {frames_to_label(inicio + 2 * GRADE, taxa)} -> " in saida


@pytest.mark.parametrize("grade", [GRADE, 13])
def test_apply_e_verify_em_23_976(tmp_path: Path, monkeypatch, grade: int) -> None:
    """O apply e o verify fecham a 23.976, com o set na mesma taxa.

    Com a grade de 13 frames um ponto de automacao cai no trecho removido e vai
    para a track [saiu], na emenda mais o deslocamento dentro do trecho.
    """
    monkeypatch.setattr("tests.unit.test_cli_reconform.GRADE", grade)
    antigo, novo = _par_na_taxa(RATE_23_976)
    old_p, new_p = tmp_path / "old.edl", tmp_path / "new.edl"
    old_p.write_text(antigo, encoding="utf-8")
    new_p.write_text(novo, encoding="utf-8")
    set_path = _set_em_23_976(tmp_path, monkeypatch)

    comando = ["reconform", str(old_p), str(new_p), "--set", str(set_path)]
    assert main(comando) == 0
    assert read_anchor(set_path).timecode_rate == RATE_23_976

    antes = _sha256(set_path)
    assert main([*comando, "--apply", "--verify"]) == 0
    assert (tmp_path / "review reconform.als").exists()
    assert _sha256(set_path) == antes
