from pathlib import Path

import pytest

from aafton.ir import MediaAsset
from aafton.media_extract import AmbiguousMediaError, resolve_asset
from aafton.report import Report
from tests.aaf_factory import wav_bytes


def _wav(caminho, frames=48000, channels=1):
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_bytes(wav_bytes(frames, channels))
    return caminho


def test_caminho_absoluto_existente_ganha(tmp_path):
    alvo = _wav(tmp_path / "Audio Files" / "cena12_tk3.wav")
    asset = MediaAsset(id="m1", filename="cena12_tk3.wav", declared_location=str(alvo))
    resolve_asset(asset, [tmp_path], Report(strict=True))
    assert asset.status == "resolved"
    assert asset.resolved_path == str(alvo)


def test_caminho_relativo_dentro_das_pastas_de_busca(tmp_path):
    alvo = _wav(tmp_path / "Audio Files" / "cena12_tk3.wav")
    asset = MediaAsset(
        id="m1",
        filename="cena12_tk3.wav",
        declared_location="/Volumes/DRIVE/Projeto/Audio Files/cena12_tk3.wav",
    )
    resolve_asset(asset, [tmp_path], Report(strict=True))
    assert asset.resolved_path == str(alvo)


def test_caminho_posix_nao_fabrica_unc_durante_a_busca_local(tmp_path, monkeypatch):
    alvo = _wav(tmp_path / "Audio Files" / "cena12_tk3.wav")
    existe_original = Path.exists

    def existe_sem_unc(self, *args, **kwargs):
        texto = str(self)
        if "\\\\Volumes" in texto:
            raise AssertionError(f"a busca local fabricou um caminho de rede: {texto}")
        return existe_original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "exists", existe_sem_unc)

    asset = MediaAsset(
        id="m1",
        filename="cena12_tk3.wav",
        declared_location="/Volumes/DRIVE/Projeto/Audio Files/cena12_tk3.wav",
    )
    resolve_asset(asset, [tmp_path], Report(strict=True))
    assert asset.status == "resolved"
    assert asset.resolved_path == str(alvo)


def test_busca_por_nome_quando_ha_candidato_unico(tmp_path):
    alvo = _wav(tmp_path / "Rendered Files" / "cena12_tk3.wav")
    asset = MediaAsset(id="m1", filename="cena12_tk3.wav", declared_location="cena12_tk3.wav")
    resolve_asset(asset, [tmp_path], Report(strict=True))
    assert asset.resolved_path == str(alvo)


def test_dois_candidatos_iguais_falham_em_vez_de_chutar(tmp_path):
    _wav(tmp_path / "Audio Files" / "cena12_tk3.wav")
    _wav(tmp_path / "Bounced Files" / "cena12_tk3.wav")
    asset = MediaAsset(id="m1", filename="cena12_tk3.wav", declared_location="cena12_tk3.wav")
    with pytest.raises(AmbiguousMediaError, match="cena12_tk3.wav"):
        resolve_asset(asset, [tmp_path], Report(strict=True))


def test_dois_candidatos_desempatados_por_duracao(tmp_path):
    _wav(tmp_path / "Audio Files" / "cena12_tk3.wav", frames=48000)
    certo = _wav(tmp_path / "Bounced Files" / "cena12_tk3.wav", frames=96000)
    asset = MediaAsset(
        id="m1",
        filename="cena12_tk3.wav",
        declared_location="cena12_tk3.wav",
        duration_samples=96000,
    )
    resolve_asset(asset, [tmp_path], Report(strict=True))
    assert asset.resolved_path == str(certo)


def test_arquivo_ausente_gera_erro_e_status_missing(tmp_path):
    asset = MediaAsset(id="m1", filename="nao_existe.wav", declared_location="nao_existe.wav")
    report = Report(strict=False)
    resolve_asset(asset, [tmp_path], report)
    assert asset.status == "missing"
    assert "MEDIA_MISSING" in [i.code for i in report.issues]


def test_mxf_e_marcado_como_nao_suportado(tmp_path):
    alvo = tmp_path / "cena.mxf"
    alvo.write_bytes(b"\x06\x0e\x2b\x34")
    asset = MediaAsset(id="m1", filename="cena.mxf", declared_location=str(alvo))
    report = Report(strict=False)
    resolve_asset(asset, [tmp_path], report)
    assert asset.status == "unsupported"
    assert "MEDIA_MXF_UNSUPPORTED" in [i.code for i in report.issues]


def test_metadados_do_wav_sao_lidos(tmp_path):
    alvo = _wav(tmp_path / "cena12_tk3.wav", frames=96000, channels=2)
    asset = MediaAsset(id="m1", filename="cena12_tk3.wav", declared_location=str(alvo))
    resolve_asset(asset, [tmp_path], Report(strict=True))
    assert asset.channels == 2
    assert asset.sample_rate == 48000
    assert asset.duration_samples == 96000
