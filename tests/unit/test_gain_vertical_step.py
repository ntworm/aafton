from aafton.aaf_read import _extract_gain_curve


def test_extract_gain_curve_mantem_degrau_vertical(monkeypatch):
    monkeypatch.setattr("aafton.aaf_read._read_point_list", lambda p: p)
    raw_items = [
        {
            "kind": "CLIP",
            "cursor_samples": 0,
            "length_samples": 100,
            "parameters": [(0.0, 0.5), (1.0, 0.5)],
        },
        {
            "kind": "CLIP",
            "cursor_samples": 100,
            "length_samples": 100,
            "parameters": [(0.0, 0.8), (1.0, 0.8)],
        },
    ]
    points = _extract_gain_curve(raw_items, 0)
    assert points == [(0, 0.5), (100, 0.5), (100, 0.8), (200, 0.8)]


def test_extract_gain_curve_tres_pontos_mesmo_instante_sobrevivem(monkeypatch):
    monkeypatch.setattr("aafton.aaf_read._read_point_list", lambda p: p)
    raw_items = [
        {
            "kind": "CLIP",
            "cursor_samples": 0,
            "length_samples": 100,
            "parameters": [(0.0, 0.5), (1.0, 0.5)],
        },
        {
            "kind": "CLIP",
            "cursor_samples": 100,
            "length_samples": 100,
            "parameters": [(0.0, 0.8), (0.0, 0.9), (1.0, 0.9)],
        },
    ]
    points = _extract_gain_curve(raw_items, 0)
    assert points == [(0, 0.5), (100, 0.5), (100, 0.8), (100, 0.9), (200, 0.9)]


def test_extract_gain_curve_mesmo_instante_mesmo_valor_deduplica(monkeypatch):
    monkeypatch.setattr("aafton.aaf_read._read_point_list", lambda p: p)
    raw_items = [
        {
            "kind": "CLIP",
            "cursor_samples": 0,
            "length_samples": 100,
            "parameters": [(0.0, 0.5), (1.0, 0.5)],
        },
        {
            "kind": "CLIP",
            "cursor_samples": 100,
            "length_samples": 100,
            "parameters": [(0.0, 0.5), (1.0, 0.5)],
        },
    ]
    points = _extract_gain_curve(raw_items, 0)
    assert points == [(0, 0.5), (100, 0.5), (200, 0.5)]
