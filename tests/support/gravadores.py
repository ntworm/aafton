"""Fixtures de WAVs sinteticos por formato de gravador documentado."""

from __future__ import annotations

import struct
from pathlib import Path

from tests.unit.test_som_direto_leitor import HORA, RATE, ixml, wav


def bext(
    description: str = "",
    *,
    time_reference: int = 0,
    data: str = "2025-05-06",
    hora: str = "10:00:00",
    originator: str = "",
) -> bytes:
    """Chunk bext v1 (602 bytes) com Description, OriginationDate e TimeReference."""
    desc = description.encode("ascii", "replace")[:256].ljust(256, b"\x00")
    orig = originator.encode("ascii", "replace")[:32].ljust(32, b"\x00")
    ref = b"\x00" * 32
    odata = data.encode("ascii", "replace")[:10].ljust(10, b"\x00")
    ohora = hora.encode("ascii", "replace")[:8].ljust(8, b"\x00")
    tail = struct.pack("<QH", time_reference, 1) + b"\x00" * 254
    return desc + orig + ref + odata + ohora + tail


def descricao(**pares: str | None) -> str:
    """Formata linhas chave=valor para o Description do bext."""
    linhas = [f"{chave}={valor}" for chave, valor in pares.items() if valor is not None]
    return "\r\n".join(linhas) + ("\r\n" if linhas else "")


def _entradas(canais: int) -> list[tuple[int, str]]:
    return [(i, f"CH{i}") for i in range(1, canais + 1)]


def _ixml_com_extras(
    scene: str,
    take: str,
    family: str,
    entradas: list[tuple[int, str]],
    *,
    circled: bool | None = None,
    tape: str = "250506",
    original_filename: str | None = None,
) -> bytes:
    raw = ixml(scene, take, family, entradas, circled=circled)
    if tape != "T1":
        raw = raw.replace(b"<TAPE>T1</TAPE>", f"<TAPE>{tape}</TAPE>".encode("ascii"))
    if original_filename is not None:
        bloco = f"<HISTORY><ORIGINAL_FILENAME>{original_filename}</ORIGINAL_FILENAME></HISTORY>"
        raw = raw.replace(b"</BWFXML>", bloco.encode("ascii") + b"</BWFXML>")
    return raw


def _zoom_wav(
    destino: Path,
    cena: str,
    take: str,
    entradas: list[tuple[int, str]],
    *,
    circulado: bool = False,
    com_ixml: bool = True,
    com_z_bext: bool = True,
    tape: str = "250506",
    tc: int = 10 * HORA,
    data: str = "2025-05-06",
    frames: int = 4800,
    rate: int = RATE,
    original_filename: str | None = None,
) -> Path:
    desc = (
        descricao(
            zSCENE=cena,
            zTAKE=take,
            zTAPE=tape,
            zCIRCLED="TRUE" if circulado else "FALSE",
        )
        if com_z_bext
        else ""
    )
    extras: list[tuple[bytes, bytes]] = [
        (b"bext", bext(desc, time_reference=tc, data=data, originator="Zoom F6"))
    ]
    if com_ixml:
        meta = _ixml_com_extras(
            cena,
            take,
            f"{cena}-{take}-fam",
            entradas,
            circled=circulado,
            tape=tape,
            original_filename=original_filename,
        )
        extras.append((b"iXML", meta))
    return wav(destino, len(entradas), frames, rate=rate, extras=tuple(extras))


def zoom_poly(
    pasta: Path,
    cena: str = "Scene001",
    take: str = "001",
    *,
    canais: int = 2,
    circulado: bool = False,
    **kw,
) -> Path:
    """Zoom F6 poly WAV na pasta raiz."""
    pref = "@" if circulado else ""
    destino = pasta / f"{pref}{cena}-{take}.wav"
    return _zoom_wav(destino, cena, take, _entradas(canais), circulado=circulado, **kw)


def zoom_tr(
    pasta: Path,
    cena: str = "Scene001",
    take: str = "001",
    tr: int = 1,
    *,
    circulado: bool = False,
    **kw,
) -> Path:
    """Zoom F6 mono _TrN dentro da pasta .TAKE."""
    pref = "@" if circulado else ""
    destino = pasta / f"{cena}_{take}.TAKE" / f"{pref}{cena}-{take}_Tr{tr}.wav"
    return _zoom_wav(destino, cena, take, [(tr, f"TR{tr}")], circulado=circulado, **kw)


def zoom_tr_par(
    pasta: Path,
    cena: str = "Scene001",
    take: str = "001",
    par: tuple[int, int] = (1, 2),
    *,
    circulado: bool = False,
    **kw,
) -> Path:
    """Zoom F6 estereo _TrN_M dentro da pasta .TAKE."""
    pref = "@" if circulado else ""
    destino = pasta / f"{cena}_{take}.TAKE" / f"{pref}{cena}-{take}_Tr{par[0]}_{par[1]}.wav"
    entradas = [(par[0], f"TR{par[0]}"), (par[1], f"TR{par[1]}")]
    return _zoom_wav(destino, cena, take, entradas, circulado=circulado, **kw)


def zoom_tl_r(
    pasta: Path,
    cena: str = "Scene001",
    take: str = "001",
    *,
    circulado: bool = False,
    **kw,
) -> Path:
    """Zoom F6 mix L/R _TL_R dentro da pasta .TAKE."""
    pref = "@" if circulado else ""
    destino = pasta / f"{cena}_{take}.TAKE" / f"{pref}{cena}-{take}_TL_R.wav"
    return _zoom_wav(destino, cena, take, [(1, "L"), (2, "R")], circulado=circulado, **kw)


def zoom_32fp(
    pasta: Path,
    cena: str = "Scene001",
    take: str = "001",
    *,
    canais: int = 2,
    circulado: bool = False,
    pasta_take: bool = False,
    **kw,
) -> Path:
    """Zoom F6 copia float _32FP."""
    pref = "@" if circulado else ""
    raiz = pasta / f"{cena}_{take}.TAKE" if pasta_take else pasta
    destino = raiz / f"{pref}{cena}_{take}_32FP.wav"
    return _zoom_wav(destino, cena, take, _entradas(canais), circulado=circulado, **kw)


def zoom_pedaco(
    pasta: Path,
    cena: str = "Scene001",
    take: str = "001",
    pedaco: int = 2,
    *,
    canais: int = 2,
    circulado: bool = False,
    pasta_take: bool = False,
    **kw,
) -> Path:
    """Zoom F6 arquivo seguinte _0002 de gravacao longa."""
    pref = "@" if circulado else ""
    raiz = pasta / f"{cena}_{take}.TAKE" if pasta_take else pasta
    destino = raiz / f"{pref}{cena}_{take}_{pedaco:04d}.wav"
    return _zoom_wav(destino, cena, take, _entradas(canais), circulado=circulado, **kw)


def sound_devices(
    pasta: Path,
    cena: str = "S01",
    take: str = "01",
    *,
    canais: int = 2,
    designador: str = "T",
    projeto: str = "",
    circulado: bool = False,
    duplicata: bool = False,
    letra: str = "",
    com_ixml: bool = True,
    bext_s: bool = False,
    tape: str = "250506",
    tc: int = 10 * HORA,
    data: str = "2025-05-06",
    frames: int = 4800,
    rate: int = RATE,
    original_filename: str | None = None,
) -> Path:
    """Sound Devices Serie 8 com iXML e bext opcionais."""
    pref = ("@" if circulado else "") + ("+" if duplicata else "")
    if projeto:
        pref += f"{projeto}="
    destino = pasta / f"{pref}{cena}{designador}{take}{letra}.wav"
    desc = descricao(sSCENE=cena, sTAKE=take, sTAPE=tape) if bext_s else ""
    extras: list[tuple[bytes, bytes]] = [
        (b"bext", bext(desc, time_reference=tc, data=data, originator="SoundDev: 888"))
    ]
    if com_ixml:
        meta = _ixml_com_extras(
            cena,
            take,
            f"{cena}{designador}{take}{letra}-fam",
            _entradas(canais),
            circled=circulado,
            tape=tape,
            original_filename=original_filename,
        )
        extras.append((b"iXML", meta))
    return wav(destino, canais, frames, rate=rate, extras=tuple(extras))


def sound_devices_circulado(pasta: Path, cena: str = "S01", take: str = "01", **kw) -> Path:
    """Sound Devices take circulado (@S01T01.wav)."""
    return sound_devices(pasta, cena, take, circulado=True, **kw)


def sound_devices_duplicata_mais(pasta: Path, cena: str = "S01", take: str = "01", **kw) -> Path:
    """Sound Devices take duplicado com prefixo + (+S01T01.wav)."""
    return sound_devices(pasta, cena, take, duplicata=True, **kw)


def sound_devices_duplicata_letra(
    pasta: Path,
    cena: str = "S01",
    take: str = "01",
    letra: str = "A",
    **kw,
) -> Path:
    """Sound Devices take duplicado com sufixo de letra (S01T01A.wav)."""
    return sound_devices(pasta, cena, take, letra=letra, **kw)


def sound_devices_pedacos(
    pasta: Path,
    cena: str = "S02",
    take: str = "01",
    letras: tuple[str, ...] = ("A", "B"),
    *,
    frames: int = 4800,
    tc: int = 10 * HORA,
    **kw,
) -> list[Path]:
    """Sound Devices take dividido em pedacos A, B... com TimeReference seguido."""
    return [
        sound_devices(
            pasta,
            cena,
            take,
            letra=letra,
            frames=frames,
            tc=tc + idx * frames,
            **kw,
        )
        for idx, letra in enumerate(letras)
    ]


def _tascam_wav(
    destino: Path,
    canais: int,
    *,
    tc: int = 10 * HORA,
    data: str = "2025-05-06",
    frames: int = 4800,
    rate: int = RATE,
) -> Path:
    extras = ((b"bext", bext("", time_reference=tc, data=data)),)
    return wav(destino, canais, frames, rate=rate, extras=extras)


def _x8_nome(
    base: str,
    num: str,
    sufixo: str,
    *,
    duplo_float: bool = False,
    div: str | None = None,
) -> str:
    f_flag = "F" if duplo_float else ""
    div_part = f"_{div}" if div else ""
    return f"{base}_{num}{f_flag}{div_part}_{sufixo}.WAV"


def tascam_x8_faixa(
    pasta: Path,
    base: str = "TASCAM",
    num: str = "0001",
    canal: int = 1,
    *,
    duplo_float: bool = False,
    div: str | None = None,
    **kw,
) -> Path:
    """Tascam X8 faixa mono (_1), sem iXML."""
    nome = _x8_nome(base, num, str(canal), duplo_float=duplo_float, div=div)
    return _tascam_wav(pasta / nome, 1, **kw)


def tascam_x8_par(
    pasta: Path,
    base: str = "TASCAM",
    num: str = "0001",
    par: tuple[int, int] = (1, 2),
    *,
    duplo_float: bool = False,
    div: str | None = None,
    **kw,
) -> Path:
    """Tascam X8 par estereo (_1-2), sem iXML."""
    nome = _x8_nome(base, num, f"{par[0]}-{par[1]}", duplo_float=duplo_float, div=div)
    return _tascam_wav(pasta / nome, 2, **kw)


def tascam_x8_mix(
    pasta: Path,
    base: str = "TASCAM",
    num: str = "0001",
    *,
    duplo_float: bool = False,
    div: str | None = None,
    **kw,
) -> Path:
    """Tascam X8 master (_MIX), sem iXML."""
    nome = _x8_nome(base, num, "MIX", duplo_float=duplo_float, div=div)
    return _tascam_wav(pasta / nome, 2, **kw)


def tascam_dr701d_par(
    pasta: Path,
    base: str = "TASCAM",
    num: str = "0002",
    par: tuple[int, int] = (1, 2),
    **kw,
) -> Path:
    """Tascam DR-701D par estereo (_12), sem iXML."""
    destino = pasta / f"{base}_{num}_{par[0]}{par[1]}.wav"
    return _tascam_wav(destino, 2, **kw)


def tascam_dr701d_poly(
    pasta: Path,
    base: str = "TASCAM",
    num: str = "0002",
    canais: int = 6,
    **kw,
) -> Path:
    """Tascam DR-701D poly (_6CH), sem iXML."""
    destino = pasta / f"{base}_{num}_{canais}CH.WAV"
    return _tascam_wav(destino, canais, **kw)


def tascam_dr40x(
    pasta: Path,
    base: str = "180113",
    num: str = "0003",
    par: str = "12",
    *,
    canais: int = 2,
    **kw,
) -> Path:
    """Tascam DR-40X fonte estereo ou mono (S12, S34), sem iXML."""
    destino = pasta / f"{base}_{num}S{par}.WAV"
    return _tascam_wav(destino, canais, **kw)
