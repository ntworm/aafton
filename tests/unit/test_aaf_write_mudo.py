"""Track muda e clip desativado do Live vao ao AAF como clip mudo do Avid.

Decisao do projeto (15/11/2026): o clip leva o ganho normal e fica mudo num
Selector cujo Selected e um Filler, em vez de um ganho -inf que o editor teria
de subir na mao.
"""

from __future__ import annotations

import gzip
import xml.etree.ElementTree as ET
from pathlib import Path

import aaf2
import pytest

from aafton.aaf_read import _read_point_list, read_aaf
from aafton.aaf_write import write_aaf
from aafton.als_ancora import start_padrao
from aafton.ir import ClipIR, MediaAsset, SessionIR, TrackIR
from aafton.pipeline import export_session
from aafton.report import Report
from aafton.timecode import TimecodeRate
from aafton.verify import verify_conversion
from tests.unit.test_als_sem_sidecar import UMA_HORA_24, clip, escrever_wav, montar_set


def _marcar(als: Path, track_mudo: str, clip_desativado: str) -> None:
    """Muta uma track e desativa um clip, como o Live grava."""
    root = ET.parse(gzip.open(als, "rb")).getroot()
    for track in root.iter("AudioTrack"):
        nome = track.find("Name/EffectiveName").get("Value")
        if nome == track_mudo:
            mixer = track.find("DeviceChain/Mixer")
            if mixer is None:
                mixer = ET.SubElement(ET.SubElement(track, "DeviceChain"), "Mixer")
            speaker = ET.SubElement(mixer, "Speaker")
            ET.SubElement(speaker, "Manual", Value="false")
        for node in track.iter("AudioClip"):
            if node.find("Name").get("Value") == clip_desativado:
                ET.SubElement(node, "Disabled", Value="true")
    with gzip.open(als, "wb") as handle:
        handle.write(ET.tostring(root, encoding="utf-8"))


def _componentes(aaf: Path, slot_nome: str) -> list[str]:
    """Tipos dos componentes da Sequence do slot, sem Filler."""
    with aaf2.open(str(aaf), "r") as f:
        comp = next(f.content.compositionmobs())
        slot = next(s for s in comp.slots if s.name == slot_nome)
        seg = slot.segment
        while type(seg).__name__ != "Sequence":
            seg = seg.segments[0]
        return [type(c).__name__ for c in seg.components if type(c).__name__ != "Filler"]


def _ganho_do_alternate(aaf: Path, slot_nome: str) -> list[float]:
    """Ganho constante dentro de cada Selector mudo do slot (1.0 sem grupo de ganho)."""
    ganhos = []
    with aaf2.open(str(aaf), "r") as f:
        comp = next(f.content.compositionmobs())
        slot = next(s for s in comp.slots if s.name == slot_nome)
        seg = slot.segment
        while type(seg).__name__ != "Sequence":
            seg = seg.segments[0]
        for c in seg.components:
            if type(c).__name__ != "Selector":
                continue
            assert type(c["Selected"].value).__name__ == "Filler"
            alt = list(c["Alternates"].value)[0]
            if type(alt).__name__ == "OperationGroup" and alt.parameters:
                ganhos.append(float(alt.parameters[0].value))
            else:
                ganhos.append(1.0)
    return ganhos


def _set(tmp_path: Path) -> Path:
    dx = escrever_wav(tmp_path, "dx.wav")
    amb = escrever_wav(tmp_path, "amb.wav")
    als = montar_set(
        tmp_path,
        [
            ("DX", [clip(dx, 0.0, 4.0), clip(dx, 4.0, 8.0, nome="dx desligado")]),
            ("AMB", [clip(amb, 0.0, 8.0)]),
        ],
    )
    _marcar(als, track_mudo="AMB", clip_desativado="dx desligado")
    return als


def test_track_muda_e_clip_desativado_viram_selector_com_ganho_normal(tmp_path):
    als = _set(tmp_path)
    aaf = tmp_path / "saida.aaf"
    export_session(als, aaf, Report(strict=False), start=start_padrao())

    assert _componentes(aaf, "DX")[1] == "Selector"
    assert _componentes(aaf, "AMB") == ["Selector"]
    assert _ganho_do_alternate(aaf, "DX") == [1.0]
    assert _ganho_do_alternate(aaf, "AMB") == [1.0]


def test_leitor_do_aafton_le_o_mudo_com_ganho_normal_e_o_verify_passa(tmp_path):
    als = _set(tmp_path)
    aaf = tmp_path / "saida.aaf"
    export_session(als, aaf, Report(strict=False), start=start_padrao())

    session = read_aaf(aaf, Report(strict=False))
    por_nome = {(t.name, c.name): c for t in session.tracks for c in t.clips}
    mudos = sorted(nome for nome, c in por_nome.items() if c.muted)
    assert mudos == [("AMB", "amb"), ("DX", "dx desligado")]
    assert all(c.gain_linear == 1.0 for c in por_nome.values())
    assert verify_conversion(aaf, als).ok is True


def _geometria(sessao: SessionIR) -> list[tuple]:
    """Posicoes, offsets e fades dos clips por track."""
    return [
        (
            t.name,
            c.timeline_start_samples,
            c.timeline_end_samples,
            c.source_offset_samples,
            c.fade_in,
            c.fade_out,
        )
        for t in sessao.tracks
        for c in t.clips
    ]


def _par_mudo(tmp_path: Path, tracks: list, clip_mudo: str) -> tuple[Path, Path, Path]:
    """Exporta um set normal e outro com um clip desativado para comparar os AAFs."""
    als_ok = montar_set(tmp_path, tracks, bpm=60.0, nome="normal.als")
    als_mudo = montar_set(tmp_path, tracks, bpm=60.0, nome="mudo.als")
    _marcar(als_mudo, "", clip_mudo)
    aaf_ok, aaf_mudo = tmp_path / "normal.aaf", tmp_path / "mudo.aaf"
    export_session(als_ok, aaf_ok, Report(strict=False), start=start_padrao())
    export_session(als_mudo, aaf_mudo, Report(strict=False), start=start_padrao())
    return als_mudo, aaf_ok, aaf_mudo


@pytest.mark.parametrize("mudo_idx", [0, 1])
def test_clip_mudo_com_crossfade_nativo_para_vizinho_ativo(tmp_path, mudo_idx):
    dx = escrever_wav(tmp_path, "dx.wav")
    c0 = clip(dx, 0.0, 4.0, nome="c0", fade_out=0.5)
    c1 = clip(dx, 4.0, 8.0, nome="c1", crossfade=True, loop_start=1.0)
    als_mudo, aaf_ok, aaf_mudo = _par_mudo(tmp_path, [("DX", [c0, c1])], f"c{mudo_idx}")
    lido = read_aaf(aaf_mudo, Report(strict=False))
    assert _geometria(lido) == _geometria(read_aaf(aaf_ok, Report(strict=False)))
    clips = lido.tracks[0].clips
    assert [c.muted for c in clips] == [mudo_idx == 0, mudo_idx == 1]
    assert all(c.gain_linear == 1.0 for c in clips)
    assert verify_conversion(aaf_mudo, als_mudo).ok is True
    esperado = ["Selector", "Transition"] if mudo_idx == 0 else ["Transition", "Selector"]
    comps = _componentes(aaf_mudo, "DX")
    assert (comps[:2] if mudo_idx == 0 else comps[1:]) == esperado
    assert _ganho_do_alternate(aaf_mudo, "DX") == [1.0]


def test_clip_mudo_com_fades_proprios_de_entrada_e_saida(tmp_path):
    dx = escrever_wav(tmp_path, "dx.wav")
    c = clip(dx, 1.0, 5.0, nome="mudo", loop_start=1.0, fade_in=0.5, fade_out=0.5)
    als_mudo, aaf_ok, aaf_mudo = _par_mudo(tmp_path, [("DX", [c])], "mudo")
    lido = read_aaf(aaf_mudo, Report(strict=False))
    assert _geometria(lido) == _geometria(read_aaf(aaf_ok, Report(strict=False)))
    cl = lido.tracks[0].clips[0]
    assert cl.muted is True and cl.gain_linear == 1.0
    assert (cl.fade_in.duration_samples, cl.fade_out.duration_samples) == (24000, 24000)
    assert verify_conversion(aaf_mudo, als_mudo).ok is True
    # Com ganho 1 o escritor nao cria grupo de ganho: o clip ativo e o SourceClip.
    assert _componentes(aaf_ok, "DX") == ["Transition", "SourceClip", "Transition"]
    assert _componentes(aaf_mudo, "DX") == ["Transition", "Selector", "Transition"]
    assert _ganho_do_alternate(aaf_mudo, "DX") == [1.0]


def _curva_do_selector(aaf: Path, slot: str) -> list[tuple[float, float]]:
    """Extrai pontos de automacao dentro do Selector via pyaaf2."""
    with aaf2.open(str(aaf), "r") as f:
        seg = next(s for s in next(f.content.compositionmobs()).slots if s.name == slot).segment
        while type(seg).__name__ != "Sequence":
            seg = seg.segments[0]
        sel = next(c for c in seg.components if type(c).__name__ == "Selector")
        assert type(sel["Selected"].value).__name__ == "Filler"
        alt = list(sel["Alternates"].value)[0]
        assert type(alt).__name__ == "OperationGroup"
        assert [type(p).__name__ for p in alt.parameters] == ["VaryingValue"]
        return _read_point_list(alt.parameters)


def _montar_sessao_ganho(dx: Path, mudo: bool, pts: list[tuple[int, float]]) -> SessionIR:
    """Sessao com automacao de ganho da track e clip opcionalmente mudo."""
    t0, rate = UMA_HORA_24, TimecodeRate(24, 1)
    m = MediaAsset(id="m1", filename=dx.name, channels=1, sample_rate=48000)
    m.duration_samples, m.status, m.resolved_path = 960000, "resolved", str(dx)
    c = ClipIR(
        id="c0",
        track_id="t0",
        channel_index=0,
        event_index=0,
        name="dx",
        media_id="m1",
        timeline_start_samples=t0,
        timeline_end_samples=t0 + 96000,
        source_offset_samples=0,
        muted=mudo,
    )
    t = TrackIR(id="t0", source_index=0, name="DX", kind="audio", channel_count=1, clips=[c])
    t.gain_points = list(pts)
    s = SessionIR(name="g", sample_rate=48000, timecode_rate=rate, start_timecode_samples=t0)
    s.tracks, s.media = [t], [m]
    return s


def test_clip_mudo_com_automacao_de_ganho_da_track(tmp_path):
    dx = escrever_wav(tmp_path, "dx.wav")
    pts = [(UMA_HORA_24, 0.25), (UMA_HORA_24 + 48000, 1.0), (UMA_HORA_24 + 96000, 0.5)]
    aaf_ok, aaf_mudo = tmp_path / "ok.aaf", tmp_path / "mudo.aaf"
    write_aaf(_montar_sessao_ganho(dx, False, pts), aaf_ok, Report(strict=False))
    write_aaf(_montar_sessao_ganho(dx, True, pts), aaf_mudo, Report(strict=False))
    lido_ok = read_aaf(aaf_ok, Report(strict=False))
    lido = read_aaf(aaf_mudo, Report(strict=False))
    assert _geometria(lido) == _geometria(lido_ok)
    ganho = [(t, pytest.approx(v)) for t, v in lido_ok.tracks[0].gain_points]
    assert lido.tracks[0].gain_points == ganho
    cl = lido.tracks[0].clips[0]
    assert cl.muted is True and cl.gain_linear == pytest.approx(1.0)
    assert _componentes(aaf_mudo, "DX") == ["Selector"]
    esperado = [(0.0, pytest.approx(0.25)), (0.5, pytest.approx(1.0)), (1.0, pytest.approx(0.5))]
    assert _curva_do_selector(aaf_mudo, "DX") == esperado
