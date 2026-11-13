def test_verify_output_no_debug():
    from aafton.verify import VerifyResult

    res = VerifyResult(regime_name="teste", rows=[])
    txt = res.to_table()
    assert "DEBUG" not in txt
