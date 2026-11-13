"""Fades que a fonte nao cobre sao mantidos como clip, nao absorvidos.

Absorver um fade-out estende o clip anterior por cima da regiao do fade, lendo
do mesmo arquivo. Quando o Pro Tools rendeu um fade mais longo do que o material
que sobra depois do corte, essa extensao aponta para alem do fim do arquivo e o
trecho toca silencio no lugar do fade.

Medido na TESTE_AFF_PROTOOLS: o `Fade 71` tem 417792 samples e restava 1 segundo
de material, entao a extensao pedia 1475584 de um arquivo com 1105792, e sete
segundos e meio de fade-out sumiam. O AAF de origem e internamente consistente;
era a nossa conversao que passava do fim.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aafton.aaf_read import _fonte_cobre_o_fade, read_aaf
from aafton.ir import ClipIR, MediaAsset
from aafton.report import Report

PT_AAF_PATH = Path("tests/fixtures/aaf/TESTE_AFF_PROTOOLS.aaf")


def _item(offset, inicio, fim, duracao_do_arquivo):
    clip = ClipIR(
        id="c",
        track_id="t",
        event_index=0,
        name="c",
        media_id="m",
        timeline_start_samples=inicio,
        timeline_end_samples=fim,
        source_offset_samples=offset,
        channel_index=0,
    )
    asset = MediaAsset(
        id="m",
        filename="a.wav",
        channels=1,
        sample_rate=48000,
        duration_samples=duracao_do_arquivo,
        status="resolved",
    )
    return {"kind": "CLIP", "clip_assets": [(clip, asset)]}


class TestDecisaoDeCobertura:
    def test_cabe_folgado(self):
        # clip usa 0..1000 de um arquivo de 10000; um fade de 500 cabe
        assert _fonte_cobre_o_fade([_item(0, 0, 1000, 10000)], 500) is True

    def test_cabe_exatamente(self):
        # sobram exatamente 500 samples e o fade pede 500
        assert _fonte_cobre_o_fade([_item(0, 0, 9500, 10000)], 500) is True

    def test_falta_um_sample(self):
        # sobram 499 e o fade pede 500. O limite importa: e aqui que a
        # extensao comeca a apontar para alem do fim do arquivo.
        assert _fonte_cobre_o_fade([_item(0, 0, 9501, 10000)], 500) is False

    def test_offset_conta(self):
        # o clip comeca em 9000 do arquivo e usa 500; sobram 500
        assert _fonte_cobre_o_fade([_item(9000, 0, 500, 10000)], 500) is True
        assert _fonte_cobre_o_fade([_item(9000, 0, 500, 10000)], 501) is False

    def test_sem_numero_nao_recusa(self):
        """Sem duracao conhecida, mantem a absorcao, que e o padrao.

        Recusar por falta de informacao trocaria fades editaveis por clips em
        todo AAF que nao declare o comprimento da fonte.
        """
        item = _item(0, 0, 1000, 10000)
        item["clip_assets"][0][1].duration_samples = None
        assert _fonte_cobre_o_fade([item], 10**9) is True

    def test_sem_clip_anterior_nao_recusa(self):
        assert _fonte_cobre_o_fade([], 500) is True
        assert _fonte_cobre_o_fade([{"kind": "FILLER"}], 500) is True


@pytest.mark.skipif(not PT_AAF_PATH.exists(), reason="fixture ausente")
class TestSobreMaterialReal:
    def test_nenhum_clip_passa_do_fim_do_seu_arquivo(self):
        """A invariante que o defeito violava, afirmada sobre o IR.

        Vale tanto quanto a conferencia do AAF exportado: esta pega a causa,
        aquela pega a consequencia.
        """
        session = read_aaf(PT_AAF_PATH, Report(strict=False), fades="absorb")
        por_id = {m.id: m for m in session.media}

        excedentes = []
        for track in session.tracks:
            for clip in track.clips:
                asset = por_id.get(clip.media_id)
                if asset is None or asset.duration_samples is None:
                    continue
                pedido = (clip.source_offset_samples or 0) + clip.duration_samples
                if pedido > asset.duration_samples:
                    excedentes.append((track.name, clip.name, pedido, asset.duration_samples))

        assert not excedentes, (
            f"{len(excedentes)} clips leem alem do fim do arquivo: {excedentes[:3]}"
        )

    def test_seis_fades_sao_mantidos_e_nenhum_some(self):
        report = Report(strict=False)
        read_aaf(PT_AAF_PATH, report, fades="absorb")

        mantidos = [i for i in report.issues if i.code == "FADE_KEPT_SUMMARY"]
        absorvidos = [i for i in report.issues if i.code == "FADE_RENDER_DISCARDED"]
        assert len(mantidos) == 1 and len(absorvidos) == 1

        assert mantidos[0].context["fades_kept"] == 6
        assert absorvidos[0].context["fades_absorbed"] == 95
        # Os fades mudam de destino, nunca desaparecem.
        assert mantidos[0].context["fades_kept"] + absorvidos[0].context["fades_absorbed"] == 101

    def test_cada_fade_mantido_e_nomeado(self):
        """O aviso por fade diz a track, para o operador achar na sessao."""
        report = Report(strict=False)
        read_aaf(PT_AAF_PATH, report, fades="absorb")
        por_fade = [i for i in report.issues if i.code == "FADE_KEPT_SOURCE_TOO_SHORT"]
        assert len(por_fade) == 6
        for issue in por_fade:
            assert issue.context.get("track")
            assert issue.context.get("fade_samples", 0) > 0
