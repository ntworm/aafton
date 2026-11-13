import copy as _copy
import gzip
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

HEADER = b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x00"
XML_DECLARATION = b'<?xml version="1.0" encoding="UTF-8"?>\n'

GLOBAL_ID_FLOOR = 100
ID_FLOOR = 100_000


class DocumentInvalid(RuntimeError):
    """O .als montado viola uma regra do formato e nao deve ser salvo."""


def load_als(path: str | Path) -> ET.Element:
    """Carrega um arquivo .als comprimido e retorna a raiz XML."""
    try:
        with gzip.open(Path(path), "rb") as handle:
            return ET.parse(handle).getroot()
    except gzip.BadGzipFile:
        raise ValueError(f"The file {path} is not a valid gzip (it does not decompress)")


def save_als(root: ET.Element, path: str | Path) -> None:
    """Escreve o elemento raiz como .als comprimido."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = ET.tostring(root, encoding="utf-8", xml_declaration=False)
    with gzip.open(p, "wb") as handle:
        handle.write(XML_DECLARATION)
        handle.write(body)


def collect_ids(node: ET.Element) -> set[int]:
    """Todos os valores do atributo Id na subarvore."""
    found: set[int] = set()
    for element in node.iter():
        raw = element.get("Id")
        if raw is not None and raw.lstrip("-").isdigit():
            found.add(int(raw))
    return found


def classify_id_tags(root: ET.Element) -> tuple[frozenset[str], frozenset[str]]:
    """Descobre quais tags usam Id como indice posicional, e quais como ponteiro."""
    grupos: dict[tuple[int, str], list[int]] = {}
    tags: set[str] = set()
    for pai in root.iter():
        for filho in pai:
            raw = filho.get("Id")
            if raw is None or not raw.lstrip("-").isdigit():
                continue
            tags.add(filho.tag)
            grupos.setdefault((id(pai), filho.tag), []).append(int(raw))

    posicionais = {
        tag for (_pai, tag), ids in grupos.items() if sorted(ids) == list(range(len(ids)))
    }
    posicionais.update({"FloatEvent", "BoolEvent", "IntEvent"})
    return frozenset(posicionais), frozenset(tags - posicionais)


def unique_ids(root: ET.Element) -> frozenset[int]:
    """Valores de Id que aparecem uma unica vez no documento."""
    contagem: dict[int, int] = {}
    for element in root.iter():
        raw = element.get("Id")
        if raw is not None and raw.lstrip("-").isdigit():
            contagem[int(raw)] = contagem.get(int(raw), 0) + 1
    return frozenset(valor for valor, vezes in contagem.items() if vezes == 1)


@dataclass
class IdAllocator:
    """Emite identificadores novos para objetos clonados."""

    start: int = ID_FLOOR
    _cursor: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        self._cursor = self.start

    @classmethod
    def from_root(cls, root: ET.Element) -> "IdAllocator":
        highest = max(collect_ids(root), default=0)
        node = root.find(".//NextPointeeId")
        declared = int(node.get("Value", 0)) if node is not None else 0
        return cls(start=max(ID_FLOOR, highest + 1, declared))

    def next(self) -> int:
        value = self._cursor
        self._cursor += 1
        return value

    def write_next_pointee_id(self, root: ET.Element) -> None:
        node = root.find(".//NextPointeeId")
        if node is None:
            raise KeyError("the template has no NextPointeeId")
        node.set("Value", str(self._cursor + 1))


def clone(
    prototype: ET.Element,
    allocator: IdAllocator,
    positional_tags: frozenset[str],
    unique: frozenset[int],
) -> ET.Element:
    """Copia profunda de um prototipo, trocando so o Id que precisa mudar."""
    duplicate = _copy.deepcopy(prototype)
    for element in duplicate.iter():
        raw = element.get("Id")
        if raw is None or element.tag in positional_tags:
            continue
        if not raw.lstrip("-").isdigit():
            continue
        if int(raw) in unique:
            element.set("Id", str(allocator.next()))
    return duplicate


def renumber_siblings(container: ET.Element, tag: str) -> None:
    """Poe os filhos de um tipo em indice posicional 0..n-1, na ordem atual."""
    for posicao, filho in enumerate(f for f in container if f.tag == tag):
        filho.set("Id", str(posicao))


def format_float(value: float) -> str:
    """Escreve o double inteiro, sem perder digito e sem notacao exponencial.

    O Live grava os proprios valores com 17 digitos significativos
    (``56.664675168581418``) e le de volta o mesmo double. Seis casas decimais
    deslocavam o tempo: 48007 samples a 60 BPM viravam 1.000146 beat, ou
    48007.008 samples, e o Live tocou o clip uma amostra depois. ``repr`` da a
    menor grafia que volta ao mesmo double; ``Decimal`` so tira o expoente que
    ``repr`` usa para valores pequenos, sem mudar o valor.
    """
    if not math.isfinite(value):
        raise ValueError(f"a non-finite value does not fit in the .als: {value!r}")
    text = repr(float(value))
    if "e" in text or "E" in text:
        text = format(Decimal(text), "f")
    if text.endswith(".0"):
        text = text[:-2]
    return "0" if text == "-0" else text


def format_value(value: float | int | str | bool) -> str:
    """Formata valor primitivo de volta para o XML do Ableton."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return format_float(value)
    return str(value)


def set_value(node: ET.Element, path: str, value: float | int | str | bool) -> None:
    """Escreve um valor primitivo num atributo Value aninhado."""
    target = node.find(path)
    if target is None:
        raise KeyError(f"Path {path!r} not found in the XML")
    target.set("Value", format_value(value))


def get_value(node: ET.Element, path: str) -> str | None:
    """Le o atributo Value de um caminho aninhado."""
    target = node.find(path)
    if target is None:
        return None
    return target.get("Value")


def validate_document(root: ET.Element, posicionais: frozenset[str]) -> None:
    """Confere as invariantes do .als antes de salvar."""
    problemas: list[str] = []

    ids_de_track = [
        int(t.get("Id"))
        for t in root.findall(".//LiveSet/Tracks/*")
        if (t.get("Id") or "").lstrip("-").isdigit()
    ]
    repetidos = {i for i in ids_de_track if ids_de_track.count(i) > 1}
    if repetidos:
        problemas.append(
            f"the tracks have repeated Ids: {sorted(repetidos)}; Live refuses the "
            f"whole document with 'Non-unique list Ids'"
        )

    group_track_ids = {
        int(t.get("Id"))
        for t in root.findall(".//LiveSet/Tracks/GroupTrack")
        if (t.get("Id") or "").lstrip("-").isdigit()
    }

    for t in root.findall(".//LiveSet/Tracks/*"):
        tg_id = get_value(t, "TrackGroupId")
        if tg_id is not None and tg_id.lstrip("-").isdigit():
            tg_id = int(tg_id)
            if tg_id != -1 and tg_id not in group_track_ids:
                problemas.append(
                    f"the track {t.get('Id')} has TrackGroupId={tg_id} but there is no "
                    f"GroupTrack with that Id; Live will crash"
                )

    seen_groups = set()
    current_group = None

    for t in root.findall(".//LiveSet/Tracks/*"):
        if t.tag == "GroupTrack":
            gid = int(t.get("Id"))
            if current_group is not None:
                seen_groups.add(current_group)
            current_group = gid
        else:
            tg_val = get_value(t, "TrackGroupId")
            if tg_val is not None and tg_val.lstrip("-").isdigit():
                tg_id = int(tg_val)
                if tg_id != -1:
                    if tg_id != current_group:
                        if tg_id in seen_groups:
                            problemas.append(
                                f"member Id={t.get('Id')} of group Id={tg_id} appeared after the group block ended; contiguity break"
                            )
                        else:
                            problemas.append(
                                f"member Id={t.get('Id')} of group Id={tg_id} appeared before the matching GroupTrack; contiguity break"
                            )
                else:
                    if current_group is not None:
                        seen_groups.add(current_group)
                        current_group = None
            else:
                if current_group is not None:
                    seen_groups.add(current_group)
                    current_group = None
    contagem: dict[int, int] = {}
    for pai in root.iter():
        sibling_ids: set[tuple[str, int]] = set()
        for element in pai:
            raw = element.get("Id")
            if raw is None or not raw.lstrip("-").isdigit():
                continue
            valor = int(raw)

            if element.tag in posicionais:
                key = (element.tag, valor)
                if key in sibling_ids:
                    problemas.append(
                        f"<{element.tag}> has a duplicated positional Id {valor} in the same parent"
                    )
                sibling_ids.add(key)
                tamanho_lista = sum(1 for f in pai if f.tag == element.tag)
                # O Live grava indice posicional comecando fora do zero em alguns casos
                # observados (ex: FileRef Id="1" dentro de OriginalFileRef, WarpMarker
                # com Ids [2, 3] numa lista de dois).
                # A folga de + 100 existe para tolerar esses casos, ao mesmo tempo em que
                # pega um indice inflado (ex: 100017) que faria o Live tentar alocar um array gigante e crashar.
                if valor >= tamanho_lista + 100:
                    problemas.append(
                        f"<{element.tag}> is a positional list, but has Id {valor} in a list of size {tamanho_lista}; "
                        f"an inflated list index makes Live allocate a huge array and crash"
                    )
                continue

            if valor >= GLOBAL_ID_FLOOR:
                contagem[valor] = contagem.get(valor, 0) + 1

    problemas += [
        f"the Id {valor} appears {vezes} times, duplicated"
        for valor, vezes in sorted(contagem.items())
        if vezes > 1
    ]

    todos = collect_ids(root)

    grupos = {
        int(t.get("Id"))
        for t in root.iter("GroupTrack")
        if (t.get("Id") or "").lstrip("-").isdigit()
    }
    for node in root.iter("TrackGroupId"):
        raw = node.get("Value", "-1")
        if raw.lstrip("-").isdigit() and int(raw) != -1 and int(raw) not in grupos:
            problemas.append(f"TrackGroupId={raw} does not match any GroupTrack of the document")

    for node in root.iter("PointeeId"):
        raw = node.get("Value", "")
        if raw.lstrip("-").isdigit() and int(raw) not in todos:
            problemas.append(f"PointeeId={raw} points to an Id that no longer exists")

    node = root.find(".//NextPointeeId")
    if node is not None and (node.get("Value") or "").isdigit():
        declarado = int(node.get("Value"))
        maior = max(todos, default=0)
        if declarado <= maior:
            problemas.append(
                f"NextPointeeId={declarado} is not greater than the greatest Id of the document, {maior}"
            )

    if problemas:
        raise DocumentInvalid(
            f"the document has {len(problemas)} problem(s):\n  - " + "\n  - ".join(problemas)
        )
