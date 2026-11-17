"""Taxa de amostragem da sessao na leitura de AAF com midia em taxas mistas."""

from __future__ import annotations

from pathlib import Path

import aaf2
from aaf2.rational import AAFRational

from aafton.aaf_read import read_aaf
from aafton.als_ancora import start_padrao
from aafton.pipeline import export_session
from aafton.report import Report
from aafton.verify import verify_conversion
from tests.aaf_factory import build_resolve_synthetic_aaf
from tests.unit.test_als_sem_sidecar import clip, escrever_wav, montar_set


def _exportar_set_3_clips_48k_2_arquivos_96k(tmp_path: Path):
    w48 = escrever_wav(tmp_path, "fala48.wav", taxa=48000)
    w96a = escrever_wav(tmp_path, "fx96_a.wav", taxa=96000)
    w96b = escrever_wav(tmp_path, "fx96_b.wav", taxa=96000)
    dx = [clip(w48, 0.0, 2.0, nome="c1"), clip(w48, 4.0, 6.0, nome="c2")]
    dx.append(clip(w48, 8.0, 10.0, nome="c3"))
    fx = [clip(w96a, 0.0, 2.0, nome="fx_a", loop_start=0.5), clip(w96b, 4.0, 6.0, nome="fx_b")]
    als = montar_set(tmp_path, [("DX", dx), ("FX", fx)])
    aaf = tmp_path / "sessao.aaf"
    sessao_set = export_session(als, aaf, Report(strict=False), start=start_padrao())
    return als, aaf, sessao_set


def _link_wav(f, wav: Path, taxa: int):
    stream = {"codec_type": "audio", "codec_name": "pcm_s16le", "sample_rate": taxa}
    stream.update({"duration_ts": taxa * 20, "channels": 1})
    meta = {"format": {"filename": str(wav), "format_name": "wav"}, "streams": [stream]}
    master, _s, _t = f.content.link_external_wav(meta)
    return master


def _aaf_descritores_mistos(tmp_path: Path, nome: str, edit_rates: list[int]) -> Path:
    """AAF com 1 descritor 48 kHz, 2 descritores 96 kHz e slots no edit_rate pedido."""
    pares = [("a48.wav", 48000), ("b96.wav", 96000), ("c96.wav", 96000)]
    aaf = tmp_path / nome
    with aaf2.open(str(aaf), "w") as f:
        comp = f.create.CompositionMob("mista")
        comp.usage = "Usage_TopLevel"
        f.content.mobs.append(comp)
        masters = [_link_wav(f, escrever_wav(tmp_path, arq, taxa=tx), tx) for arq, tx in pares]
        for idx, edit_rate in enumerate(edit_rates):
            slot = comp.create_timeline_slot(edit_rate=AAFRational(edit_rate, 1))
            slot.name = f"Track {idx + 1}"
            seq = f.create.Sequence(media_kind="sound")
            slot.segment = seq
            sc = f.create.SourceClip(media_kind="sound", start=0)
            sc.length = edit_rate
            sc.mob, sc.slot_id = masters[idx % len(masters)], 1
            seq.components.append(sc)
        tc_slot = comp.create_timeline_slot(edit_rate=AAFRational(25, 1))
        tc_slot.name, tc = "TC", f.create.Timecode(fps=25, drop=False)
        tc.start, tc.length, tc_slot.segment = 90000, 25, tc
    return aaf


def _posicoes(track) -> list[tuple[int, int, int | None]]:
    return [
        (c.timeline_start_samples, c.timeline_end_samples, c.source_offset_samples)
        for c in track.clips
    ]


def test_read_aaf_usa_edit_rate_dos_slots_quando_maioria_dos_arquivos_difere(tmp_path):
    _, aaf, sessao_set = _exportar_set_3_clips_48k_2_arquivos_96k(tmp_path)
    report = Report(strict=False)

    lida = read_aaf(aaf, report)

    assert sessao_set.sample_rate == 48000
    assert lida.sample_rate == 48000
    assert lida.start_timecode_samples == sessao_set.start_timecode_samples
    assert [_posicoes(t) for t in lida.tracks] == [_posicoes(t) for t in sessao_set.tracks]
    avisos = [i for i in report.issues if i.code == "AAF_MIXED_SAMPLE_RATES"]
    assert len(avisos) == 1
    assert "48000" in avisos[0].message
    assert "slot" in avisos[0].message


def test_verify_conversion_sem_sidecar_passa_com_desvio_zero_em_taxa_mista(tmp_path):
    als, aaf, _ = _exportar_set_3_clips_48k_2_arquivos_96k(tmp_path)

    resultado = verify_conversion(aaf, als)

    assert resultado.ok is True
    assert resultado.tolerance_samples == 0
    assert resultado.audio_expected == 5
    assert resultado.audio_compared == 5
    assert resultado.max_delta_samples == 0
    assert all(r.delta_samples == 0 and r.delta_end_samples == 0 for r in resultado.rows)


def test_slots_em_frame_rate_ou_discordantes_mantem_maioria_dos_descritores(tmp_path):
    aaf_24 = _aaf_descritores_mistos(tmp_path, "fps24.aaf", [24, 24])
    rep_24 = Report(strict=False)
    sess_24 = read_aaf(aaf_24, rep_24)
    assert sess_24.sample_rate == 96000
    avisos_24 = [i for i in rep_24.issues if i.code == "AAF_MIXED_SAMPLE_RATES"]
    assert len(avisos_24) == 1
    assert "96000" in avisos_24[0].message
    assert "descriptor" in avisos_24[0].message

    aaf_disc = _aaf_descritores_mistos(tmp_path, "disc.aaf", [48000, 96000])
    sess_disc = read_aaf(aaf_disc, Report(strict=False))
    assert sess_disc.sample_rate == 96000


def test_resolve_24fps_e_slots_48k_com_descritores_48k_leem_igual(tmp_path):
    wav = escrever_wav(tmp_path, "dx.wav", taxa=48000)
    aaf_24 = build_resolve_synthetic_aaf(tmp_path / "r.aaf", wav, edit_rate=24, sample_rate=48000)
    sess_resolve = read_aaf(aaf_24, Report(strict=False))
    assert sess_resolve.sample_rate == 48000
    assert sess_resolve.start_timecode_samples == 3600 * 48000

    als = montar_set(tmp_path, [("DX", [clip(wav, 0.0, 2.0)])])
    aaf_48 = tmp_path / "uniforme48.aaf"
    export_session(als, aaf_48, Report(strict=False), start=start_padrao())
    rep_48 = Report(strict=False)
    sess_48 = read_aaf(aaf_48, rep_48)
    assert sess_48.sample_rate == 48000
    assert not [i for i in rep_48.issues if i.code == "AAF_MIXED_SAMPLE_RATES"]


def _comp_com_slots(f, nome: str, edit_rate: int, duracoes: tuple[int, ...], master) -> None:
    """Adiciona uma CompositionMob TopLevel com slots de audio nas duracoes dadas."""
    comp = f.create.CompositionMob(nome)
    comp.usage = "Usage_TopLevel"
    f.content.mobs.append(comp)
    for idx, duracao in enumerate(duracoes):
        slot = comp.create_timeline_slot(edit_rate=AAFRational(edit_rate, 1))
        slot.name = f"Track {idx + 1}"
        seq = f.create.Sequence(media_kind="sound")
        slot.segment = seq
        sc = f.create.SourceClip(media_kind="sound", start=0)
        sc.length = duracao
        sc.mob, sc.slot_id = master, 1
        seq.components.append(sc)
        seq.length = duracao
    tc_slot = comp.create_timeline_slot(edit_rate=AAFRational(25, 1))
    tc_slot.name, tc = "TC", f.create.Timecode(fps=25, drop=False)
    tc.start, tc.length, tc_slot.segment = 90000, 25, tc


def test_reranking_nao_adota_taxa_de_slot_ausente_nos_descritores(tmp_path):
    pares = [("a48.wav", 48000), ("b48.wav", 48000), ("c96.wav", 96000)]
    aaf = tmp_path / "rerank.aaf"
    with aaf2.open(str(aaf), "w") as f:
        masters = [_link_wav(f, escrever_wav(tmp_path, arq, taxa=tx), tx) for arq, tx in pares]
        # A 48k (maioria dos descritores): comp_confirmada soma 19 vs 18.
        # A 96k (reranking): comp_nao_confirmada vence (39 vs 38), mas 44000
        # nao e taxa padrao nem aparece em nenhum descritor da sessao.
        _comp_com_slots(f, "comp_confirmada", 96000, (12, 12, 14), masters[0])
        _comp_com_slots(f, "comp_nao_confirmada", 44000, (6, 6, 6), masters[0])
    report = Report(strict=False)

    sess = read_aaf(aaf, report)

    assert sess.name == "comp_nao_confirmada"
    assert sess.sample_rate == 48000
    avisos = [i for i in report.issues if i.code == "AAF_MIXED_SAMPLE_RATES"]
    assert len(avisos) == 1
    assert "48000" in avisos[0].message
    assert "descriptor" in avisos[0].message


def test_read_aaf_aceita_taxa_padrao_do_slot_sem_descritor_e_recusa_nao_padrao(tmp_path):
    wav48 = escrever_wav(tmp_path, "so48.wav", taxa=48000)

    aaf_padrao = tmp_path / "slot44k1_sem_desc.aaf"
    with aaf2.open(str(aaf_padrao), "w") as f:
        master = _link_wav(f, wav48, 48000)
        _comp_com_slots(f, "sub_44k1", 44100, (44100,), master)

    sess_padrao = read_aaf(aaf_padrao, Report(strict=False))
    assert sess_padrao.sample_rate == 44100

    aaf_nao_padrao = tmp_path / "slot2997_sem_desc.aaf"
    with aaf2.open(str(aaf_nao_padrao), "w") as f:
        master = _link_wav(f, wav48, 48000)
        _comp_com_slots(f, "ntsc_ticks", 2997, (2997,), master)

    sess_nao_padrao = read_aaf(aaf_nao_padrao, Report(strict=False))
    assert sess_nao_padrao.sample_rate == 48000
