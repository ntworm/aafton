"""OperationGroup de ganho que envolve uma Sequence com varios clips.

O efeito vale para o grupo inteiro: cada clip de dentro sai com o ganho do
grupo multiplicado no proprio, nas posicoes que teria numa Sequence rasa.
"""

from __future__ import annotations

from pathlib import Path

import aaf2
from aaf2.auid import AUID

from aafton.aaf_read import read_aaf
from aafton.report import Report
from tests.aaf_factory import wav_bytes

TAXA = 48000


def _gain_op_def(f):
    param_def = f.create.ParameterDef(
        AUID("e4962321-2267-11d3-8a4c-0050040ef7d2"),
        "Amplitude",
        "",
        f.dictionary.lookup_typedef("Rational"),
    )
    f.dictionary["ParameterDefinitions"].append(param_def)
    op_def = f.create.OperationDef(AUID("9d2ea894-0968-11d3-8a38-0050040ef7d2"), "Audio Gain", "")
    op_def["IsTimeWarp"].value = False
    op_def["NumberInputs"].value = 1
    op_def["DataDefinition"].value = f.dictionary.lookup_datadef("Sound")
    op_def["ParametersDefined"].value.append(param_def)
    f.dictionary["OperationDefinitions"].append(op_def)
    return op_def, param_def


def _montar(path: Path, wav: Path, *, no_selector: bool = False, sem_grupo: bool = False) -> Path:
    """Track com Filler 1000, grupo [clip 2000, Filler 500, clip 1000] e clip 3000 depois."""
    with aaf2.open(str(path), "w") as f:
        composicao = f.create.CompositionMob("grupo")
        f.content.mobs.append(composicao)
        meta = {
            "format": {"filename": str(wav), "format_name": "wav"},
            "streams": [
                {
                    "codec_type": "audio",
                    "codec_name": "pcm_s16le",
                    "sample_rate": TAXA,
                    "duration_ts": TAXA * 60,
                    "channels": 1,
                }
            ],
        }
        master, _s, _t = f.content.link_external_wav(meta)
        slot = composicao.create_timeline_slot(edit_rate=TAXA)
        slot.name = "A1"
        externa = f.create.Sequence(media_kind="sound")
        slot.segment = externa

        def clip(n):
            c = f.create.SourceClip(media_kind="sound", start=0)
            c.length = n
            c.mob = master
            c.slot_id = 1
            return c

        interna = f.create.Sequence(media_kind="sound")
        interna.components.append(clip(2000))
        interna.components.append(f.create.Filler(media_kind="sound", length=500))
        interna.components.append(clip(1000))
        externa.components.append(f.create.Filler(media_kind="sound", length=1000))
        if sem_grupo:
            externa.components.append(interna)
        else:
            op_def, param_def = _gain_op_def(f)
            grupo = f.create.OperationGroup(op_def)
            grupo.media_kind = "sound"
            grupo.length = 3500
            grupo.segments.append(interna)
            grupo.parameters.append(
                f.create.ConstantValue(param_def, aaf2.rational.AAFRational(1, 2))
            )
            if no_selector:
                seletor = f.create.Selector(media_kind="sound", length=3500)
                seletor["Selected"].value = grupo
                externa.components.append(seletor)
            else:
                externa.components.append(grupo)
        externa.components.append(clip(3000))
    return path


def _ler(tmp_path: Path, **kw):
    tmp_path.mkdir(parents=True, exist_ok=True)
    wav = tmp_path / "fonte.wav"
    wav.write_bytes(wav_bytes(TAXA * 60, sample_rate=TAXA))
    aaf = _montar(tmp_path / "grupo.aaf", wav, **kw)
    report = Report(strict=False)
    session = read_aaf(aaf, report)
    clips = sorted(session.tracks[0].clips, key=lambda c: c.timeline_start_samples)
    return clips, report, session.start_timecode_samples


def _relativas(clips, inicio):
    return [(c.timeline_start_samples - inicio, c.timeline_end_samples - inicio) for c in clips]


def test_grupo_de_ganho_sobre_sequence_le_cada_clip_com_o_ganho(tmp_path):
    clips, report, inicio = _ler(tmp_path)
    assert _relativas(clips, inicio) == [(1000, 3000), (3500, 4500), (4500, 7500)]
    assert [round(c.gain_linear, 4) for c in clips] == [0.5, 0.5, 1.0]
    assert not [i for i in report.issues if i.severity == "error"]


def test_grupo_da_as_mesmas_posicoes_que_a_sequence_aninhada_sem_efeito(tmp_path):
    com, _r1, i1 = _ler(tmp_path / "a")
    sem, _r2, i2 = _ler(tmp_path / "b", sem_grupo=True)
    assert _relativas(com, i1) == _relativas(sem, i2)


def test_selector_com_grupo_selecionado_le_os_clips(tmp_path):
    clips, report, inicio = _ler(tmp_path, no_selector=True)
    assert _relativas(clips, inicio) == [(1000, 3000), (3500, 4500), (4500, 7500)]
    assert not [i for i in report.issues if i.severity == "error"]


def _cadeia_aaf(path: Path, wav: Path, cadeia: list, rampa_antes: bool = False) -> Path:
    """Track com grupo(s) sobre Sequence[clip 1000, clip 1000].

    ``cadeia`` vai de fora para dentro: ('ganho', (n, d)), ('rampa', None) ou
    ('pan', None). Com ``rampa_antes`` um clip de 1000 com ganho variavel vem antes.
    """
    from aaf2.rational import AAFRational

    with aaf2.open(str(path), "w") as f:
        comp = f.create.CompositionMob("cadeia")
        f.content.mobs.append(comp)
        meta = {
            "format": {"filename": str(wav), "format_name": "wav"},
            "streams": [
                {
                    "codec_type": "audio",
                    "codec_name": "pcm_s16le",
                    "sample_rate": TAXA,
                    "duration_ts": TAXA * 60,
                    "channels": 1,
                }
            ],
        }
        master, _s, _t = f.content.link_external_wav(meta)
        slot = comp.create_timeline_slot(edit_rate=TAXA)
        slot.name = "A1"
        externa = f.create.Sequence(media_kind="sound")
        slot.segment = externa
        op_ganho, param_ganho = _gain_op_def(f)
        interp = f.create.InterpolationDef(
            AUID("5b7199c0-6744-11d2-8326-00609719b0fb"), "Linear", ""
        )
        f.dictionary["InterpolationDefinitions"].append(interp)
        op_pan = f.create.OperationDef(
            AUID("9d2ea893-0968-11d3-8a38-0050040ef7d2"), "Mono Audio Pan", ""
        )
        op_pan["IsTimeWarp"].value = False
        op_pan["NumberInputs"].value = 1
        op_pan["DataDefinition"].value = f.dictionary.lookup_datadef("Sound")
        f.dictionary["OperationDefinitions"].append(op_pan)

        def clip(n):
            c = f.create.SourceClip(media_kind="sound", start=0)
            c.length = n
            c.mob = master
            c.slot_id = 1
            return c

        def rampa():
            vv = f.create.VaryingValue(param_ganho, interp)
            for t, v in (((0, 1), (1, 1)), ((1, 1), (1, 4))):
                cp = f.create.ControlPoint()
                cp["Time"].value = AAFRational(*t)
                cp["Value"].value = AAFRational(*v)
                vv["PointList"].append(cp)
            return vv

        if rampa_antes:
            g = f.create.OperationGroup(op_ganho)
            g.media_kind = "sound"
            g.length = 1000
            g.segments.append(clip(1000))
            g.parameters.append(rampa())
            externa.components.append(g)
        atual = f.create.Sequence(media_kind="sound")
        atual.components.append(clip(1000))
        atual.components.append(clip(1000))
        for tipo, valor in reversed(cadeia):
            if tipo == "seq":
                envolta = f.create.Sequence(media_kind="sound")
                envolta.components.append(atual)
                atual = envolta
                continue
            g = f.create.OperationGroup(op_pan if tipo == "pan" else op_ganho)
            g.media_kind = "sound"
            g.length = 2000
            g.segments.append(atual)
            if tipo == "ganho":
                g.parameters.append(f.create.ConstantValue(param_ganho, AAFRational(*valor)))
            elif tipo == "rampa":
                g.parameters.append(rampa())
            atual = g
        externa.components.append(atual)
    return path


def _ler_cadeia(tmp_path: Path, cadeia, rampa_antes=False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    wav = tmp_path / "fonte.wav"
    wav.write_bytes(wav_bytes(TAXA * 60, sample_rate=TAXA))
    aaf = _cadeia_aaf(tmp_path / "cadeia.aaf", wav, cadeia, rampa_antes)
    report = Report(strict=False)
    session = read_aaf(aaf, report)
    return session, report


def _codigos(report, severidade="error"):
    return {i.code for i in report.issues if i.severity == severidade}


def test_dois_ganhos_encadeados_multiplicam_de_verdade(tmp_path):
    session, report = _ler_cadeia(tmp_path, [("ganho", (1, 2)), ("ganho", (1, 2))])
    assert [round(c.gain_linear, 4) for c in session.tracks[0].clips] == [0.25, 0.25]
    assert not _codigos(report)


def test_ganho_variavel_dentro_de_constante_vira_erro(tmp_path):
    session, report = _ler_cadeia(tmp_path, [("ganho", (1, 2)), ("rampa", None)])
    assert "AAF_UNKNOWN_COMPONENT" in _codigos(report)


def test_pan_sobre_sequence_vira_erro(tmp_path):
    session, report = _ler_cadeia(tmp_path, [("pan", None)])
    assert "AAF_UNKNOWN_COMPONENT" in _codigos(report)


def test_grupo_com_ganho_em_track_com_envelope_vira_erro(tmp_path):
    session, report = _ler_cadeia(tmp_path, [("ganho", (1, 2))], rampa_antes=True)
    assert "AAF_GROUP_GAIN_WITH_TRACK_AUTOMATION" in _codigos(report)


def test_grupo_dentro_de_grupo_com_envelope_nao_perde_o_ganho_de_dentro(tmp_path):
    """Ganho 1 por fora, Sequence, ganho 1/2 por dentro, numa track com envelope."""
    session, report = _ler_cadeia(
        tmp_path, [("ganho", (1, 1)), ("seq", None), ("ganho", (1, 2))], rampa_antes=True
    )
    assert "AAF_GROUP_GAIN_WITH_TRACK_AUTOMATION" in _codigos(report)
