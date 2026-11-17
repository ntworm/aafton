"""Classificacao de clips por sinais de arquivo, metadados BWF e nome.

Implementa as regras deterministicas da secao 'Sinais e classificacao' do
plano de organizacao de sessao (P1). Cada sinal habilitado ('file', 'meta',
'name') emite no maximo uma opiniao; RECORDER (sinal 'meta') concorda com
PRODUCTION e com VO, valendo PRODUCTION quando nao ha opiniao de nome.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from . import bwf_meta
from .bwf_meta import BwfInfo

PRODUCTION = "PRODUCTION"
VO = "VO"
CAMERA = "CAMERA"
MUSIC = "MUSIC"
SFX = "SFX"
AMBIENCE = "AMBIENCE"
PICTURE_REF = "PICTURE_REF"
RECORDER = "RECORDER"

ALL_SIGNALS: frozenset[str] = frozenset({"file", "meta", "name"})
CERTAINTY_LEVELS: frozenset[str] = frozenset({"permissive", "balanced", "strict"})

_CAMERA_EXTS = frozenset({".r3d", ".mov", ".mxf", ".braw"})
_PICTURE_REF_EXTS = frozenset({".mp4"})
_VO_EXTS = frozenset({".m4a"})
_OTHER_MEDIA_EXTS = frozenset(
    {
        ".wav",
        ".aif",
        ".aiff",
        ".mp3",
        ".mpeg",
        ".mpg",
        ".aac",
        ".flac",
        ".ogg",
        ".wma",
        ".bwf",
        ".avi",
        ".mkv",
    }
)
_ALL_MEDIA_EXTS = _CAMERA_EXTS | _PICTURE_REF_EXTS | _VO_EXTS | _OTHER_MEDIA_EXTS

_RECORDER_ORIGINATORS = (
    "sound devices",
    "mixpre",
    "zoom",
    "tascam",
    "zaxcom",
    "aaton",
    "sonosax",
)
_STRICT_REASON_TOKENS = ("file", "metadata", "ixml", "bext")

# Sufixo de canal do par estereo, com _ ou . como o stereo._get_base_name.
_LR_SUFFIX_RE = re.compile(r"[_.][lrLR]$")
_CAMERA_ROLL_RE = re.compile(r"^[a-z]\d{3}_[a-z]\d{3}_")
_RECORDER_CODE_RE = re.compile(r"(?:cena|scene|take|sc\d|tk\d|pl\d)")
_OFF_CODE_RE = re.compile(r"off")
_VO_WORD_RE = re.compile(r"(?<![a-z])(?:vo|narr)(?![a-z])")
_MUSIC_RE = re.compile(
    r"(?<![a-z])(?:bwv|op\.(?![a-z])|no\.?[\s_.-]*\d+"
    r"|musica|music|song|trilha|score|theme)(?![a-z])"
)
_SFX_RE = re.compile(r"(?<![a-z])(?:som[\s_.-]+de|sfx|fx|efeito|foley|whoosh|impact)(?![a-z])")
_AMBIENCE_RE = re.compile(
    r"(?<![a-z])(?:amb|ambiente|ambience|room[\s_.-]+tone|roomtone"
    r"|bg|passaros|birds|chuva|rain|vento|wind)(?![a-z])"
)


@dataclass(frozen=True)
class Opinion:
    """Opiniao emitida por um sinal ('file', 'meta' ou 'name')."""

    signal: str
    kind: str


@dataclass(frozen=True)
class Classification:
    """Resultado da classificacao de um clip."""

    kind: str | None
    score: int
    conflict: bool
    reasons: tuple[str, ...] = ()
    opinions: tuple[Opinion, ...] = ()


def fold_text(text: str) -> str:
    """Normaliza em NFD, remove marcas de acento e converte para minusculas."""
    decomposed = unicodedata.normalize("NFD", text)
    stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return stripped.lower()


def strip_channel_and_extensions(name: str) -> tuple[str, str | None]:
    """Remove _L/_R e todas as extensoes de midia do fim; devolve (base, ultima_ext)."""
    current = _LR_SUFFIX_RE.sub("", name.strip())
    last_ext: str | None = None
    while True:
        dot = current.rfind(".")
        if dot <= 0:
            break
        ext = current[dot:].lower()
        if ext not in _ALL_MEDIA_EXTS:
            break
        if last_ext is None:
            last_ext = ext
        current = _LR_SUFFIX_RE.sub("", current[:dot])
    return current, last_ext


def _file_opinion(clip_name: str, media_filename: str | None) -> tuple[Opinion, str] | None:
    """Avalia o sinal 'file' pela ultima extensao real de audio ou video."""
    # O nome do clip vem primeiro: no AAF do Premiere a midia e um UUID conformado
    # (77fba76b-....wav) e so o nome do clip guarda a extensao do arquivo original.
    _, ext = strip_channel_and_extensions(clip_name)
    if ext is None and media_filename:
        _, ext = strip_channel_and_extensions(media_filename)
    if ext in _CAMERA_EXTS:
        return Opinion("file", CAMERA), f"camera file ({ext})"
    if ext in _PICTURE_REF_EXTS:
        return Opinion("file", PICTURE_REF), f"picture ref file ({ext})"
    if ext in _VO_EXTS:
        return Opinion("file", VO), f"voice file ({ext})"
    return None


def _has_ixml_recorder(bwf: BwfInfo) -> bool:
    """Indica se o iXML traz SCENE ou TAKE preenchido."""
    if bwf.ixml is None:
        return False
    scene = (bwf.ixml.scene or "").strip()
    take = (bwf.ixml.take or "").strip()
    return bool(scene or take)


def _has_bext_recorder(bwf: BwfInfo) -> bool:
    """Indica se o originador do bext pertence a um gravador conhecido."""
    if not bwf.bext:
        return False
    originator = str(bwf.bext.get("originator") or "").lower()
    return any(token in originator for token in _RECORDER_ORIGINATORS)


def _meta_opinion(bwf: BwfInfo | None) -> tuple[Opinion, str] | None:
    """Avalia o sinal 'meta' a partir de BwfInfo (iXML ou bext)."""
    if bwf is None:
        return None
    if _has_ixml_recorder(bwf):
        return Opinion("meta", RECORDER), "recorder metadata (iXML)"
    if _has_bext_recorder(bwf):
        return Opinion("meta", RECORDER), "recorder metadata (bext)"
    return None


def _match_name_text(raw_name: str) -> tuple[Opinion, str] | None:
    """Aplica as regras de nome sobre uma string ja sem extensao e sem _L/_R."""
    base, _ = strip_channel_and_extensions(raw_name)
    folded = fold_text(base)
    if not folded:
        return None
    if _CAMERA_ROLL_RE.search(folded):
        return Opinion("name", CAMERA), "camera roll name"
    if _OFF_CODE_RE.search(folded) or _VO_WORD_RE.search(folded):
        return Opinion("name", VO), "voice name (off/vo)"
    if _RECORDER_CODE_RE.search(folded):
        return Opinion("name", PRODUCTION), "recorder name (scene/take)"
    if _MUSIC_RE.search(folded):
        return Opinion("name", MUSIC), "music name"
    if _SFX_RE.search(folded):
        return Opinion("name", SFX), "sfx name"
    if _AMBIENCE_RE.search(folded):
        return Opinion("name", AMBIENCE), "ambience name"
    return None


def _name_opinion(clip_name: str, media_filename: str | None) -> tuple[Opinion, str] | None:
    """Avalia o sinal 'name' sobre o nome da midia e o nome do clip."""
    if media_filename:
        result = _match_name_text(media_filename)
        if result is not None:
            return result
    return _match_name_text(clip_name)


def _resolve_opinions(pairs: list[tuple[Opinion, str]]) -> Classification:
    """Resolve a votacao entre as opinioes, tratando a concordancia de RECORDER."""
    if not pairs:
        return Classification(kind=None, score=0, conflict=False, reasons=(), opinions=())
    opinions = tuple(op for op, _ in pairs)
    reasons = tuple(reason for _, reason in pairs)
    name_kinds = [op.kind for op in opinions if op.signal == "name"]
    recorder_target = VO if VO in name_kinds else PRODUCTION
    effective = [recorder_target if op.kind == RECORDER else op.kind for op in opinions]
    counts: dict[str, int] = {}
    for kind in effective:
        counts[kind] = counts.get(kind, 0) + 1
    max_count = max(counts.values())
    winners = [kind for kind, count in counts.items() if count == max_count]
    if len(winners) != 1:
        return Classification(
            kind=None,
            score=0,
            conflict=len(counts) > 1,
            reasons=reasons,
            opinions=opinions,
        )
    winner = winners[0]
    conflict = any(kind != winner for kind in effective)
    return Classification(
        kind=winner,
        score=max_count,
        conflict=conflict,
        reasons=reasons,
        opinions=opinions,
    )


def classify(
    clip_name: str,
    media_filename: str | None = None,
    bwf: BwfInfo | None = None,
    signals: Iterable[str] = ALL_SIGNALS,
) -> Classification:
    """Classifica um clip segundo os sinais habilitados ('file', 'meta', 'name')."""
    enabled = frozenset(signals)
    pairs: list[tuple[Opinion, str]] = []
    if "file" in enabled:
        file_op = _file_opinion(clip_name, media_filename)
        if file_op is not None:
            pairs.append(file_op)
    if "meta" in enabled:
        meta_op = _meta_opinion(bwf)
        if meta_op is not None:
            pairs.append(meta_op)
    if "name" in enabled:
        name_op = _name_opinion(clip_name, media_filename)
        if name_op is not None:
            pairs.append(name_op)
    return _resolve_opinions(pairs)


def _agrees_with_winner(opinion_kind: str, winner: str) -> bool:
    """Verifica se a opiniao apoia o tipo vencedor."""
    if opinion_kind == winner:
        return True
    return opinion_kind == RECORDER and winner in (PRODUCTION, VO)


def _has_file_or_meta_support(classification: Classification) -> bool:
    """Verifica se ha opiniao 'file' ou 'meta' a favor do vencedor para o modo strict."""
    if classification.kind is None:
        return False
    if classification.opinions:
        return any(
            op.signal in ("file", "meta") and _agrees_with_winner(op.kind, classification.kind)
            for op in classification.opinions
        )
    return any(
        any(tok in reason.lower() for tok in _STRICT_REASON_TOKENS)
        for reason in classification.reasons
    )


def moves(classification: Classification, level: str = "permissive") -> bool:
    """Decide se a classificacao move o clip no nivel ('permissive', 'balanced', 'strict')."""
    if level not in CERTAINTY_LEVELS:
        raise ValueError(f"unknown certainty level: {level!r}")
    if classification.kind is None:
        return False
    if level == "permissive":
        return classification.score >= 1
    if level == "balanced":
        return classification.score >= 2 and not classification.conflict
    return (
        classification.score >= 2
        and not classification.conflict
        and _has_file_or_meta_support(classification)
    )


def read_media_meta(path: str | Path | None) -> BwfInfo | None:
    """Le BwfInfo de um arquivo .wav/.WAV existente e legivel; devolve None nos demais casos."""
    if path is None:
        return None
    try:
        candidate = Path(path)
        if candidate.suffix.lower() != ".wav" or not candidate.is_file():
            return None
        return bwf_meta.read_bwf(candidate)
    except Exception:
        return None
