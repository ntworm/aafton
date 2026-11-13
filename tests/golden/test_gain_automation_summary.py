import aaf2
from aafton.aaf_read import read_aaf
from aafton.report import Report


def count_varying_values(aaf_path):
    def walk_components(component):
        yield component
        if hasattr(component, "components"):
            for c in component.components:
                yield from walk_components(c)
        if hasattr(component, "segments"):
            for c in component.segments:
                yield from walk_components(c)
        if hasattr(component, "parameters"):
            for c in component.parameters:
                yield from walk_components(c)

    def _get_pts(param):
        prop = param.get("PointList") if hasattr(param, "get") else None
        if prop is None:
            try:
                prop = param["PointList"]
            except Exception:
                pass
        if prop is not None:
            return prop.value if hasattr(prop, "value") else prop
        return None

    count = 0
    with aaf2.open(aaf_path, "r") as f:
        for mob in f.content.mobs:
            if not isinstance(mob, aaf2.mobs.CompositionMob):
                continue
            for slot in mob.slots:
                if not getattr(slot, "segment", None):
                    continue
                for component in walk_components(slot.segment):
                    if "VaryingValue" in type(component).__name__:
                        pts = _get_pts(component)
                        if pts:
                            vals = set()
                            for p in pts:
                                v = (
                                    p.get("Value")
                                    if hasattr(p, "get")
                                    else getattr(p, "value", None)
                                )
                                if v is None:
                                    try:
                                        v = p["Value"]
                                    except Exception:
                                        pass
                                if v is not None:
                                    val = getattr(v, "value", v)
                                    vals.add(val)
                            if len(vals) > 1:
                                count += 1
    return count


def test_gain_automation_summary_matches_aaf_varying_values():
    paths = {
        "TESTE_AAF": ("tests/fixtures/aaf/TESTE_AFF_PROTOOLS.aaf", 28),
        "SPOT_AAF_TESTE": ("tests/fixtures/aaf/SPOT_AAF_TESTE.aaf", 8),
    }

    for name, (path, expected_count) in paths.items():
        # A. Conta direto do aaf2
        direct_count = count_varying_values(path)
        assert direct_count == expected_count, (
            f"{name}: expected {expected_count} varying values directly from aaf2, got {direct_count}"
        )

        # B. Roda o nosso read_aaf
        rep = Report(strict=False)
        read_aaf(path, rep)
        summary = [i for i in rep.issues if i.code == "GAIN_AUTOMATION_SUMMARY"]

        assert len(summary) == 1
        msg = summary[0].message

        # C. Verifica a mensagem
        assert f"Found {expected_count} gain automation ramps" in msg

        # Assert decomposicao = total
        ctx = summary[0].context
        assert ctx["total"] == expected_count
        assert ctx["clip"] + ctx["fade"] == expected_count
