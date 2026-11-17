"""Expansao de canais na ida do AAF: casamento, entradas faltando, irmas, grupo e marca."""

import json
import random
import struct
from pathlib import Path

import pytest

from aafton.aaf_read import read_aaf
from aafton.als_read import read_session
from aafton.expandir import expand_production_channels, planejar, resolve_for_inspect
from aafton.ir import EXPANDED_MARK, FadeIR
from aafton.pipeline import ImportOptions, import_session
from aafton.report import Report
from aafton.sidecar import sidecar_path_for
from tests.aaf_factory import build_calibration_aaf
from tests.unit.test_som_direto_leitor import RATE, _chunk, bext, ixml

SEP = "·"
TC = 10 * 3600 * RATE
FRAMES = 9600
INICIO_AAF = 3600 * RATE  # o AAF de calibracao comeca em 01:00:00:00


def gravar(path: Path, canais: list[bytes], *, bits=16, tag=1, extras=()) -> Path:
    """WAV com os canais dados (bytes de cada canal), intercalados, e chunks extras."""
    largura = bits // 8
    frames = len(canais[0]) // largura
    dados = b"".join(c[i * largura : (i + 1) * largura] for i in range(frames) for c in canais)
    align = largura * len(canais)
    fmt = struct.pack("<HHIIHH", tag, len(canais), RATE, RATE * align, align, bits)
    corpo = b"WAVE" + _chunk(b"fmt ", fmt)
    for chunk_id, payload in extras:
        corpo += _chunk(chunk_id, payload)
    corpo += _chunk(b"data", dados)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(corpo)) + corpo)
    return path


def canais_16(semente: str, quantos: int, frames: int = FRAMES) -> list[bytes]:
    gerador = random.Random(semente)
    return [gerador.randbytes(frames * 2) for _ in range(quantos)]


def metadados(scene, take, entradas, tc=TC) -> tuple:
    return ((b"bext", bext(tc, "2025-05-05")), (b"iXML", ixml(scene, take, "", entradas)))


def original(pasta: Path, nome: str, scene, take, entradas, tc=TC) -> tuple[Path, list[bytes]]:
    canais = canais_16(nome, len(entradas))
    caminho = gravar(pasta / nome, canais, extras=metadados(scene, take, entradas, tc))
    return caminho, canais


def copia(pasta: Path, nome: str, canal: bytes, scene, take, entrada, tc=TC) -> Path:
    """Copia mono do editor: o canal do original com o TRACK_LIST de uma entrada."""
    return gravar(pasta / nome, [canal], extras=metadados(scene, take, [entrada], tc))


def clip(path: Path, nome: str, start: int, duration: int, offset: int = 0) -> dict:
    return {"path": path, "mob_name": nome, "start": start, "duration": duration, "offset": offset}


@pytest.fixture
def cena(tmp_path):
    return montar_cena(tmp_path)


def montar_cena(tmp_path: Path) -> dict:
    """Gravador com tres takes e um AAF do editor com quatro tracks.

    Take A (4 entradas): o editor usou so a 3, na Audio 1. Take C (3 entradas):
    entrada 3 na Audio 2 e na Audio 3, com clips sobrepostos. Take B (2
    entradas): completo, nas Audio 3 e 4.
    """
    gravador, midia = tmp_path / "som" / "05-05-25", tmp_path / "AAF Media"
    entradas_a = [(3, "NEUMANN"), (4, "CCM8L"), (5, "LAPSOLDADO"), (6, "LAPJS")]
    entradas_c = [(3, "MK41"), (4, "A"), (5, "B")]
    entradas_b = [(3, "NEUMANN"), (4, "CCM8L")]
    a, canais_a = original(gravador, "009CENA16A-009.WAV", "16A", "009", entradas_a)
    c, canais_c = original(gravador, "025CENA37-025.WAV", "37", "025", entradas_c, TC + RATE)
    b, canais_b = original(gravador, "003CENA16-003.WAV", "16", "003", entradas_b, TC + 2 * RATE)
    copia_a = copia(midia, "a3.wav", canais_a[0], "16A", "009", (3, "NEUMANN"))
    copia_c = copia(midia, "c3.wav", canais_c[0], "37", "025", (3, "MK41"), TC + RATE)
    copia_b3 = copia(midia, "b3.wav", canais_b[0], "16", "003", (3, "NEUMANN"), TC + 2 * RATE)
    copia_b4 = copia(midia, "b4.wav", canais_b[1], "16", "003", (4, "CCM8L"), TC + 2 * RATE)
    tracks = [
        {"name": "Audio 1", "clips": [clip(copia_a, "009CENA16A-009.WAV", 48000, 4800, 1000)]},
        {"name": "Audio 2", "clips": [clip(copia_c, "025CENA37-025.WAV", 20000, 4000)]},
        {
            "name": "Audio 3",
            "clips": [
                clip(copia_c, "025CENA37-025.WAV", 22000, 4000, 2000),
                clip(copia_b3, "003CENA16-003.WAV", 40000, 4000),
            ],
        },
        {"name": "Audio 4", "clips": [clip(copia_b4, "003CENA16-003.WAV", 40000, 4000)]},
    ]
    aaf = build_calibration_aaf(tmp_path / "editor.aaf", tracks, frames=60000)
    return {"aaf": aaf, "folder": tmp_path / "som", "a": a, "b": b, "c": c, "tmp": tmp_path}


def importar(cena, nome="expandido.als", **opcoes):
    saida = cena["tmp"] / "out" / nome
    saida.parent.mkdir(parents=True, exist_ok=True)
    report = Report(strict=False)
    opts = ImportOptions(input=str(cena["aaf"]), output=str(saida), **opcoes)
    import_session(opts, report, production_audio=str(cena["folder"]))
    return saida, report


def _sessao_resolvida(cena):
    report = Report(strict=False)
    session = read_aaf(cena["aaf"], report)
    resolve_for_inspect(session, [cena["tmp"] / "AAF Media"], report)
    return session, report


def _clips(track):
    return [
        (c.timeline_start_samples - INICIO_AAF, c.timeline_end_samples - INICIO_AAF)
        for c in sorted(track.clips, key=lambda c: c.timeline_start_samples)
    ]


def test_take_com_so_a_entrada_3_ganha_irmas_4_a_6_com_as_mesmas_posicoes(cena):
    saida, _ = importar(cena)
    session = read_session(saida, Report(strict=False))
    nomes = [t.name for t in session.tracks]
    assert nomes == [
        "Audio 1",
        f"Audio 1 {SEP} In 4 {SEP} CCM8L",
        f"Audio 1 {SEP} In 5 {SEP} LAPSOLDADO",
        f"Audio 1 {SEP} In 6 {SEP} LAPJS",
        "Audio 2",
        f"Audio 2 {SEP} In 4 {SEP} A",
        f"Audio 2 {SEP} In 5 {SEP} B",
        "Audio 3",
        "Audio 4",
    ]
    por_nome = {t.name: t for t in session.tracks}
    editor = por_nome["Audio 1"]
    for nome in nomes[1:4]:
        irma = por_nome[nome]
        assert _clips(irma) == _clips(editor) == [(48000, 52800)]
        assert irma.clips[0].source_offset_samples == editor.clips[0].source_offset_samples
        assert irma.raw["expanded"] == EXPANDED_MARK


def test_take_completo_nao_ganha_irma(cena):
    saida, report = importar(cena)
    session = read_session(saida, Report(strict=False))
    assert not [t for t in session.tracks if t.name.startswith(("Audio 3 ", "Audio 4 "))]
    resumo = next(i for i in report.issues if i.code == "EXPANSION_SUMMARY")
    assert resumo.context == {"takes": 3, "expanded_takes": 2, "siblings": 5}


def test_take_em_duas_tracks_fica_na_dona_e_o_trecho_duplicado_entra_uma_vez(cena):
    saida, report = importar(cena)
    session = read_session(saida, Report(strict=False))
    irma = next(t for t in session.tracks if t.name == f"Audio 2 {SEP} In 4 {SEP} A")
    assert _clips(irma) == [(20000, 24000), (24000, 26000)]
    segundo = sorted(irma.clips, key=lambda c: c.timeline_start_samples)[1]
    assert segundo.source_offset_samples == 4000
    notas = [i.message for i in report.issues if i.code == "EXPANSION_NOTE"]
    # Mesmo original na mesma posicao da fonte: duplicado do editor, nota informativa.
    duplicado = [
        i for i in report.issues if i.code == "EXPANSION_NOTE" and "duplicate part" in i.message
    ]
    assert duplicado and all(i.severity == "info" for i in duplicado)
    assert not any("overlapping part was left out" in nota for nota in notas)


def test_irma_copia_o_mix_da_dona_e_os_fades_e_o_ganho_do_clip(cena, tmp_path):
    session, report = _sessao_resolvida(cena)
    dona = session.tracks[0]
    dona.muted, dona.gain_linear, dona.pan = True, 0.5, -0.25
    dona.gain_points = [(INICIO_AAF + 48000, 0.5)]
    editor = dona.clips[0]
    editor.fade_in = FadeIR(kind="in", duration_samples=100)
    editor.fade_out = FadeIR(kind="out", duration_samples=200)
    editor.gain_linear = 0.7
    expand_production_channels(session, cena["folder"], tmp_path / "monos", report)
    irmas = session.tracks[1:4]
    for irma in irmas:
        assert (irma.muted, irma.gain_linear, irma.pan) == (True, 0.5, -0.25)
        assert irma.gain_points == dona.gain_points
        assert irma.expansion_group == dona.expansion_group == "Audio 1"
        novo = irma.clips[0]
        assert novo.fade_in.duration_samples == 100 and novo.fade_out.duration_samples == 200
        assert novo.gain_linear == 0.7
        assert novo.timeline_start_samples == editor.timeline_start_samples


def test_clip_da_irma_copia_o_mute_do_clip_do_editor(cena, tmp_path):
    session, report = _sessao_resolvida(cena)
    session.tracks[0].clips[0].muted = True
    expand_production_channels(session, cena["folder"], tmp_path / "monos", report)
    mudos = {t.name: [c.muted for c in t.clips] for t in session.tracks}
    for nome in (f"Audio 1 {SEP} In 4 {SEP} CCM8L", f"Audio 1 {SEP} In 6 {SEP} LAPJS"):
        assert mudos[nome] == [True]
    assert mudos[f"Audio 2 {SEP} In 4 {SEP} A"] == [False, False]


def test_grupo_com_o_nome_da_dona_e_sidecar_com_a_marca(cena):
    saida, _ = importar(cena)
    session = read_session(saida, Report(strict=False))
    grupos = {t.name: t.group_path for t in session.tracks}
    assert grupos["Audio 1"] == ["Audio 1"]
    assert grupos[f"Audio 1 {SEP} In 6 {SEP} LAPJS"] == ["Audio 1"]
    assert grupos[f"Audio 2 {SEP} In 5 {SEP} B"] == ["Audio 2"]
    assert grupos["Audio 3"] == [] and grupos["Audio 4"] == []
    dados = json.loads(sidecar_path_for(saida).read_text(encoding="utf-8"))["expanded"]
    irma = dados["tracks"][f"Audio 1 {SEP} In 4 {SEP} CCM8L"]
    assert irma["mark"] == EXPANDED_MARK
    assert irma["clips"][0]["source"] == str(cena["a"])
    assert irma["clips"][0]["input"] == "In 4"
    assert dados["folder"] == str(cena["folder"].resolve())


def test_groups_prefix_deixa_a_expansao_fora_com_nota(cena):
    saida, report = importar(cena, groups="prefix")
    session = read_session(saida, Report(strict=False))
    grupos = {t.name: t.group_path for t in session.tracks}
    assert grupos["Audio 1"] == ["Audio 1"]
    assert grupos[f"Audio 2 {SEP} In 4 {SEP} A"] == ["Audio 2"]
    assert grupos["Audio 3"] == grupos["Audio 4"] == ["Audio"]
    assert "EXPANSION_GROUPS_PREFIX" in [i.code for i in report.issues]


def test_sem_production_audio_o_set_sai_como_hoje(cena):
    saida = cena["tmp"] / "out" / "sem.als"
    saida.parent.mkdir(parents=True, exist_ok=True)
    import_session(ImportOptions(input=str(cena["aaf"]), output=str(saida)), Report(strict=False))
    session = read_session(saida, Report(strict=False))
    assert [t.name for t in session.tracks] == ["Audio 1", "Audio 2", "Audio 3", "Audio 4"]
    assert "expanded" not in json.loads(sidecar_path_for(saida).read_text(encoding="utf-8"))


# --- Casamento sem iXML, _L/_R e ambiguidade ---


def test_clips_l_e_r_de_um_original_de_dois_canais(tmp_path):
    gravador, midia = tmp_path / "som", tmp_path / "midia"
    entradas = [(3, "NEUMANN"), (4, "CCM8L")]
    _, canais = original(gravador, "27CENA12A-013.WAV", "12A", "013", entradas)
    esquerda = copia(midia, "l.wav", canais[0], "12A", "013", (3, "NEUMANN"))
    direita = copia(midia, "r.wav", canais[1], "12A", "013", (4, "CCM8L"))
    tracks = [
        {"name": "Audio 5_L", "clips": [clip(esquerda, "27CENA12A-013.WAV_L", 1000, 2000)]},
        {"name": "Audio 5_R", "clips": [clip(direita, "27CENA12A-013.WAV_R", 1000, 2000)]},
    ]
    aaf = build_calibration_aaf(tmp_path / "lr.aaf", tracks, frames=20000)
    session = read_aaf(aaf, Report(strict=False))
    resolve_for_inspect(session, [midia], Report(strict=False))
    plano = planejar(session, gravador)
    (take,) = plano.takes.values()
    assert take.usadas == {("In", 3), ("In", 4)} and take.faltando == []


def _float32(valores: list[float]) -> bytes:
    return struct.pack(f"<{len(valores)}f", *valores)


def _vinte_e_quatro(valores: list[float]) -> bytes:
    inteiros = [max(min(round(v * (1 << 23)), (1 << 23) - 1), -(1 << 23)) for v in valores]
    return b"".join(n.to_bytes(3, "little", signed=True) for n in inteiros)


def test_copia_mono_sem_ixml_casa_pelo_nome_e_pela_comparacao_de_amostras(tmp_path):
    gravador, midia = tmp_path / "som", tmp_path / "midia"
    gerador = random.Random(7)
    canais = [[gerador.uniform(-0.9, 0.9) for _ in range(FRAMES)] for _ in range(3)]
    canais[1][10] = 1.5  # a copia de 24 bits corta esta amostra
    extras = metadados("20", "001", [(3, "NEUMANN"), (4, "CCM8L"), (5, "LAP")])
    flutuantes = [_float32(c) for c in canais]
    gravar(gravador / "020CENA20-001.WAV", flutuantes, bits=32, tag=3, extras=extras)
    copia_24 = gravar(midia / "c.wav", [_vinte_e_quatro(canais[1])], bits=24)
    tracks = [{"name": "Audio 1", "clips": [clip(copia_24, "020CENA20-001.WAV", 0, 5000, 5)]}]
    aaf = build_calibration_aaf(tmp_path / "f.aaf", tracks, frames=20000)
    session = read_aaf(aaf, Report(strict=False))
    resolve_for_inspect(session, [midia], Report(strict=False))
    plano = planejar(session, gravador)
    (take,) = plano.takes.values()
    assert take.usadas == {("In", 4)}
    assert take.faltando == [("In", 3), ("In", 5)]


def test_original_ambiguo_deixa_o_clip_como_esta_com_nota(tmp_path):
    gravador, midia = tmp_path / "som", tmp_path / "midia"
    canais = canais_16("x", 2)
    extras = metadados("1", "1", [(3, "A"), (4, "B")])
    for dia in ("dia1", "dia2"):
        gravar(gravador / dia / "X-001.WAV", canais, extras=extras)
    sem_ixml = gravar(midia / "x.wav", [canais[0]])
    tracks = [{"name": "Audio 1", "clips": [clip(sem_ixml, "X-001.WAV", 0, 2000)]}]
    aaf = build_calibration_aaf(tmp_path / "x.aaf", tracks, frames=20000)
    session = read_aaf(aaf, Report(strict=False))
    resolve_for_inspect(session, [midia], Report(strict=False))
    report = Report(strict=False)
    expand_production_channels(session, gravador, tmp_path / "monos", report)
    assert [t.name for t in session.tracks] == ["Audio 1"]
    notas = [i.message for i in report.issues if i.code == "EXPANSION_NOTE"]
    assert any("more than one original" in nota and "(1x)" in nota for nota in notas)


def test_sobreposicao_com_audio_diferente_continua_aviso():
    from aafton.expandir import NOTA_SOBREPOSTO, Irma, _nota_da_sobreposicao
    from aafton.expandir_casamento import NOTA_DUPLICADO
    from aafton.ir import TrackIR

    track = TrackIR(id="t", source_index=0, name="t", kind="audio", channel_count=1)
    irma = Irma(track=track, dona=track, chave=("In", 4))
    irma.fontes.append((0, 1000, "a.wav", 0))
    assert _nota_da_sobreposicao(irma, 500, 1500, "a.wav", 0) == NOTA_DUPLICADO
    assert _nota_da_sobreposicao(irma, 500, 1500, "a.wav", 10) == NOTA_SOBREPOSTO
    assert _nota_da_sobreposicao(irma, 500, 1500, "b.wav", 0) == NOTA_SOBREPOSTO
