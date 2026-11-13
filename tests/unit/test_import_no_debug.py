from aafton.ir import TimecodeRate


def test_import_nao_imprime_debug(tmp_path, capsys):
    from aafton.ir import SessionIR, TrackIR
    from aafton.aaf_write import write_aaf
    from aafton.report import Report
    from aafton.cli import main

    aaf = tmp_path / "teste_import.aaf"
    als = tmp_path / "teste_import.als"
    t0 = TrackIR(id="t0", source_index=0, name="Track 1", kind="audio", channel_count=1, clips=[])
    sessao = SessionIR(
        name="test",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1, False),
        start_timecode_samples=0,
        tracks=[t0],
        media=[],
    )
    write_aaf(sessao, aaf, Report(strict=False))

    args = ["import", str(aaf), str(als)]
    assert main(args) == 0
    captured = capsys.readouterr()

    # Prove that the first non-empty line is exactly # Relatorio de conversao
    lines = [line for line in captured.out.splitlines() if line.strip()]
    assert len(lines) > 0
    assert lines[0] == "# Conversion report"

    # Prove that no list of tags is printed (the old debug print printed things like ['AudioTrack', 'GroupTrack'])
    assert "['" not in captured.out
