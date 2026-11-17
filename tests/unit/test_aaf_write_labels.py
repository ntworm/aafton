"""Prova de que o AAF exportado carrega o rotulo de canal do 5.1.

O leitor ja sabe ler o rotulo que o proprio AAF declara: o nome do MasterMob
termina no canal do slot. O escritor precisa escrever essa evidencia, senao o
round-trip volta a adivinhar o layout pelo SMPTE e troca o nome das tracks.
"""

import json
import shutil
from pathlib import Path

import aaf2

from aafton.aaf_channel_labels import (
    CONJUNTO_51,
    LIMITE_DE_CLIPS,
    rotulo_do_slot,
    rotulos_do_grupo,
)
from aafton.aaf_write_labels import rotular_mastermobs
from aafton.als_read import read_session
from aafton.cli import main
from aafton.report import Report
from tests.support.paths import REPO, corpus_dir, fixture_aaf

# Os seis canais do 5.1 do Pro Tools, na ordem do export em texto da sessao.
NOMES_51 = ["5.1 [L]", "5.1 [C]", "5.1 [R]", "5.1 [Ls]", "5.1 [Rs]", "5.1 [LFE]"]

# Um segundo grupo do 5.1, com outro nome base e os mesmos seis canais.
NOMES_SURR = ["SURR [L]", "SURR [C]", "SURR [R]", "SURR [Ls]", "SURR [Rs]", "SURR [LFE]"]


class SourceClip:
    """SourceClip de mentira: a busca do leitor so precisa de .mob."""

    def __init__(self, mob):
        self.mob = mob


class Mob:
    """MasterMob de mentira: o nome e o que o leitor le."""

    def __init__(self, nome):
        self.name = nome


class Segmento:
    """Segmento de mentira: a busca desce por .components."""

    def __init__(self, components):
        self.components = components


class Slot:
    """Slot de timeline de mentira: o leitor so precisa de .segment."""

    def __init__(self, segment):
        self.segment = segment


def _grupo(nomes_track, nomes_mob=None):
    """Slots de mentira para os nomes de track dados, com um mob cada."""
    if nomes_mob is None:
        nomes_mob = [f"audio_{indice}" for indice, _ in enumerate(nomes_track)]
    slots = []
    mobs = []
    for nome_track, nome_mob in zip(nomes_track, nomes_mob):
        mob = Mob(nome_mob)
        slots.append((Slot(Segmento([SourceClip(mob)])), nome_track))
        mobs.append(mob)
    return slots, mobs


def _codigos(report):
    """Codigos das issues do relatorio, na ordem em que foram emitidos."""
    return [issue.code for issue in report.issues]


def test_grupo_completo_renomeia_cada_mob():
    """O grupo completo do 5.1 escreve o rotulo no nome do MasterMob."""
    slots, mobs = _grupo(NOMES_51)
    report = Report(strict=False)

    rotular_mastermobs(slots, report)

    assert [mob.name for mob in mobs] == [
        "audio_0.L",
        "audio_1.C",
        "audio_2.R",
        "audio_3.Ls",
        "audio_4.Rs",
        "audio_5.LFE",
    ]
    assert _codigos(report) == ["WRITE_CHANNEL_LABELS_CARRIED"]
    assert report.issues[0].context["track"] == "5.1"
    assert report.issues[0].context["labels"] == list(CONJUNTO_51)


def test_nome_que_ja_tem_o_rotulo_fica_como_esta():
    """O mob que ja termina no rotulo nao ganha o rotulo duas vezes."""
    slots, mobs = _grupo(NOMES_51, ["audio.L", "b", "c", "d", "e", "f"])
    report = Report(strict=False)

    rotular_mastermobs(slots, report)

    assert mobs[0].name == "audio.L"
    assert mobs[1].name == "b.C"
    assert _codigos(report) == ["WRITE_CHANNEL_LABELS_CARRIED"]


def test_grupo_de_cinco_nao_entra():
    """Cinco slots nao sao o 5.1: nada e renomeado e nada e reportado."""
    slots, mobs = _grupo(NOMES_51[:5])
    report = Report(strict=False)

    rotular_mastermobs(slots, report)

    assert [mob.name for mob in mobs] == [
        "audio_0",
        "audio_1",
        "audio_2",
        "audio_3",
        "audio_4",
    ]
    assert report.issues == []


def test_rotulo_duplicado_nao_entra():
    """Dois slots com o mesmo rotulo nao formam o 5.1."""
    nomes = ["5.1 [L]", "5.1 [L]", "5.1 [R]", "5.1 [Ls]", "5.1 [Rs]", "5.1 [LFE]"]
    slots, mobs = _grupo(nomes)
    report = Report(strict=False)

    rotular_mastermobs(slots, report)

    assert [mob.name for mob in mobs] == [
        "audio_0",
        "audio_1",
        "audio_2",
        "audio_3",
        "audio_4",
        "audio_5",
    ]
    assert report.issues == []


def test_grupo_de_sete_nao_entra():
    """Sete slots nao sao o 5.1: nada e renomeado e nada e reportado."""
    nomes = NOMES_51 + ["5.1 [LFE]"]
    slots, mobs = _grupo(nomes)
    report = Report(strict=False)

    rotular_mastermobs(slots, report)

    assert [mob.name for mob in mobs] == [f"audio_{indice}" for indice in range(7)]
    assert report.issues == []


def test_slot_sem_rotulo_fica_intacto():
    """Um slot sem rotulo de canal tira o grupo do 5.1."""
    nomes = ["5.1 [L]", "5.1 [C]", "5.1 [R]", "5.1 [Ls]", "5.1 [Rs]", "5.1"]
    slots, mobs = _grupo(nomes)
    report = Report(strict=False)

    rotular_mastermobs(slots, report)

    assert [mob.name for mob in mobs] == [f"audio_{indice}" for indice in range(6)]
    assert report.issues == []


def test_grupo_estereo_fica_intacto():
    """O par stereo [L]/[R] nao e o 5.1 e fica como esta."""
    slots, mobs = _grupo(["STEREO [L]", "STEREO [R]"])
    report = Report(strict=False)

    rotular_mastermobs(slots, report)

    assert [mob.name for mob in mobs] == ["audio_0", "audio_1"]
    assert report.issues == []


def test_mob_compartilhado_por_dois_rotulos_nao_e_renomeado():
    """Um mob com dois rotulos nao pode ser renomeado: a evidencia se perde."""
    compartilhado = Mob("compartilhado")
    slots = [
        (Slot(Segmento([SourceClip(compartilhado)])), NOMES_51[0]),
        (Slot(Segmento([SourceClip(compartilhado)])), NOMES_51[1]),
    ]
    mobs = [compartilhado]
    for indice, nome in enumerate(NOMES_51[2:], start=2):
        mob = Mob(f"audio_{indice}")
        mobs.append(mob)
        slots.append((Slot(Segmento([SourceClip(mob)])), nome))
    report = Report(strict=False)

    rotular_mastermobs(slots, report)

    assert mobs[0].name == "compartilhado"
    assert [mob.name for mob in mobs[1:]] == [
        "audio_2.R",
        "audio_3.Ls",
        "audio_4.Rs",
        "audio_5.LFE",
    ]
    assert _codigos(report) == ["WRITE_CHANNEL_LABELS_LOST"]


def _dois_grupos_compartilhados(indice_51, indice_surr):
    """Dois grupos do 5.1 que compartilham um mob e tem os demais distintos."""
    compartilhado = Mob("compartilhado")
    mobs_51 = [Mob(f"audio_51_{indice}") for indice in range(len(NOMES_51))]
    mobs_surr = [Mob(f"audio_surr_{indice}") for indice in range(len(NOMES_SURR))]
    mobs_51[indice_51] = compartilhado
    mobs_surr[indice_surr] = compartilhado
    slots = [(Slot(Segmento([SourceClip(mob)])), nome) for nome, mob in zip(NOMES_51, mobs_51)]
    slots += [(Slot(Segmento([SourceClip(mob)])), nome) for nome, mob in zip(NOMES_SURR, mobs_surr)]
    return slots, mobs_51, mobs_surr, compartilhado


def test_mob_compartilhado_entre_dois_grupos_perde_a_evidencia_dos_dois():
    """O mob de dois grupos nao e renomeado e nenhum grupo se declara carried."""
    slots, mobs_51, mobs_surr, compartilhado = _dois_grupos_compartilhados(1, 0)
    report = Report(strict=False)

    rotular_mastermobs(slots, report)

    assert [mob.name for mob in mobs_51 + mobs_surr] == [
        "audio_51_0.L",
        "compartilhado",
        "audio_51_2.R",
        "audio_51_3.Ls",
        "audio_51_4.Rs",
        "audio_51_5.LFE",
        "compartilhado",
        "audio_surr_1.C",
        "audio_surr_2.R",
        "audio_surr_3.Ls",
        "audio_surr_4.Rs",
        "audio_surr_5.LFE",
    ]
    assert _codigos(report) == ["WRITE_CHANNEL_LABELS_LOST", "WRITE_CHANNEL_LABELS_LOST"]
    assert [issue.context["track"] for issue in report.issues] == ["5.1", "SURR"]
    assert all("two labels" in issue.context["reason"] for issue in report.issues)


def test_mob_compartilhado_com_o_mesmo_rotulo_e_renomeado_uma_vez():
    """O mob com o mesmo rotulo nos dois grupos ganha o rotulo uma vez so."""
    slots, mobs_51, mobs_surr, compartilhado = _dois_grupos_compartilhados(1, 1)
    report = Report(strict=False)

    rotular_mastermobs(slots, report)

    assert compartilhado.name == "compartilhado.C"
    assert [mob.name for mob in mobs_51 + mobs_surr] == [
        "audio_51_0.L",
        "compartilhado.C",
        "audio_51_2.R",
        "audio_51_3.Ls",
        "audio_51_4.Rs",
        "audio_51_5.LFE",
        "audio_surr_0.L",
        "compartilhado.C",
        "audio_surr_2.R",
        "audio_surr_3.Ls",
        "audio_surr_4.Rs",
        "audio_surr_5.LFE",
    ]
    assert _codigos(report) == [
        "WRITE_CHANNEL_LABELS_CARRIED",
        "WRITE_CHANNEL_LABELS_CARRIED",
    ]


def test_busca_truncada_nao_renomeia_nada():
    """Busca truncada tira a evidencia do grupo inteiro."""
    mob = Mob("audio_0")
    clips = [SourceClip(mob) for _ in range(LIMITE_DE_CLIPS + 1)]
    slots = [(Slot(Segmento(clips)), NOMES_51[0])]
    mobs = [mob]
    for indice, nome in enumerate(NOMES_51[1:], start=1):
        outro = Mob(f"audio_{indice}")
        mobs.append(outro)
        slots.append((Slot(Segmento([SourceClip(outro)])), nome))
    report = Report(strict=False)

    rotular_mastermobs(slots, report)

    assert [m.name for m in mobs] == [f"audio_{indice}" for indice in range(6)]
    assert _codigos(report) == ["WRITE_CHANNEL_LABELS_LOST"]


def _fixture_isolado(tmp_path):
    """Copia do AAF real numa pasta so dele, como no fixture pt_aaf."""
    isolado = tmp_path / "aaf_isolado"
    isolado.mkdir()
    return Path(shutil.copy2(fixture_aaf("TESTE_AFF_PROTOOLS.aaf"), isolado))


def _importa_o_aaf(origem, audio, als_destino):
    """Importa um AAF com midia ligada e confere o codigo de saida."""
    cmd_import = [
        "import",
        str(origem),
        str(als_destino),
        "--media",
        "link",
        "--audio-dir",
        str(audio),
        "--stereo",
        "split",
    ]
    codigo_import = main(cmd_import)
    assert als_destino.exists()
    assert codigo_import == 0
    return codigo_import


def _exporta_o_als(als1, aaf_out, relatorio):
    """Exporta um .als para AAF no modo permissivo e confere o codigo de saida."""
    cmd_export = ["export", str(als1), str(aaf_out), "--permissive", "--report", str(relatorio)]
    codigo_export = main(cmd_export)
    assert aaf_out.exists()
    assert codigo_export == 0
    return codigo_export


def _confere_o_grupo_51(aaf_out):
    """Confere que os slots do grupo 5.1 do AAF exportado carregam os rotulos."""
    with aaf2.open(str(aaf_out), "r") as f:
        comp = [m for m in f.content.mobs if isinstance(m, aaf2.mobs.CompositionMob)][0]
        slots_51 = [s for s in comp.slots if s.name == "5.1"]
        slots_51.sort(key=lambda s: int(s["PhysicalTrackNumber"].value))
        assert len(slots_51) == len(CONJUNTO_51)
        assert [rotulo_do_slot(s) for s in slots_51] == list(CONJUNTO_51)
        assert rotulos_do_grupo(slots_51) == list(CONJUNTO_51)


def _carry_do_relatorio(relatorio):
    """Os itens do relatorio que provam que os rotulos foram carregados."""
    evidence = json.loads(relatorio.read_text(encoding="utf-8"))
    carried = [i for i in evidence["issues"] if i["code"] == "WRITE_CHANNEL_LABELS_CARRIED"]
    assert carried
    return carried


def test_fixture_real_exporta_o_rotulo_que_o_leitor_le(tmp_path, monkeypatch):
    """O AAF exportado do fixture real carrega o rotulo que o leitor le."""
    monkeypatch.chdir(REPO)
    origem = _fixture_isolado(tmp_path)
    audio = corpus_dir("teste_aaf")
    als1 = tmp_path / "pass1.als"
    aaf_out = tmp_path / "export.aaf"
    relatorio = tmp_path / "export.json"

    _importa_o_aaf(origem, audio, als1)
    _exporta_o_als(als1, aaf_out, relatorio)

    _confere_o_grupo_51(aaf_out)

    carried = _carry_do_relatorio(relatorio)
    assert carried[0]["context"]["track"] == "5.1"
    assert carried[0]["context"]["labels"] == list(CONJUNTO_51)

    als2 = tmp_path / "pass2.als"
    _importa_o_aaf(aaf_out, audio, als2)
    nomes_1 = [t.name for t in read_session(als1, Report(strict=False)).tracks]
    nomes_2 = [t.name for t in read_session(als2, Report(strict=False)).tracks]
    assert nomes_1 == nomes_2
    assert "5.1 [C]" in nomes_1
