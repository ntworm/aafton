"""Oraculo independente: sinais conhecidos e caminho completo da CLI/pacote."""

from __future__ import annotations

import ast
import json
import math
import random
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from aafton.audio_audit import AuditError, audit_audio, read_wav, to_markdown

REPO = Path(__file__).resolve().parents[2]


def wav(path, channels, *, bits=32, tag=3, extensible=False, time_reference=0):
    """Encodeur de test, sans lecteur/writer du produit."""
    count = len(channels)
    align = count * bits // 8
    fmt = struct.pack(
        "<HHIIHH", 0xFFFE if extensible else tag, count, 48000, 48000 * align, align, bits
    )
    if extensible:
        fmt += (
            struct.pack("<HHI", 22, bits, 0)
            + struct.pack("<I", tag)
            + bytes.fromhex("00001000800000aa00389b71")
        )
    payload = bytearray()
    for frame in zip(*channels):
        for sample in frame:
            if tag == 3:
                payload.extend(struct.pack("<f" if bits == 32 else "<d", sample))
            else:
                value = round(sample * (1 << (bits - 1)))
                payload.extend(value.to_bytes(bits // 8, "little", signed=True))
    body = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    if time_reference is not None:
        bext = bytearray(602)
        struct.pack_into("<Q", bext, 338, time_reference)
        body += b"bext" + struct.pack("<I", len(bext)) + bext
    body += b"data" + struct.pack("<I", len(payload)) + payload
    if len(payload) % 2:
        body += b"\0"
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body) + 4) + b"WAVE" + body)
    return path


@pytest.fixture
def signal():
    rng = random.Random(31013)
    return [rng.uniform(-0.4, 0.4) for _ in range(2048)]


@pytest.fixture
def case(tmp_path, signal):
    reference = tmp_path / "reference"
    candidate = tmp_path / "candidate"
    reference.mkdir()
    candidate.mkdir()
    wav(reference / "Dialogo.wav", [signal])
    wav(candidate / "dialogo.wav", [signal])
    sidecar = tmp_path / "set.aafton.json"
    sidecar.write_text(
        json.dumps(
            {
                "sample_rate": 48000,
                "bpm": 60,
                "live_origin_samples": 0,
                "fades": "keep",
                "timecode_rate": {"num": 25, "den": 1, "drop_frame": False},
            }
        ),
        encoding="utf-8",
    )
    return reference, candidate, sidecar


def audit(case, **kwargs):
    return audit_audio(*case, max_lag=8, **kwargs)


def test_identicos_passam_com_residuo_zero(case):
    result = audit(case)
    assert result["ok"] and result["coverage"]["complete"]
    track = result["tracks"][0]
    assert track["verdict"] == "ok"
    assert track["channels"][0]["residual_peak_dbfs"] == -math.inf
    assert track["channels"][0]["lag_samples"] == 0


@pytest.mark.parametrize("lag", [-1, 1, 7])
def test_atraso_reprova_sem_realinhamento(case, signal, lag):
    shifted = ([0.0] * lag + signal[:-lag]) if lag > 0 else (signal[-lag:] + [0.0] * -lag)
    wav(case[1] / "dialogo.wav", [shifted])
    result = audit(case)
    assert not result["ok"]
    assert result["tracks"][0]["channels"][0]["lag_samples"] == lag
    assert result["tracks"][0]["channels"][0]["residual_peak_dbfs"] > -90


def test_ganho_meio_db_reprova_e_mede(case, signal):
    wav(case[1] / "dialogo.wav", [[x * 10 ** (0.5 / 20) for x in signal]])
    result = audit(case)
    assert not result["ok"]
    assert result["tracks"][0]["channels"][0]["gain_db"] == pytest.approx(0.5, abs=0.01)


def test_lr_trocados_reprovam(case, signal):
    right = signal[::-1]
    wav(case[0] / "Dialogo.wav", [signal, right])
    wav(case[1] / "dialogo.wav", [right, signal])
    assert not audit(case)["ok"]


def test_mono_para_estereo_compara_ambos_e_diferenca_lr(case, signal):
    wav(case[1] / "dialogo.wav", [signal, signal])
    track = audit(case)["tracks"][0]
    assert track["verdict"] == "ok" and len(track["channels"]) == 2
    assert track["channel_policy"] == "mono->stereo: each channel against mono"
    assert track["lr_peak_dbfs"] == -math.inf
    wav(case[1] / "dialogo.wav", [signal, [x * 0.9 for x in signal]])
    assert not audit(case)["ok"]


@pytest.mark.parametrize("kind", ["missing", "extra", "ambiguous"])
def test_cobertura_incompleta_ou_ambigua_reprova(case, signal, kind):
    if kind == "missing":
        (case[1] / "dialogo.wav").unlink()
    elif kind == "extra":
        wav(case[1] / "extra.wav", [signal])
    else:
        wav(case[1] / "dia-logo.wav", [signal])
    result = audit(case)
    assert not result["ok"] and not result["coverage"]["complete"]
    assert result["errors"]


def test_map_explicito_resolve_nome_ambiguo_sem_ocultar_extra(case, signal):
    other = case[1] / "dia-logo.wav"
    (case[1] / "dialogo.wav").rename(other)
    mapping = {"pairs": [{"track": "fala", "reference": "Dialogo.wav", "candidate": other.name}]}
    assert audit(case, mapping=mapping)["ok"]
    wav(case[1] / "dialogo.wav", [signal])
    assert not audit(case, mapping=mapping)["ok"]


def test_map_pode_compor_par_split_com_cobertura_total(case, signal):
    (case[0] / "Dialogo.wav").unlink()
    wav(case[0] / "voz_L.wav", [signal])
    wav(case[0] / "voz_R.wav", [signal[::-1]])
    wav(case[1] / "dialogo.wav", [signal, signal[::-1]])
    mapping = {
        "pairs": [
            {"track": "voz", "reference": ["voz_L.wav", "voz_R.wav"], "candidate": "dialogo.wav"}
        ]
    }
    assert audit(case, mapping=mapping)["ok"]


def test_nomes_5_1_com_ponto_mantem_canais_distintos(case, signal):
    (case[0] / "Dialogo.wav").unlink()
    (case[1] / "dialogo.wav").unlink()
    names = ["5.1 [L]", "5.1 [R]", "5.1 [C]", "5.1 [LFE]", "5.1 [Ls]", "5.1 [Rs]"]
    for name in names:
        wav(case[0] / f"{name}.wav", [signal])
        wav(case[1] / f"{name}.wav", [signal])
    mapping = {
        "pairs": [{"track": n, "reference": f"{n}.wav", "candidate": f"{n}.wav"} for n in names]
    }
    assert audit(case, mapping=mapping)["ok"]
    masks = {"tracks": {n: [[0, 4]] for n in names}}
    result = audit(case, mapping=mapping, mask=masks, mode="absorb")
    assert result["coverage"]["complete"] and len(result["tracks"]) == 6


@pytest.mark.parametrize(
    "bits,tag,extensible",
    [
        (16, 1, False),
        (24, 1, False),
        (32, 1, False),
        (32, 3, False),
        (64, 3, False),
        (24, 1, True),
        (32, 3, True),
    ],
)
def test_leitor_formatos_e_quantizacao(tmp_path, signal, bits, tag, extensible):
    audio = read_wav(
        wav(tmp_path / "sinal.wav", [signal], bits=bits, tag=tag, extensible=extensible)
    )
    tolerance = 2 ** (-(bits - 1)) if tag == 1 else (1e-7 if bits == 32 else 1e-15)
    assert list(audio.channels[0]) == pytest.approx(signal, abs=tolerance)


def test_pcm24_e_float32_concordam_dentro_da_quantizacao(tmp_path, signal):
    a = read_wav(wav(tmp_path / "pcm.wav", [signal], bits=24, tag=1))
    b = read_wav(wav(tmp_path / "float.wav", [signal]))
    assert max(abs(x - y) for x, y in zip(a.channels[0], b.channels[0])) <= 2**-23


def test_bwf_time_reference_alinha_sem_correlacao(case, signal):
    wav(case[0] / "Dialogo.wav", [signal], time_reference=48000)
    wav(case[1] / "dialogo.wav", [[0.0] * 48000 + signal])
    result = audit(case)
    assert result["ok"]
    assert result["tracks"][0]["common_start_samples"] == 48000


def test_sem_bwf_exige_reference_start_e_aceita_rotulo(case, signal):
    wav(case[0] / "Dialogo.wav", [signal], time_reference=None)
    with pytest.raises(AuditError, match="TimeReference.*reference-start"):
        audit(case)
    assert audit(case, reference_start="00:00:00:00")["ok"]


def test_origem_nao_zero_do_sidecar(case, signal):
    origin = 3600 * 48000
    data = json.loads(case[2].read_text())
    data["live_origin_samples"] = origin
    case[2].write_text(json.dumps(data))
    wav(case[0] / "Dialogo.wav", [signal], time_reference=origin)
    assert audit(case)["ok"]


def test_audio_fora_da_janela_comum_nao_desaparece(case, signal):
    wav(case[1] / "dialogo.wav", [signal[:-1]])
    result = audit(case)
    assert not result["ok"]
    assert "outside the common window" in " ".join(result["tracks"][0]["reasons"])


def test_mask_absorb_relata_sem_aceitar_e_keep_recusa_mascara(case, signal):
    data = json.loads(case[2].read_text())
    data["fades"] = "absorb"
    case[2].write_text(json.dumps(data))
    wav(case[1] / "dialogo.wav", [[0.0] * 20 + signal[20:]])
    mask = {"tracks": {"dialogo": [[0, 20]]}}
    result = audit(case, mask=mask)
    assert result["report_only"] and not result["ok"]
    assert result["tracks"][0]["channels"][0]["residual_peak_dbfs"] == -math.inf
    with pytest.raises(AuditError, match="keep does not allow a mask"):
        audit(case, mask=mask, mode="keep")


def test_mascara_total_ou_track_desconhecida_falha(case):
    with pytest.raises(AuditError):
        audit(case, mask={"tracks": {"inexistente": [[0, 2]]}}, mode="absorb")
    with pytest.raises(AuditError, match="without samples"):
        audit(case, mask={"tracks": {"dialogo": [[0, 9999]]}}, mode="absorb")


@pytest.mark.parametrize("kind", ["rf64", "truncated", "nan", "guid"])
def test_leitor_falha_fechado(tmp_path, signal, kind):
    path = wav(tmp_path / "ruim.wav", [signal], extensible=kind == "guid")
    content = path.read_bytes()
    if kind == "rf64":
        content = b"RF64" + content[4:]
    elif kind == "truncated":
        content = content[:-5]
    elif kind == "nan":
        content = content[:-4] + struct.pack("<f", float("nan"))
    else:
        content = content[:48] + b"\xff" + content[49:]
    path.write_bytes(content)
    with pytest.raises(AuditError):
        read_wav(path)


def test_sidecar_nao_inventa_taxa_ou_origem(case):
    for data in (
        {"sample_rate": 48000, "bpm": 60},
        {"sample_rate": 48000, "bpm": 120, "live_origin_samples": 0},
    ):
        case[2].write_text(json.dumps(data))
        with pytest.raises(AuditError):
            audit(case)


def test_oraculo_nao_importa_leitores_da_conversao():
    forbidden = {
        "aaf_read",
        "als_read",
        "media_extract",
        "stereo",
        "verify",
        "sidecar",
        "ir",
        "timecode",
    }
    source = (REPO / "src/aafton/audio_audit.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert not forbidden.intersection((node.module or "").split("."))
        elif isinstance(node, ast.Import):
            assert all(not forbidden.intersection(a.name.split(".")) for a in node.names)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "__import__"


def test_cli_relatorios_json_markdown_e_exit_codes(case, tmp_path, signal):
    command = [
        sys.executable,
        "-m",
        "aafton.cli",
        "audit-audio",
        "--reference",
        str(case[0]),
        "--candidate",
        str(case[1]),
        "--sidecar",
        str(case[2]),
        "--max-lag",
        "8",
        "--report",
        str(tmp_path / "laudo.md"),
        "--json",
        str(tmp_path / "laudo.json"),
    ]
    res = subprocess.run(command, cwd=REPO, capture_output=True, text=True)
    assert res.returncode == 0, res.stdout + res.stderr
    data = json.loads((tmp_path / "laudo.json").read_text(encoding="utf-8"))
    assert data["ok"] and data["tracks"][0]["channels"][0]["residual_peak_dbfs"] is None
    assert "dialogo" in (tmp_path / "laudo.md").read_text(encoding="utf-8")
    wav(case[1] / "dialogo.wav", [[x * 1.1 for x in signal]])
    assert subprocess.run(command, cwd=REPO, capture_output=True).returncode == 1
    assert "RMS" in to_markdown(audit(case))


def test_calibracao_usa_factory_e_especificacao_e_sanidade(tmp_path):
    from scripts.make_calibration_session import make_calibration_session

    generated = make_calibration_session(tmp_path / "calibracao")
    spec = json.loads(Path(generated["spec"]).read_text(encoding="utf-8"))
    assert {t["name"] for t in spec["tracks"]} == {
        "CAL_MONO",
        "CAL_OFFSET",
        "CAL_GAIN",
        "CAL_INTERLEAVED",
        "CAL_SPLIT",
        "CAL_FADES",
        "CAL_TWO_CLIPS",
    }
    sidecar = tmp_path / "anchor.json"
    sidecar.write_text(
        json.dumps(
            {"sample_rate": 48000, "bpm": 60, "fades": "keep", "live_origin_samples": 172800000}
        )
    )
    result = audit_audio(generated["expected"], generated["expected"], sidecar, max_lag=8)
    assert result["ok"] and len(result["tracks"]) == 7
    import aaf2

    with aaf2.open(generated["aaf"], "r") as f:
        assert len(list(f.content.compositionmobs())) == 1
        assert len(list(f.content.compositionmobs())[0].slots) == 9  # split ocupa dois slots + TC
    source = (REPO / "scripts/make_calibration_session.py").read_text(encoding="utf-8")
    assert "tests.aaf_factory" in source
    assert "aaf_write" not in source and "SessionIR" not in source


def test_calibracao_expected_exercita_offset_gain_fades_e_split(tmp_path):
    from scripts.make_calibration_session import make_calibration_session

    generated = make_calibration_session(tmp_path / "cal")
    expected = Path(generated["expected"])
    source = read_wav(Path(generated["samples"]) / "mono.wav").channels[0]
    mono = read_wav(expected / "CAL_MONO.wav").channels[0]
    assert max(abs(x) for x in mono[:48007]) == 0
    assert mono[48007 + 17] == source[17] == 0.75
    offset = read_wav(expected / "CAL_OFFSET.wav").channels[0]
    assert offset[48000 + 7] == pytest.approx(source[624], abs=1e-8)
    gain = read_wav(expected / "CAL_GAIN.wav").channels[0]
    assert gain[48000 + 17] / source[17] == pytest.approx(501187 / 1000000, abs=1e-8)
    fades = read_wav(expected / "CAL_FADES.wav").channels[0]
    assert fades[48000] == fades[95999] == 0
    assert fades[48000 + 2400] == pytest.approx(source[2400] * 0.5, abs=1e-8)
    a = read_wav(expected / "CAL_INTERLEAVED.wav")
    b = read_wav(expected / "CAL_SPLIT.wav")
    assert list(a.channels[0]) == list(b.channels[0])
    assert list(a.channels[1]) == list(b.channels[1])
    assert a.channels[0][48017] != a.channels[1][48017]
    import aaf2

    with aaf2.open(generated["aaf"], "r") as f:
        slots = {s.name: s for s in list(f.content.compositionmobs())[0].slots}
        assert int(slots["CAL_OFFSET"].segment.components[1].start) == 617
        combiner = slots["CAL_INTERLEAVED"].segment.components[1]
        assert [c.slot_id for c in combiner.segments] == [1, 1]
        assert len({str(c.mob.mob_id) for c in combiner.segments}) == 2


def test_preparo_ponta_a_ponta_calibracao_sem_corpus_de_silencio(tmp_path, monkeypatch):
    output = tmp_path / "pacote"
    monkeypatch.setenv("AAFTON_CORPUS_DIR", str(tmp_path / "corpus_inexistente"))
    res = subprocess.run(
        [sys.executable, "scripts/prepare_audio_audit.py", "--output-dir", str(output)],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert (output / "calibracao/calibracao.als").is_file()
    anchor = json.loads((output / "calibracao/calibracao.aafton.json").read_text())
    assert anchor["fades"] == "keep"
    import gzip
    import xml.etree.ElementTree as ET

    tree = ET.fromstring(gzip.decompress((output / "calibracao/calibracao.als").read_bytes()))
    tracks = {
        t.find("Name/EffectiveName").get("Value"): t for t in tree.findall(".//Tracks/AudioTrack")
    }
    assert len(tracks["CAL_FADES"].findall(".//AudioClip")) == 3
    assert len(tracks["CAL_INTERLEAVED"].findall(".//AudioClip")) == 1
    assert not (output / "real").exists()
    assert (output / "calibracao/estereo-produto.aaf").is_file()
    instructions = (output / "INSTRUCOES.md").read_text(encoding="utf-8")
    assert "All Individual Tracks" in instructions and "re-export" in instructions
    assert "TESTE_NOVO" in instructions and "25 fps" in instructions
    assert "{{" not in instructions
    assert "01:00:00:00" in instructions and "Bus 1 (Mono)" in instructions
    for folder in (
        "renders/live/calibracao",
        "renders/protools/calibracao",
        "renders/protools/calibracao-brutos",
        "renders/protools/estereo-produto",
    ):
        assert (output / folder).is_dir() and not list((output / folder).iterdir())
    (output / "renders/live/calibracao/preservar.wav").write_bytes(b"render humano")
    rerun = subprocess.run(
        [sys.executable, "scripts/prepare_audio_audit.py", "--output-dir", str(output)],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert rerun.returncode == 1 and "ja contem renders" in rerun.stderr
    assert (output / "renders/live/calibracao/preservar.wav").read_bytes() == b"render humano"


# --- P03b: achados A2, A3 e A5 do revisor e mascara de release (D12, D14) ---


def _sidecar_com_clips(case, ranges, track="dialogo"):
    data = json.loads(case[2].read_text())
    data["clip_ranges"] = [{"track": track, "ranges": ranges}]
    case[2].write_text(json.dumps(data))


def test_silencio_contra_silencio_reprova_keep_com_razao(case):
    wav(case[0] / "Dialogo.wav", [[0.0] * 2048])
    wav(case[1] / "dialogo.wav", [[0.0] * 2048])
    result = audit(case)
    assert not result["ok"]
    reasons = " ".join(result["tracks"][0]["reasons"])
    assert (
        "reference without active audio" in reasons and "candidate without active audio" in reasons
    )
    command = [
        sys.executable,
        "-m",
        "aafton.cli",
        "audit-audio",
        "--reference",
        str(case[0]),
        "--candidate",
        str(case[1]),
        "--sidecar",
        str(case[2]),
        "--max-lag",
        "8",
    ]
    res = subprocess.run(command, cwd=REPO, capture_output=True, text=True, errors="replace")
    assert res.returncode == 1 and "without active audio" in res.stdout


def test_audio_abaixo_do_limite_nao_conta_como_ativo(case, signal):
    quiet = [x * 1e-5 for x in signal]  # pico abaixo de -90 dBFS
    wav(case[0] / "Dialogo.wav", [quiet])
    wav(case[1] / "dialogo.wav", [quiet])
    result = audit(case)
    assert not result["ok"]
    assert "reference without active audio" in " ".join(result["tracks"][0]["reasons"])


def test_candidato_silencioso_reprova_com_razao_explicita(case):
    wav(case[1] / "dialogo.wav", [[0.0] * 2048])
    result = audit(case)
    assert not result["ok"]
    reasons = result["tracks"][0]["reasons"]
    assert any("candidate without active audio" in r for r in reasons)
    assert not any("reference without active audio" in r for r in reasons)


@pytest.mark.parametrize("edge", ["fim", "inicio"])
def test_probe_unico_de_borda_nao_vence_lag_zero(case, edge):
    """Sonda P2/P2b do revisor: atividade so nas 100 amostras da borda."""
    rng = random.Random(7)
    burst = [rng.uniform(-0.4, 0.4) for _ in range(100)]
    reference = [0.0] * 9900 + burst if edge == "fim" else burst + [0.0] * 9900
    residual = 10 ** (-110 / 20)
    candidate = [x + (residual if x else 0.0) for x in reference]
    wav(case[0] / "Dialogo.wav", [reference])
    wav(case[1] / "dialogo.wav", [candidate])
    result = audit_audio(*case, max_lag=200)
    channel = result["tracks"][0]["channels"][0]
    assert channel["lag_samples"] == 0
    assert channel["correlation_probes_used"] == channel["correlation_probes"] == 100
    assert channel["residual_peak_dbfs"] < -100
    assert result["ok"]


def test_lag_de_uma_amostra_sozinho_reprova_keep(case):
    """A5: residuo abaixo de -90 dBFS; so a regra de lag 0 reprova."""
    smooth = [0.001 * math.sin(2 * math.pi * 3 * i / 48000) for i in range(4096)]
    wav(case[0] / "Dialogo.wav", [smooth])
    wav(case[1] / "dialogo.wav", [[0.0] + smooth[:-1]])
    result = audit_audio(*case, max_lag=8)
    channel = result["tracks"][0]["channels"][0]
    assert channel["residual_peak_dbfs"] < -90
    assert channel["lag_samples"] == 1
    assert not result["ok"]
    assert result["tracks"][0]["reasons"] == ["channel 1: lag 1 samples"]


def _com_rabo(signal, end, tail):
    """Imita o declick do Live: a fonte continua numa rampa linear depois do fim."""
    rendered = [x if i < end else 0.0 for i, x in enumerate(signal)]
    for k in range(tail):
        rendered[end + k] = signal[end + k] * (1 - k / tail)
    return rendered


def test_release_mask_cobre_rabo_curto_e_relata_frames(case, signal):
    reference = [x if i < 1500 else 0.0 for i, x in enumerate(signal)]
    wav(case[0] / "Dialogo.wav", [reference])
    wav(case[1] / "dialogo.wav", [_com_rabo(signal, 1500, 46)])
    _sidecar_com_clips(case, [[0, 1500]])
    assert not audit(case)["ok"]
    result = audit(case, release_mask=64)
    assert result["ok"] and result["release_mask"] == 64
    track = result["tracks"][0]
    assert track["release_masked_frames"] == 64 and track["release_regions"] == [[1500, 1564]]
    assert "64 of them by the release mask" in to_markdown(result)


def test_release_mask_nao_esconde_rabo_maior_nem_erro_dentro_do_clip(case, signal):
    reference = [x if i < 1500 else 0.0 for i, x in enumerate(signal)]
    wav(case[0] / "Dialogo.wav", [reference])
    _sidecar_com_clips(case, [[0, 1500]])
    wav(case[1] / "dialogo.wav", [_com_rabo(signal, 1500, 96)])
    assert not audit(case, release_mask=64)["ok"]
    louder = [x * 10 ** (0.5 / 20) for x in reference]
    louder = louder[:1500] + _com_rabo(signal, 1500, 46)[1500:]
    wav(case[1] / "dialogo.wav", [louder])
    result = audit(case, release_mask=64)
    assert not result["ok"]
    assert "residual peak" in " ".join(result["tracks"][0]["reasons"])


def test_release_mask_nunca_entra_em_outro_clip(case, signal):
    reference = [x if i < 1500 else 0.0 for i, x in enumerate(signal)]
    _sidecar_com_clips(case, [[0, 1000], [1000, 1500]])
    wav(case[0] / "Dialogo.wav", [reference])
    damaged = list(reference)
    for i in range(1000, 1040):  # erro logo depois do primeiro fim, dentro do segundo clip
        damaged[i] = 0.0
    wav(case[1] / "dialogo.wav", [damaged])
    result = audit(case, release_mask=64)
    assert result["tracks"][0]["release_regions"] == [[1500, 1564]]
    assert not result["ok"]


def test_release_mask_exige_fins_de_clip_no_sidecar(case):
    with pytest.raises(AuditError, match="clip_ranges"):
        audit(case, release_mask=64)
    _sidecar_com_clips(case, [[0, 1500]], track="outra")
    with pytest.raises(AuditError, match="clip_ranges"):
        audit(case, release_mask=64)


def test_cli_release_mask_relata_frames_no_json(case, tmp_path, signal):
    reference = [x if i < 1500 else 0.0 for i, x in enumerate(signal)]
    wav(case[0] / "Dialogo.wav", [reference])
    wav(case[1] / "dialogo.wav", [_com_rabo(signal, 1500, 46)])
    _sidecar_com_clips(case, [[0, 1500]])
    command = [
        sys.executable,
        "-m",
        "aafton.cli",
        "audit-audio",
        "--reference",
        str(case[0]),
        "--candidate",
        str(case[1]),
        "--sidecar",
        str(case[2]),
        "--max-lag",
        "8",
        "--release-mask",
        "64",
        "--json",
        str(tmp_path / "laudo.json"),
    ]
    res = subprocess.run(command, cwd=REPO, capture_output=True, text=True, errors="replace")
    assert res.returncode == 0, res.stdout + res.stderr
    data = json.loads((tmp_path / "laudo.json").read_text(encoding="utf-8"))
    assert data["release_mask"] == 64
    assert data["tracks"][0]["release_masked_frames"] == 64
