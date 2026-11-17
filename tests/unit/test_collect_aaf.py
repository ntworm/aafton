"""Testes unitarios do collect para arquivos AAF linkados e mistos."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import aaf2

from aafton.aaf_read import read_aaf
from aafton.collect import plan_collect, run_collect
from aafton.collect_aaf import iter_descriptors
from aafton.media_extract import resolve_asset
from aafton.report import Report
from tests.aaf_factory import (
    SAMPLE_RATE,
    _wav_summary_header,
    build_linked_aaf,
    wav_bytes,
)


def _write_wav(path: Path, frames: int = 48000, tag: bytes = b"\x01\x01") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = bytearray(wav_bytes(frames, channels=1, sample_rate=SAMPLE_RATE))
    raw[44 : 44 + len(tag)] = tag
    path.write_bytes(bytes(raw))
    return path


def _build_synthetic_aaf(tmp_path: Path) -> dict:
    """Monta um AAF sintetico usando build_linked_aaf da aaf_factory e enriquece os descritores."""
    src_dir = tmp_path / "aaf_source"
    sub_dir = src_dir / "subfolder"
    src_dir.mkdir(parents=True)
    sub_dir.mkdir(parents=True)

    wav1 = _write_wav(src_dir / "dialog_one.wav", frames=96000, tag=b"\x10\x20")
    wav2 = _write_wav(sub_dir / "rel_two.wav", frames=96000, tag=b"\x30\x40")
    # O pyaaf2 le o cabecalho ao linkar: o arquivo existe na montagem e sai depois.
    missing_wav = _write_wav(src_dir / "gone_three.wav", frames=96000, tag=b"P`")
    hist_embedded_url = "file:///C:/historical/embedded_origin.wav"

    aaf_path = src_dir / "project.aaf"
    build_linked_aaf(aaf_path, [(0, 48000, 0)], wav1, track_name="DX 1")

    with aaf2.open(str(aaf_path), "rw") as f:
        comp = next(m for m in f.content.mobs if type(m).__name__ == "CompositionMob")
        seq = comp.slots[0].segment

        # Adicionar um segundo Locator ao descritor de wav1 (one descriptor with two Locators)
        mob_wav1 = next(
            m
            for m in f.content.sourcemobs()
            if getattr(m, "descriptor", None) is not None
            and type(m.descriptor).__name__ == "WAVEDescriptor"
        )
        loc2 = f.create.NetworkLocator()
        loc2["URLString"].value = "file:///backup/mirror/dialog_one.wav"
        mob_wav1.descriptor["Locator"].append(loc2)

        # Adicionar wav2 com URL relativa (subfolder/rel_two.wav)
        meta2 = {
            "format": {"filename": str(wav2), "format_name": "wav"},
            "streams": [
                {
                    "codec_type": "audio",
                    "codec_name": "pcm_s16le",
                    "sample_rate": SAMPLE_RATE,
                    "duration_ts": 96000,
                    "channels": 1,
                }
            ],
        }
        master2, src2, _ = f.content.link_external_wav(meta2)
        src2.descriptor["Locator"].value[0]["URLString"].value = "file:subfolder/rel_two.wav"
        sc2 = f.create.SourceClip(media_kind="sound", start=0)
        sc2.length = 48000
        sc2.mob = master2
        sc2.slot_id = 1
        seq.components.append(sc2)

        # Adicionar missing_wav (apontando para arquivo inexistente)
        meta_miss = {
            "format": {"filename": str(missing_wav), "format_name": "wav"},
            "streams": [
                {
                    "codec_type": "audio",
                    "codec_name": "pcm_s16le",
                    "sample_rate": SAMPLE_RATE,
                    "duration_ts": 96000,
                    "channels": 1,
                }
            ],
        }
        master_miss, src_miss, _ = f.content.link_external_wav(meta_miss)
        src_miss.descriptor["Locator"].value[0]["URLString"].value = missing_wav.resolve().as_uri()
        sc_miss = f.create.SourceClip(media_kind="sound", start=0)
        sc_miss.length = 48000
        sc_miss.mob = master_miss
        sc_miss.slot_id = 1
        seq.components.append(sc_miss)

        # Adicionar SourceMob embedded que mantem um Locator historico
        emb_master = f.create.MasterMob("emb_clip")
        f.content.mobs.append(emb_master)
        emb_desc = f.create.WAVEDescriptor()
        emb_desc["SampleRate"].value = aaf2.rational.AAFRational(SAMPLE_RATE, 1)
        emb_desc["Length"].value = 48000
        emb_desc["Summary"].value = list(_wav_summary_header(1, SAMPLE_RATE))
        emb_loc = f.create.NetworkLocator()
        emb_loc["URLString"].value = hist_embedded_url
        emb_desc["Locator"].append(emb_loc)

        emb_source = f.create.SourceMob()
        emb_source.name = "emb_clip <SOURCE MOB>"
        emb_source["EssenceDescription"].value = emb_desc
        f.content.mobs.append(emb_source)

        ed = f.create.EssenceData()
        ed.mob = emb_source
        stream = ed.open("w")
        stream.write(wav_bytes(48000, channels=1, sample_rate=SAMPLE_RATE))
        stream.close()
        f.content.essencedata.append(ed)

        sslot = emb_source.create_timeline_slot(edit_rate=SAMPLE_RATE)
        sslot.slot_id = 1
        sslot.name = "A1"
        sslot.segment = f.create.Filler("sound", length=48000)

        mslot = emb_master.create_timeline_slot(edit_rate=SAMPLE_RATE)
        mslot.slot_id = 1
        mslot.name = "A1"
        sc_emb_src = f.create.SourceClip(media_kind="sound", start=0)
        sc_emb_src.length = 48000
        sc_emb_src.mob = emb_source
        sc_emb_src.slot_id = 1
        mslot.segment = sc_emb_src

        sc_emb = f.create.SourceClip(media_kind="sound", start=0)
        sc_emb.length = 48000
        sc_emb.mob = emb_master
        sc_emb.slot_id = 1
        seq.components.append(sc_emb)
        seq.length = 48000 * 4

    missing_wav.unlink()
    return {
        "aaf_path": aaf_path,
        "wav1": wav1,
        "wav2": wav2,
        "missing_wav": missing_wav,
        "hist_embedded_url": hist_embedded_url,
    }


def test_linked_aaf_collect_full_acceptance(tmp_path):
    """Cobre todos os requisitos do item 2 de aceite num AAF sintetico."""
    fx = _build_synthetic_aaf(tmp_path)
    aaf_path: Path = fx["aaf_path"]
    wav1: Path = fx["wav1"]
    wav2: Path = fx["wav2"]

    for idx, p in enumerate((aaf_path, wav1, wav2)):
        ts = 1_700_000_000_000_000_000 + idx * 1_000_000_000
        os.utime(p, ns=(ts, ts))
    orig_aaf_bytes = aaf_path.read_bytes()
    orig_aaf_mtime = aaf_path.stat().st_mtime_ns

    plan = plan_collect(aaf_path)
    states = {it.state for it in plan.items}
    assert states == {"ready", "missing", "embedded"}
    assert sum(1 for it in plan.items if it.state == "ready") == 2
    assert sum(1 for it in plan.items if it.state == "missing") == 1
    assert sum(1 for it in plan.items if it.state == "embedded") == 1

    dest_dir = tmp_path / "aaf_collected"
    res = run_collect(plan, dest_dir)
    assert res.exit_code == 3  # 1 missing
    assert res.session_path.is_file()
    assert res.report.counts["copied"] == 2
    assert res.report.counts["missing"] == 1
    assert res.report.counts["embedded"] == 1

    media_dir = dest_dir / "project Media"
    copied_w1 = media_dir / "dialog_one.wav"
    copied_w2 = media_dir / "rel_two.wav"
    assert copied_w1.is_file()
    assert copied_w2.is_file()
    assert (
        hashlib.sha256(copied_w1.read_bytes()).hexdigest()
        == hashlib.sha256(wav1.read_bytes()).hexdigest()
    )
    assert (
        hashlib.sha256(copied_w2.read_bytes()).hexdigest()
        == hashlib.sha256(wav2.read_bytes()).hexdigest()
    )

    # Inspecionar Locators no AAF coletado
    with aaf2.open(str(res.session_path), "r") as f:
        for mob in f.content.sourcemobs():
            desc = getattr(mob, "descriptor", None)
            if desc is None or type(desc).__name__ != "WAVEDescriptor":
                continue
            locs = [loc["URLString"].value for loc in desc["Locator"].value]
            if getattr(mob, "essence", None) is not None:
                # Embedded stays embedded and its historical Locator stays untouched
                assert locs == [fx["hist_embedded_url"]]
                stream = mob.essence.open("r")
                data = stream.read()
                stream.close()
                assert len(data) > 48000
            elif any("dialog_one.wav" in u for u in locs):
                # Ambos os Locators do descritor apontam para a copia como URI file:///
                assert len(locs) == 2
                assert locs[0] == copied_w1.resolve().as_uri()
                assert locs[1] == copied_w1.resolve().as_uri()
                assert locs[0].startswith("file:///")
            elif any("rel_two.wav" in u for u in locs):
                assert locs == [copied_w2.resolve().as_uri()]
                assert locs[0].startswith("file:///")
            elif any("gone_three.wav" in u for u in locs):
                assert locs == [fx["missing_wav"].resolve().as_uri()]

    # Reler AAF original e AAF coletado com read_aaf: mesmos clips exceto caminhos
    rep_orig = Report(strict=False)
    sess_orig = read_aaf(aaf_path, rep_orig, extract_dir=tmp_path / "ext_orig")
    for asset in sess_orig.media:
        if asset.status != "resolved":
            resolve_asset(asset, [aaf_path.parent, aaf_path.parent / "subfolder"], rep_orig)

    rep_coll = Report(strict=False)
    sess_coll = read_aaf(res.session_path, rep_coll, extract_dir=tmp_path / "ext_coll")
    for asset in sess_coll.media:
        if asset.status != "resolved":
            resolve_asset(asset, [dest_dir, media_dir], rep_coll)

    orig_clips = [
        (
            c.name,
            c.timeline_start_samples,
            c.timeline_end_samples,
            c.source_offset_samples,
            c.gain_linear,
        )
        for t in sess_orig.tracks
        for c in t.clips
    ]
    coll_clips = [
        (
            c.name,
            c.timeline_start_samples,
            c.timeline_end_samples,
            c.source_offset_samples,
            c.gain_linear,
        )
        for t in sess_coll.tracks
        for c in t.clips
    ]
    assert coll_clips == orig_clips

    # Verificar que os assets copiados resolveram dentro da pasta coletada
    resolved_in_coll = [
        Path(a.resolved_path).resolve()
        for a in sess_coll.media
        if (a.status == "resolved" and a.embedded_mob_id is None)
        or (a.resolved_path and "Media" in a.resolved_path)
    ]
    assert copied_w1.resolve() in resolved_in_coll
    assert copied_w2.resolve() in resolved_in_coll

    # Original intacto
    assert aaf_path.read_bytes() == orig_aaf_bytes
    assert aaf_path.stat().st_mtime_ns == orig_aaf_mtime


def test_multiple_descriptor_and_video_descriptor_in_aaf(tmp_path):
    """Testa MultipleDescriptor e CDCIDescriptor (video) em AAF."""
    src_dir = tmp_path / "multi_src"
    src_dir.mkdir()
    wav = _write_wav(src_dir / "track.wav", frames=48000)
    vid = src_dir / "picture.bin"  # extensao nao-video mas CDCIDescriptor
    vid.write_bytes(b"\x00" * 1024)
    aaf_path = src_dir / "multi.aaf"
    build_linked_aaf(aaf_path, [(0, 48000, 0)], wav)

    with aaf2.open(str(aaf_path), "rw") as f:
        mob = next(
            m for m in f.content.sourcemobs() if type(m.descriptor).__name__ == "WAVEDescriptor"
        )
        child = mob.descriptor
        multi = f.create.MultipleDescriptor()
        multi["SampleRate"].value = SAMPLE_RATE
        multi["Length"].value = 48000
        mob.descriptor = multi
        multi["FileDescriptors"].append(child)

        # Adicionar SourceMob com CDCIDescriptor apontando para picture.bin
        vid_mob = f.create.SourceMob("video_mob")
        cdci = f.create.CDCIDescriptor()
        cdci["SampleRate"].value = aaf2.rational.AAFRational(24, 1)
        cdci["Length"].value = 24
        cdci["StoredWidth"].value = 1920
        cdci["StoredHeight"].value = 1080
        cdci["FrameLayout"].value = "FullFrame"
        cdci["VideoLineMap"].value = [0, 0]
        cdci["ImageAspectRatio"].value = aaf2.rational.AAFRational(16, 9)
        cdci["ComponentWidth"].value = 8
        cdci["HorizontalSubsampling"].value = 2
        vloc = f.create.NetworkLocator()
        vloc["URLString"].value = vid.resolve().as_uri()
        cdci["Locator"].append(vloc)
        vid_mob.descriptor = cdci
        f.content.mobs.append(vid_mob)

    plan = plan_collect(aaf_path)
    kinds = {Path(it.source).name: it.kind for it in plan.items}
    assert kinds["track.wav"] == "audio"
    assert kinds["picture.bin"] == "video"

    # Com include_video=False, picture.bin fica video_skipped
    dest_no_vid = tmp_path / "multi_no_vid"
    res_nv = run_collect(plan, dest_no_vid, include_video=False)
    assert res_nv.report.counts["copied"] == 1
    assert res_nv.report.counts["video_skipped"] == 1

    with aaf2.open(str(res_nv.session_path), "r") as f:
        for mob in f.content.sourcemobs():
            for d in iter_descriptors(getattr(mob, "descriptor", None)):
                if type(d).__name__ == "WAVEDescriptor":
                    url = d["Locator"].value[0]["URLString"].value
                    assert url == (dest_no_vid / "multi Media" / "track.wav").resolve().as_uri()


def test_locator_sem_url_nao_para_o_collect(tmp_path):
    # Um TextLocator ao lado do NetworkLocator nao tem URLString: fica como
    # esta, e o NetworkLocator passa a apontar para a copia.
    wav = _write_wav(tmp_path / "origem" / "fala.wav")
    aaf_path = tmp_path / "origem" / "corte.aaf"
    build_linked_aaf(aaf_path, [(0, 48000, 0)], wav, track_name="DX 1")
    with aaf2.open(str(aaf_path), "rw") as f:
        mob = next(
            m for m in f.content.sourcemobs() if type(m.descriptor).__name__ == "WAVEDescriptor"
        )
        texto = f.create.TextLocator()
        texto["Name"].value = "nota do editor"
        mob.descriptor["Locator"].append(texto)

    res = run_collect(aaf_path, tmp_path / "entrega")

    copia = (tmp_path / "entrega" / "corte Media" / "fala.wav").resolve()
    assert res.report.count("copied") == 1
    with aaf2.open(str(res.session_path), "r") as f:
        mob = next(
            m for m in f.content.sourcemobs() if type(m.descriptor).__name__ == "WAVEDescriptor"
        )
        urls = [
            loc["URLString"].value
            for loc in mob.descriptor["Locator"].value
            if loc.get("URLString") is not None
        ]
        nomes = [
            loc["Name"].value
            for loc in mob.descriptor["Locator"].value
            if loc.get("Name") is not None
        ]
    assert urls == [copia.as_uri()]
    assert nomes == ["nota do editor"]


def test_aaf_somente_leitura_e_coletado(tmp_path):
    import stat

    wav = _write_wav(tmp_path / "origem" / "fala.wav")
    aaf_path = tmp_path / "origem" / "arquivo.aaf"
    build_linked_aaf(aaf_path, [(0, 48000, 0)], wav, track_name="DX 1")
    aaf_path.chmod(stat.S_IREAD)
    try:
        res = run_collect(aaf_path, tmp_path / "entrega")
        assert res.report.count("copied") == 1
        assert not os.access(aaf_path, os.W_OK)
    finally:
        aaf_path.chmod(stat.S_IREAD | stat.S_IWRITE)
