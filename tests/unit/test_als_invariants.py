from aafton.report import Report
from aafton.aaf_read import read_aaf
from tests.support.paths import fixture_aaf


def test_no_overlapping_clips_on_same_track(tmp_path):
    pt_aaf = fixture_aaf("TESTE_AFF_PROTOOLS.aaf")

    report = Report(strict=False)
    session = read_aaf(pt_aaf, report, fades="absorb")

    for track in session.tracks:
        if not track.clips:
            continue

        # Ordena os clips pelo tempo de inicio
        clips = sorted(track.clips, key=lambda c: c.timeline_start_samples)

        for i in range(len(clips) - 1):
            curr = clips[i]
            nxt = clips[i + 1]
            # O proximo clip nao pode comecar antes do fim do anterior
            assert nxt.timeline_start_samples >= curr.timeline_end_samples, (
                f"Sobreposicao na track {track.name}: clip {nxt.name} comeca em {nxt.timeline_start_samples} "
                f"mas o anterior {curr.name} termina em {curr.timeline_end_samples}"
            )
