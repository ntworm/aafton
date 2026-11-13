"""
Testes para os defeitos 1, 2, 3 e 4 do aaf_write.py.

D1: descriptor duração era sempre 2880000 (60s fixo) — agora usa asset.duration_samples.
D2: mídia AIFF gerava WAVEDescriptor inválido — agora usa AIFCDescriptor.
D3: _interleave_wav quebrava com AIFF (KeyError fmt_payload + endianness errado).
D4: except limpeza sobrescrevia exceção original.
"""

import struct
import wave
import aifc
import pytest
import aaf2
from pathlib import Path

from aafton.aaf_write import write_aaf
from aafton.ir import ClipIR, MediaAsset, SessionIR, TrackIR
from aafton.report import Report
from aafton.timecode import TimecodeRate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_session(tmp_path, assets, clips_per_track):
    """Monta um SessionIR mínimo com assets e clips fornecidos."""
    session = SessionIR(
        name="test",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=0,
    )
    for asset in assets:
        session.media.append(asset)

    for i, (asset, clip_list) in enumerate(zip(assets, clips_per_track)):
        track = TrackIR(
            id=f"t{i}", source_index=i, name=f"Track {i + 1}", kind="audio", channel_count=1
        )
        for start, dur in clip_list:
            track.clips.append(
                ClipIR(
                    id=f"t{i}c{start}",
                    track_id=f"t{i}",
                    event_index=0,
                    name="clip",
                    media_id=asset.id,
                    timeline_start_samples=start,
                    timeline_end_samples=start + dur,
                    source_offset_samples=0,
                    channel_index=0,
                )
            )
        session.tracks.append(track)
    return session


def make_wav(path: Path, channels: int, sample_rate: int, n_samples: int) -> None:
    with wave.open(str(path), "wb") as f:
        f.setnchannels(channels)
        f.setsampwidth(2)
        f.setframerate(sample_rate)
        f.writeframes(b"\x00\x00" * channels * n_samples)


def make_aiff(path: Path, channels: int, sample_rate: int, n_samples: int) -> None:
    with aifc.open(str(path), "wb") as f:
        f.setnchannels(channels)
        f.setsampwidth(2)
        f.setframerate(sample_rate)
        f.setnframes(n_samples)
        f.setcomptype(b"NONE", b"")
        f.writeframes(b"\x00\x00" * channels * n_samples)


def read_aaf_descriptors(aaf_path: Path):
    """Devolve lista de (length, descriptor_class) para cada SourceMob do AAF."""
    results = []
    with aaf2.open(str(aaf_path), "r") as f:
        for mob in f.content.mobs:
            if hasattr(mob, "descriptor") and mob.descriptor is not None:
                desc = mob.descriptor
                cls_name = type(desc).__name__
                length = desc.get("Length", {})
                if hasattr(length, "value"):
                    length = length.value
                results.append((length, cls_name))
    return results


def sourceclip_max_reach(aaf_path: Path):
    """Para cada SourceClip, calcula start + length. Devolve o máximo."""
    max_reach = 0
    with aaf2.open(str(aaf_path), "r") as f:
        for mob in f.content.mobs:
            for slot in mob.slots:
                seg = getattr(slot, "segment", None)
                if seg is None:
                    continue
                comps = getattr(seg, "components", [])
                for comp in comps:
                    if type(comp).__name__ == "SourceClip":
                        reach = int(getattr(comp, "start", 0)) + int(getattr(comp, "length", 0))
                        if reach > max_reach:
                            max_reach = reach
    return max_reach


# ---------------------------------------------------------------------------
# Defeito 1: duração dos descritores
# ---------------------------------------------------------------------------


class TestDefect1Duration:
    def test_descriptor_length_matches_asset_duration(self, tmp_path):
        """Nenhum descritor deve ter o comprimento literal de 2880000 (60s)."""
        wav = tmp_path / "audio.wav"
        make_wav(wav, 1, 48000, 96000)  # 2 segundos

        asset = MediaAsset(
            id="m0",
            filename="audio.wav",
            channels=1,
            sample_rate=48000,
            duration_samples=96000,
            status="resolved",
            resolved_path=str(wav),
        )
        session = make_session(tmp_path, [asset], [[(0, 96000)]])
        out = tmp_path / "out.aaf"
        report = Report(strict=False)
        write_aaf(session, out, report)

        descriptors = read_aaf_descriptors(out)
        lengths = [item for item, _ in descriptors if isinstance(item, int)]
        assert lengths, "Nenhum descritor com Length encontrado"
        assert all(item != 2880000 for item in lengths), (
            f"Comprimento literal 2880000 ainda presente: {lengths}"
        )
        assert any(item == 96000 for item in lengths), (
            f"Esperado 96000 em algum descritor, got: {lengths}"
        )

    def test_no_sourceclip_exceeds_descriptor_length(self, tmp_path):
        """Invariante: nenhum SourceClip pede além do comprimento declarado no descritor."""
        wav = tmp_path / "audio.wav"
        n = 200000
        make_wav(wav, 1, 48000, n)

        asset = MediaAsset(
            id="m0",
            filename="audio.wav",
            channels=1,
            sample_rate=48000,
            duration_samples=n,
            status="resolved",
            resolved_path=str(wav),
        )
        session = make_session(tmp_path, [asset], [[(0, n)]])
        out = tmp_path / "out.aaf"
        write_aaf(session, out, Report(strict=False))

        # descriptor length for this asset
        descriptors = read_aaf_descriptors(out)
        desc_lengths = {item for item, _ in descriptors if isinstance(item, int)}
        assert len(desc_lengths) >= 1
        min_desc = min(desc_lengths)
        # the max reach of any SourceClip referencing this asset
        max_reach = sourceclip_max_reach(out)
        assert max_reach <= min_desc, f"SourceClip alcança {max_reach} > descriptor {min_desc}"

    def test_asset_without_duration_samples_deduces_from_clips(self, tmp_path):
        """Asset sem duration_samples: comprimento deduzido dos clips, aviso emitido."""
        wav = tmp_path / "audio.wav"
        make_wav(wav, 1, 48000, 300000)

        asset = MediaAsset(
            id="m0",
            filename="audio.wav",
            channels=1,
            sample_rate=48000,
            duration_samples=None,  # não lido
            status="resolved",
            resolved_path=str(wav),
        )
        session = make_session(tmp_path, [asset], [[(0, 150000)]])
        out = tmp_path / "out.aaf"
        report = Report(strict=False)
        write_aaf(session, out, report)

        codes = [i.code for i in report.issues]
        assert "MEDIA_DURATION_DEDUCED" in codes

        descriptors = read_aaf_descriptors(out)
        lengths = [item for item, _ in descriptors if isinstance(item, int)]
        assert any(item == 150000 for item in lengths), (
            f"Esperado 150000 deduzido dos clips, got {lengths}"
        )


# ---------------------------------------------------------------------------
# Defeito 2: descriptor AIFF
# ---------------------------------------------------------------------------


class TestDefect2AIFFDescriptor:
    def test_aiff_asset_gets_aifc_descriptor(self, tmp_path):
        aif = tmp_path / "audio.aif"
        make_aiff(aif, 1, 48000, 48000)

        asset = MediaAsset(
            id="m0",
            filename="audio.aif",
            channels=1,
            sample_rate=48000,
            duration_samples=48000,
            status="resolved",
            resolved_path=str(aif),
        )
        session = make_session(tmp_path, [asset], [[(0, 48000)]])
        out = tmp_path / "out.aaf"
        report = Report(strict=False)
        write_aaf(session, out, report)

        descriptors = read_aaf_descriptors(out)
        cls_names = [cls for _, cls in descriptors]
        assert any("AIFC" in c for c in cls_names), f"Esperado AIFCDescriptor, got: {cls_names}"
        assert not any("WAVE" in c for c in cls_names), (
            f"WAVEDescriptor nao deveria aparecer: {cls_names}"
        )

    def test_wav_asset_keeps_wave_descriptor(self, tmp_path):
        wav = tmp_path / "audio.wav"
        make_wav(wav, 1, 48000, 48000)

        asset = MediaAsset(
            id="m0",
            filename="audio.wav",
            channels=1,
            sample_rate=48000,
            duration_samples=48000,
            status="resolved",
            resolved_path=str(wav),
        )
        session = make_session(tmp_path, [asset], [[(0, 48000)]])
        out = tmp_path / "out.aaf"
        write_aaf(session, out, Report(strict=False))

        descriptors = read_aaf_descriptors(out)
        cls_names = [cls for _, cls in descriptors]
        assert any("WAVE" in c for c in cls_names), f"Esperado WAVEDescriptor, got: {cls_names}"

    def test_mixed_wav_and_aiff_exports_correct_descriptors(self, tmp_path):
        wav = tmp_path / "aud.wav"
        aif = tmp_path / "aud.aif"
        make_wav(wav, 1, 48000, 48000)
        make_aiff(aif, 1, 48000, 48000)

        a_wav = MediaAsset(
            id="m0",
            filename="aud.wav",
            channels=1,
            sample_rate=48000,
            duration_samples=48000,
            status="resolved",
            resolved_path=str(wav),
        )
        a_aif = MediaAsset(
            id="m1",
            filename="aud.aif",
            channels=1,
            sample_rate=48000,
            duration_samples=48000,
            status="resolved",
            resolved_path=str(aif),
        )

        session = SessionIR(
            name="mixed",
            sample_rate=48000,
            timecode_rate=TimecodeRate(25, 1),
            start_timecode_samples=0,
        )
        session.media.extend([a_wav, a_aif])
        for i, a in enumerate([a_wav, a_aif]):
            t = TrackIR(id=f"t{i}", source_index=i, name=f"T{i}", kind="audio", channel_count=1)
            t.clips.append(
                ClipIR(
                    id=f"c{i}",
                    track_id=f"t{i}",
                    event_index=0,
                    name="clip",
                    media_id=a.id,
                    timeline_start_samples=0,
                    timeline_end_samples=48000,
                    source_offset_samples=0,
                    channel_index=0,
                )
            )
            session.tracks.append(t)

        out = tmp_path / "out.aaf"
        write_aaf(session, out, Report(strict=False))

        descriptors = read_aaf_descriptors(out)
        cls_names = [cls for _, cls in descriptors]
        assert any("WAVE" in c for c in cls_names)
        assert any("AIFC" in c for c in cls_names)


# ---------------------------------------------------------------------------
# Defeito 3: entrelaçamento AIFF
# ---------------------------------------------------------------------------


class TestDefect3AIFFInterleave:
    def _make_aiff_mono(self, path, sample_rate, samples, pattern=None):
        """Grava AIFF mono com padrão fixo para verificar endianness."""
        import struct as s

        if pattern is None:
            pattern = [1000 * (i % 10) for i in range(samples)]
        # Write raw AIFF big-endian
        pcm = s.pack(f">{samples}h", *pattern)
        comm = s.pack(">HlH", 1, samples, 16)
        # float 80-bit for sample_rate
        if sample_rate == 48000:
            sr_float = b"\x40\x0e\xbb\x80\x00\x00\x00\x00\x00\x00"
        elif sample_rate == 44100:
            sr_float = b"\x40\x0d\xac\x44\x00\x00\x00\x00\x00\x00"
        else:
            raise ValueError(f"Unsupported sample_rate {sample_rate} in test helper")
        comm += sr_float
        comm_chunk = b"COMM" + s.pack(">I", len(comm)) + comm
        ssnd_data = s.pack(">II", 0, 0) + pcm  # offset=0, blockSize=0
        ssnd_chunk = b"SSND" + s.pack(">I", len(ssnd_data)) + ssnd_data
        body = b"AIFF" + comm_chunk + ssnd_chunk
        path.write_bytes(b"FORM" + s.pack(">I", len(body)) + body)
        return pattern

    def test_aiff_mono_interleave_samples_correct(self, tmp_path):
        """Duas AIFF mono entrelacam em WAV estéreo com amostras na ordem certa."""
        from aafton.media_extract import read_audio_metadata
        from aafton.stereo import _interleave_wav

        n = 100
        pat_l = [i * 10 for i in range(n)]
        pat_r = [i * 20 for i in range(n)]

        aif_l = tmp_path / "l.aif"
        aif_r = tmp_path / "r.aif"
        self._make_aiff_mono(aif_l, 48000, n, pat_l)
        self._make_aiff_mono(aif_r, 48000, n, pat_r)

        meta_l = read_audio_metadata(aif_l)
        meta_r = read_audio_metadata(aif_r)
        out = tmp_path / "stereo.wav"
        _interleave_wav(aif_l, aif_r, out, meta_l, meta_r)

        # Read result and verify samples
        with wave.open(str(out), "rb") as w:
            assert w.getnchannels() == 2
            assert w.getframerate() == 48000
            raw = w.readframes(n)

        samples = struct.unpack(f"<{n * 2}h", raw)
        for i in range(n):
            assert samples[i * 2] == pat_l[i], f"L[{i}] esperado {pat_l[i]}, got {samples[i * 2]}"
            assert samples[i * 2 + 1] == pat_r[i], (
                f"R[{i}] esperado {pat_r[i]}, got {samples[i * 2 + 1]}"
            )

    def _make_aifc_sowt_mono(self, path, sample_rate, samples, pattern=None):
        """Grava AIFC com compressao 'sowt', que e PCM *little-endian*.

        E o formato que a Apple grava. As amostras ja estao na ordem do WAV,
        entao o entrelacamento nao pode troca-las: trocar aqui produz um
        arquivo do tamanho exato e cheio de ruido.
        """
        import struct as s

        if pattern is None:
            pattern = [1000 * (i % 10) for i in range(samples)]
        pcm = s.pack(f"<{samples}h", *pattern)
        if sample_rate == 48000:
            sr_float = b"\x40\x0e\xbb\x80\x00\x00\x00\x00\x00\x00"
        else:
            raise ValueError(f"Unsupported sample_rate {sample_rate} in test helper")
        comm = s.pack(">HlH", 1, samples, 16) + sr_float + b"sowt" + b"\x00"
        comm_chunk = b"COMM" + s.pack(">I", len(comm)) + comm
        if len(comm) % 2:
            comm_chunk += b"\x00"
        ssnd_data = s.pack(">II", 0, 0) + pcm
        ssnd_chunk = b"SSND" + s.pack(">I", len(ssnd_data)) + ssnd_data
        fver_chunk = b"FVER" + s.pack(">I", 4) + s.pack(">I", 0xA2805140)
        body = b"AIFC" + fver_chunk + comm_chunk + ssnd_chunk
        path.write_bytes(b"FORM" + s.pack(">I", len(body)) + body)
        return pattern

    def test_aifc_sowt_interleave_nao_troca_bytes(self, tmp_path):
        """Fonte 'sowt' ja e little-endian e precisa passar intacta.

        Controle do teste irmao: la a fonte e big-endian e *tem* que ser
        trocada. Se o entrelacamento trocar sempre, ou nunca, um dos dois
        reprova. Sem este par, uma troca incondicional passa despercebida.
        """
        from aafton.media_extract import read_audio_metadata
        from aafton.stereo import _interleave_wav

        n = 64
        pat_l = [i * 7 - 200 for i in range(n)]
        pat_r = [300 - i * 5 for i in range(n)]

        aif_l = tmp_path / "l_sowt.aif"
        aif_r = tmp_path / "r_sowt.aif"
        self._make_aifc_sowt_mono(aif_l, 48000, n, pat_l)
        self._make_aifc_sowt_mono(aif_r, 48000, n, pat_r)

        meta_l = read_audio_metadata(aif_l)
        meta_r = read_audio_metadata(aif_r)
        assert meta_l["is_big_endian"] is False, "sowt e little-endian"

        out = tmp_path / "stereo_sowt.wav"
        _interleave_wav(aif_l, aif_r, out, meta_l, meta_r)

        with wave.open(str(out), "rb") as w:
            assert w.getnchannels() == 2
            raw = w.readframes(n)

        samples = struct.unpack(f"<{n * 2}h", raw)
        for i in range(n):
            assert samples[i * 2] == pat_l[i], f"L[{i}] esperado {pat_l[i]}, got {samples[i * 2]}"
            assert samples[i * 2 + 1] == pat_r[i], (
                f"R[{i}] esperado {pat_r[i]}, got {samples[i * 2 + 1]}"
            )

    def test_aiff_interleave_no_exception_on_bad_media(self, tmp_path):
        """Falha de mídia durante entrelaçamento vira issue, não exceção crua."""
        from aafton.stereo import process_stereo_pairing

        # create two AIFF files
        aif_l = tmp_path / "x_L.aif"
        aif_r = tmp_path / "x_R.aif"
        self._make_aiff_mono(aif_l, 48000, 50)
        self._make_aiff_mono(aif_r, 48000, 50)

        # corrupt the right file to force interleave failure
        aif_r.write_bytes(b"\x00" * 20)

        session = SessionIR(
            name="s",
            sample_rate=48000,
            timecode_rate=TimecodeRate(25, 1),
            start_timecode_samples=0,
        )
        a_l = MediaAsset(
            id="ml",
            filename="x_L.aif",
            channels=1,
            sample_rate=48000,
            duration_samples=50,
            status="resolved",
            resolved_path=str(aif_l),
        )
        a_r = MediaAsset(
            id="mr",
            filename="x_R.aif",
            channels=1,
            sample_rate=48000,
            duration_samples=50,
            status="resolved",
            resolved_path=str(aif_r),
        )
        session.media.extend([a_l, a_r])

        t_l = TrackIR(id="tl", source_index=0, name="x [L]", kind="audio", channel_count=1)
        t_r = TrackIR(id="tr", source_index=1, name="x [R]", kind="audio", channel_count=1)
        t_l.clips.append(
            ClipIR(
                id="cl",
                track_id="tl",
                event_index=0,
                name="c",
                media_id="ml",
                timeline_start_samples=0,
                timeline_end_samples=50,
                source_offset_samples=0,
                channel_index=0,
            )
        )
        t_r.clips.append(
            ClipIR(
                id="cr",
                track_id="tr",
                event_index=0,
                name="c",
                media_id="mr",
                timeline_start_samples=0,
                timeline_end_samples=50,
                source_offset_samples=0,
                channel_index=0,
            )
        )
        session.tracks.extend([t_l, t_r])

        report = Report(strict=False)
        extract = tmp_path / "out"
        extract.mkdir()
        # should not raise
        process_stereo_pairing(session, "pair", report, extract)
        codes = [i.code for i in report.issues]
        # Nomear o codigo importa: STEREO_INTERLEAVED e info de rotina e sai
        # sempre, entao uma assercao por familia passaria mesmo se a falha
        # nao fosse relatada.
        assert "MEDIA_METADATA_UNREADABLE" in codes, (
            f"Esperado aviso da midia ilegivel, got: {codes}"
        )


# ---------------------------------------------------------------------------
# Defeito 4: limpeza na excecao
# ---------------------------------------------------------------------------


class TestDefect4ExceptionCleanup:
    def test_original_exception_survives_cleanup(self, tmp_path, monkeypatch):
        """A exceção original sobe, não a do unlink."""
        from aafton import aaf_write

        class MyError(Exception):
            pass

        original_open = aaf2.open

        def bad_open(*args, **kwargs):
            ctx = original_open(*args, **kwargs)
            original_enter = ctx.__enter__

            class FakeCtx:
                def __enter__(self_):
                    return original_enter()

                def __exit__(self_, exc_type, exc_val, exc_tb):
                    raise MyError("erro original de escrita")

            return FakeCtx()

        monkeypatch.setattr(aaf_write.aaf2, "open", bad_open)

        session = SessionIR(
            name="x",
            sample_rate=48000,
            timecode_rate=TimecodeRate(25, 1),
            start_timecode_samples=0,
        )
        out = tmp_path / "out.aaf"
        with pytest.raises(MyError, match="erro original"):
            write_aaf(session, out, Report(strict=False))


class TestAutoConferenciaDoExport:
    """A conferencia do proprio AAF exportado.

    Existe porque o `aafton verify` compara o AAF de origem com o `.als` e nunca
    abre o AAF exportado. Foi por essa fresta que passou um `duration_ts` fixo
    em sessenta segundos.
    """

    def _sessao(self, tmp_path, duracao_asset, fim_do_clip):
        wav = tmp_path / "a.wav"
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(48000)
            w.writeframes(b"\x00\x00" * duracao_asset)

        session = SessionIR(
            name="s",
            sample_rate=48000,
            timecode_rate=TimecodeRate(25, 1),
            start_timecode_samples=0,
        )
        session.media.append(
            MediaAsset(
                id="m0",
                filename="a.wav",
                channels=1,
                sample_rate=48000,
                duration_samples=duracao_asset,
                status="resolved",
                resolved_path=str(wav),
            )
        )
        track = TrackIR(id="t0", source_index=0, name="A", kind="audio", channel_count=1)
        track.clips.append(
            ClipIR(
                id="c0",
                track_id="t0",
                event_index=0,
                name="c",
                media_id="m0",
                timeline_start_samples=0,
                timeline_end_samples=fim_do_clip,
                source_offset_samples=0,
                channel_index=0,
            )
        )
        session.tracks.append(track)
        return session

    def test_clip_que_cabe_na_fonte_passa(self, tmp_path):
        session = self._sessao(tmp_path, duracao_asset=48000, fim_do_clip=24000)
        report = Report(strict=False)
        write_aaf(session, tmp_path / "ok.aaf", report)
        codes = [i.code for i in report.issues]
        assert "EXPORT_SELF_CHECK_OK" in codes, codes
        assert "EXPORT_SOURCECLIP_EXCEEDS_SOURCE" not in codes

    def test_clip_que_passa_do_fim_da_fonte_e_acusado(self, tmp_path):
        """O caso do `duration_ts` fixo: o clip pede mais do que a fonte tem.

        Sem esta assercao, reintroduzir um comprimento inventado no descritor
        volta a passar despercebido, que foi exatamente o que aconteceu.
        """
        session = self._sessao(tmp_path, duracao_asset=48000, fim_do_clip=96000)
        report = Report(strict=False)
        write_aaf(session, tmp_path / "ruim.aaf", report)
        acusacoes = [i for i in report.issues if i.code == "EXPORT_SOURCECLIP_EXCEEDS_SOURCE"]
        assert len(acusacoes) == 1, [i.code for i in report.issues]
        assert acusacoes[0].context["count"] == 1
        assert acusacoes[0].context["worst_requested"] == 96000
        assert acusacoes[0].context["worst_declared"] == 48000

    def test_a_cadeia_atravessa_a_sequence_do_mastermob(self, tmp_path):
        """O MasterMob do pyaaf2 guarda uma Sequence, nao um SourceClip direto.

        Se o percurso nao entrar na Sequence, ele nao acha descritor nenhum e a
        conferencia degrada para 'nao sei dizer' sem nunca acusar nada.
        """
        session = self._sessao(tmp_path, duracao_asset=48000, fim_do_clip=24000)
        report = Report(strict=False)
        write_aaf(session, tmp_path / "cadeia.aaf", report)
        codes = [i.code for i in report.issues]
        assert "EXPORT_SOURCECLIP_SEM_DESCRITOR" not in codes, (
            "o percurso parou antes do descritor: " + str(codes)
        )
