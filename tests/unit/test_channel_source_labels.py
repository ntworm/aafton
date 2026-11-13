"""C04: o rotulo de canal vem do proprio AAF quando a fonte declara.

O Pro Tools exporta a track 5.1 como seis slots de timeline, cada um com o
seu MasterMob, e o nome do MasterMob termina no rotulo do canal do slot
(``...-08.L``, ``...-08.C``). O export em texto da sessao do Pro Tools
confirma a mesma ordem de canais. Aqui so a fixture real e o export em texto
sao lidos; nenhuma midia externa e tocada.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

from aafton.aaf_channel_labels import (
    CONJUNTO_51,
    LIMITE_DE_CLIPS,
    rotulo_do_slot,
    rotulos_do_grupo,
)
from aafton.aaf_read import read_aaf
from aafton.protools_text import ProToolsTextExport
from aafton.report import Report
from tests.support.paths import fixture_aaf

_SUFIXO = re.compile(r"\.(LFE|Ls|Rs|L|C|R)$")
_FADE = re.compile(r"^Fade\s+\d+$", re.IGNORECASE)
_CINCO_UM = "5.1 (5.1)"


def _rotulo(nome: str) -> str | None:
    """Rotulo de canal no fim de um nome, ou None."""
    achado = _SUFIXO.search(nome or "")
    return achado.group(1) if achado else None


def _sessao_real():
    """A sessao da fixture real, lida do AAF versionado."""
    return read_aaf(fixture_aaf("TESTE_AFF_PROTOOLS.aaf"), Report(strict=False))


def _tracks_51(sessao) -> dict:
    """As tracks 5.1 da sessao, pelo nome."""
    return {t.name: t for t in sessao.tracks if t.name.startswith("5.1 [")}


def _clips_de_midia(track) -> list:
    """Os clips da track que nao sao fade."""
    return [clip for clip in track.clips if not _FADE.match((clip.name or "").strip())]


def _rotulos_dos_clips(track) -> set:
    """Os rotulos de canal dos clips da track que nao sao fade."""
    return {_rotulo(clip.name) for clip in _clips_de_midia(track)}


def test_rotulo_da_track_bate_com_o_sufixo_dos_clips_nao_fade():
    """Cada track 5.1 real carrega o rotulo que os proprios clips declaram."""
    tracks = _tracks_51(_sessao_real())
    assert len(tracks) == 6
    for nome, track in tracks.items():
        assert _rotulos_dos_clips(track) == {nome.split("[", 1)[1].strip("]")}, nome


def test_rotulos_batem_com_o_export_em_texto_do_pro_tools():
    """O canal do export em texto e a track que o AAF nomeia com aquele canal."""
    export = ProToolsTextExport.parse_file(fixture_aaf("teste_aaf_protools_txt.txt"))
    tracks = _tracks_51(_sessao_real())
    for canal in range(1, 7):
        eventos = export.tracks[_CINCO_UM].clips(canal)
        rotulos = {_rotulo(evento.clip_name) for evento in eventos}
        rotulos.discard(None)
        assert len(rotulos) == 1, (canal, rotulos)
        rotulo = rotulos.pop()
        nossos = _rotulos_dos_clips(tracks[f"5.1 [{rotulo}]"])
        assert nossos == {rotulo}, (canal, rotulo)


def test_o_rotulo_da_fonte_e_registrado_no_relatorio():
    """A origem dos rotulos fica no relatorio, e so uma vez por track."""
    report = Report(strict=False)
    read_aaf(fixture_aaf("TESTE_AFF_PROTOOLS.aaf"), report)
    infos = [i for i in report.issues if i.code == "CHANNEL_LABELS_FROM_SOURCE"]
    assert len(infos) == 1
    assert infos[0].context["labels"] == ["L", "C", "R", "Ls", "Rs", "LFE"]
    adivinhados = [i for i in report.issues if i.code == "CHANNEL_LAYOUT_GUESSED"]
    assert [i.context["track"] for i in adivinhados] == ["AMB 1ST"]


class SourceClip:
    """SourceClip de mentira: so o mob interessa."""

    def __init__(self, nome: str) -> None:
        self.mob = SimpleNamespace(name=nome)


def _slot(*nomes: str):
    """Um slot de mentira, com um SourceClip por nome."""
    clips = [SourceClip(nome) for nome in nomes]
    return SimpleNamespace(segment=SimpleNamespace(components=clips))


class MobQuebrado:
    """Mob que quebra quando o nome e pedido."""

    @property
    def name(self):
        raise RuntimeError("nome quebrado")


class Grupo:
    """Operacao com segmentos dentro, como o OperationGroup do AAF."""

    def __init__(self, *segmentos) -> None:
        self.segments = list(segmentos)


def _slot_de(*clips):
    """Um slot de mentira, com os SourceClips dados."""
    return SimpleNamespace(segment=SimpleNamespace(components=list(clips)))


def test_ausencia_de_rotulo_nao_inventa_um():
    """Slot sem rotulo, ou com fade sem sufixo, nao ganha rotulo nenhum."""
    assert rotulo_do_slot(_slot()) is None
    assert rotulo_do_slot(_slot("Fade 26")) is None
    assert rotulo_do_slot(_slot("som-08", "Fade 26")) is None


def test_conflito_de_rotulo_nao_inventa_um():
    """Dois canais no mesmo slot, ou o mesmo canal em dois slots, nao viram rotulo."""
    assert rotulo_do_slot(_slot("som-08.L", "som-08.R")) is None
    grupo = [_slot(f"som-08.{rotulo}") for rotulo in ("L", "C", "R", "Ls", "Rs", "L")]
    assert rotulos_do_grupo(grupo) is None


def test_fade_sem_sufixo_nao_conta_como_evidencia():
    """O fade nao tem canal: ele nao atrapalha o rotulo do slot."""
    assert rotulo_do_slot(_slot("Fade 26", "som-08.L", "Fade 27")) == "L"


def test_clip_sem_rotulo_derruba_a_evidencia_do_slot():
    """Um SourceClip que nao e fade e nao declara rotulo derruba o slot.

    A evidencia do slot e inteira: um clip sem rotulo deixa de ser evidencia
    de canal, e quem decide passa a ser o layout padrao.
    """
    assert rotulo_do_slot(_slot("som-08.L", "desconhecido")) is None
    assert rotulo_do_slot(_slot("desconhecido", "som-08.L")) is None


def test_mob_ausente_ou_quebrado_derruba_a_evidencia_do_slot():
    """Mob que falta ou que quebra deixa o slot sem evidencia."""
    sem_mob = SourceClip("som-08.L")
    sem_mob.mob = None
    assert rotulo_do_slot(_slot_de(sem_mob)) is None
    quebrado = SourceClip("som-08.L")
    quebrado.mob = MobQuebrado()
    assert rotulo_do_slot(_slot_de(quebrado)) is None


def test_clip_dentro_de_grupo_tem_o_rotulo_do_slot():
    """O clip vem dentro de um grupo: a busca desce e acha o rotulo."""
    slot = SimpleNamespace(segment=SimpleNamespace(components=[Grupo(SourceClip("som-08.L"))]))
    assert rotulo_do_slot(slot) == "L"


def test_muitos_clips_deixam_a_evidencia_de_fora():
    """Com mais clips que o teto, a evidencia fica incompleta e nao vale."""
    assert rotulo_do_slot(_slot(*["som-08.L"] * (LIMITE_DE_CLIPS + 1))) is None


def test_grupo_de_51_precisa_de_seis_slots_com_conjunto_unico():
    """O grupo do 5.1 precisa de seis slots com o conjunto de rotulos do 5.1."""
    completo = [_slot(f"som-08.{rotulo}") for rotulo in CONJUNTO_51]
    assert rotulos_do_grupo(completo) == list(CONJUNTO_51)
    assert rotulos_do_grupo(completo[:-1]) is None
    repetido = list(completo)
    repetido[-1] = _slot("som-08.L")
    assert rotulos_do_grupo(repetido) is None


def test_grupo_com_evidence_incompleta_vai_para_o_layout_padrao():
    """Um slot sem evidencia derruba o grupo inteiro: quem decide e o layout."""
    grupo = [_slot(f"som-08.{rotulo}") for rotulo in CONJUNTO_51]
    assert rotulos_do_grupo(grupo) == list(CONJUNTO_51)
    grupo[0] = _slot("som-08.L", "desconhecido")
    assert rotulos_do_grupo(grupo) is None
