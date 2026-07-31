from __future__ import annotations

import re
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from functools import cache
from pathlib import Path
from typing import Any, TypeAlias, cast, get_type_hints

import opendocs._models as core_models
from opendocs._models import BBox, Block, DocumentType, WarningRecord

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DATACLASS_TYPE = "__office_dataclass__"


def _require_int(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int")
    return value


def _require_string(name: str, value: object) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a str")
    return value


def _require_optional_string(name: str, value: object) -> str | None:
    if value is None:
        return None
    return _require_string(name, value)


def _require_tuple(name: str, value: object) -> tuple[object, ...]:
    if not isinstance(value, tuple):
        raise TypeError(f"{name} must be a tuple")
    return value


def _require_basename(name: str, value: object) -> str:
    artifact_name = _require_string(name, value)
    candidate = Path(artifact_name)
    windows_stem = artifact_name.split(".", 1)[0].rstrip(" ").upper()
    windows_reserved = windows_stem in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "CONIN$",
        "CONOUT$",
    } or (
        len(windows_stem) == 4
        and windows_stem[:3] in {"COM", "LPT"}
        and windows_stem[3] in "123456789¹²³"
    )
    forbidden = '<>:"/\\|?*'
    if (
        not artifact_name
        or artifact_name[-1] in {" ", "."}
        or any(
            ord(character) < 32 or ord(character) == 127 or character in forbidden
            for character in artifact_name
        )
        or candidate.is_absolute()
        or candidate.name != artifact_name
        or artifact_name in {".", ".."}
        or windows_reserved
    ):
        raise ValueError(f"{name} must be a portable non-empty basename")
    return artifact_name


def _require_sha256(name: str, value: object) -> str:
    digest = _require_string(name, value)
    if not _SHA256_RE.fullmatch(digest):
        raise ValueError(f"{name} must be a lowercase 64-character SHA-256 hex digest")
    return digest


def _require_bbox(name: str, value: object) -> BBox:
    if not isinstance(value, BBox):
        raise TypeError(f"{name} must be a BBox")
    return value.require_normalized(name)


def _require_block(name: str, index: int, value: object) -> Block:
    if not isinstance(value, _KNOWN_BLOCK_TYPES):
        raise TypeError(f"{name}[{index}] is not a supported block type")
    return cast(Block, value)


def _require_warning(name: str, index: int, value: object) -> WarningRecord:
    if not isinstance(value, WarningRecord):
        raise TypeError(f"{name}[{index}] must be a WarningRecord")
    return value


def _require_source_index(name: str, value: object) -> int:
    source_index = _require_int(name, value)
    if source_index < 0:
        raise ValueError(f"{name} must be greater than or equal to zero")
    return source_index


def _block_class_names() -> tuple[str, ...]:
    return (
        "TextBlock",
        "MarkdownBlock",
        "PageBreakBlock",
        "TableBlock",
        "InlineText",
        "InlineLink",
        "ParagraphBlock",
        "HeadingBlock",
        "ListItemBlock",
        "HardPageBreakBlock",
        "SpannedTableCell",
        "SpannedTableBlock",
    )


_MODEL_REGISTRY: dict[str, type[Any]] = {}
for _name in (*_block_class_names(), "WarningRecord", "BBox"):
    _class = getattr(core_models, _name, None)
    if isinstance(_class, type) and is_dataclass(_class):
        _MODEL_REGISTRY[_name] = _class

_KNOWN_BLOCK_TYPES = tuple(
    value for name, value in _MODEL_REGISTRY.items() if name not in {"WarningRecord", "BBox"}
)


@dataclass(frozen=True, slots=True)
class NativeSlot:
    source_index: int
    blocks: tuple[Block, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "source_index", _require_source_index("source_index", self.source_index)
        )
        normalized_blocks = _require_tuple("blocks", self.blocks)
        if not normalized_blocks:
            raise ValueError("blocks must contain at least one block")
        for index, block in enumerate(normalized_blocks):
            _require_block("blocks", index, block)
        object.__setattr__(self, "blocks", cast(tuple[Block, ...], normalized_blocks))


@dataclass(frozen=True, slots=True)
class ImageSlot:
    source_index: int
    artifact_name: str
    content_sha256: str
    bbox: BBox
    alt_text: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "source_index", _require_source_index("source_index", self.source_index)
        )
        object.__setattr__(
            self, "artifact_name", _require_basename("artifact_name", self.artifact_name)
        )
        object.__setattr__(
            self,
            "content_sha256",
            _require_sha256("content_sha256", self.content_sha256),
        )
        object.__setattr__(self, "bbox", _require_bbox("bbox", self.bbox))
        object.__setattr__(self, "alt_text", _require_optional_string("alt_text", self.alt_text))


@dataclass(frozen=True, slots=True)
class BreakSlot:
    source_index: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "source_index", _require_source_index("source_index", self.source_index)
        )


OfficeSlot: TypeAlias = NativeSlot | ImageSlot | BreakSlot


@dataclass(frozen=True, slots=True)
class OfficePage:
    page_number: int
    slots: tuple[OfficeSlot, ...]

    def __post_init__(self) -> None:
        normalized_page_number = _require_int("page_number", self.page_number)
        if normalized_page_number <= 0:
            raise ValueError("page_number must be greater than zero")
        normalized_slots = _require_tuple("slots", self.slots)
        seen: set[int] = set()
        for index, slot in enumerate(normalized_slots):
            if not isinstance(slot, NativeSlot | ImageSlot | BreakSlot):
                raise TypeError(f"slots[{index}] must be an Office slot")
            if slot.source_index in seen:
                raise ValueError("Office page source indexes must be unique")
            seen.add(slot.source_index)
        object.__setattr__(self, "page_number", normalized_page_number)
        object.__setattr__(self, "slots", cast(tuple[OfficeSlot, ...], normalized_slots))


@dataclass(frozen=True, slots=True)
class OfficeDocument:
    document_type: DocumentType
    pages: tuple[OfficePage, ...]
    warnings: tuple[WarningRecord, ...] = ()

    def __post_init__(self) -> None:
        if self.document_type not in {DocumentType.DOCX, DocumentType.PPTX}:
            raise ValueError("document_type must be DOCX or PPTX")
        normalized_pages = _require_tuple("pages", self.pages)
        page_numbers: set[int] = set()
        for index, page in enumerate(normalized_pages):
            if not isinstance(page, OfficePage):
                raise TypeError(f"pages[{index}] must be an OfficePage")
            if page.page_number in page_numbers:
                raise ValueError("page numbers must be unique")
            page_numbers.add(page.page_number)
        normalized_warnings = _require_tuple("warnings", self.warnings)
        for index, warning in enumerate(normalized_warnings):
            _require_warning("warnings", index, warning)
        object.__setattr__(self, "document_type", self.document_type)
        object.__setattr__(self, "pages", cast(tuple[OfficePage, ...], normalized_pages))
        object.__setattr__(self, "warnings", cast(tuple[WarningRecord, ...], normalized_warnings))


def _dataclass_to_wire(value: object) -> dict[str, object]:
    class_name = type(value).__name__
    fields_payload: dict[str, object] = {}
    for field in fields(cast(Any, value)):
        fields_payload[field.name] = _value_to_wire(getattr(value, field.name))
    return {_DATACLASS_TYPE: class_name, "fields": fields_payload}


def _value_to_wire(value: object) -> object:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, tuple):
        return tuple(_value_to_wire(item) for item in value)
    if is_dataclass(value) and type(value).__name__ in _MODEL_REGISTRY:
        return _dataclass_to_wire(value)
    raise TypeError(f"Office wire value type is not supported: {type(value).__name__}")


def _decode_dataclass(value: dict[str, object]) -> object:
    if set(value) != {_DATACLASS_TYPE, "fields"}:
        raise ValueError("Office dataclass wire is invalid")
    class_name = value[_DATACLASS_TYPE]
    fields_value = value["fields"]
    if not isinstance(class_name, str) or not isinstance(fields_value, dict):
        raise ValueError("Office dataclass wire is invalid")
    cls = _MODEL_REGISTRY.get(class_name)
    if cls is None:
        raise ValueError("Office dataclass type is invalid")
    field_names = {field.name for field in fields(cast(Any, cls))}
    typed_fields = cast(dict[str, object], fields_value)
    if set(typed_fields) != field_names:
        raise ValueError("Office dataclass wire is invalid")
    field_types = _resolved_field_types(cls)
    kwargs: dict[str, Any] = {
        name: _restore_enum_field(_value_from_wire(item), field_types.get(name))
        for name, item in typed_fields.items()
    }
    return cls(**kwargs)


def _value_from_wire(value: object) -> object:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, tuple):
        return tuple(_value_from_wire(item) for item in value)
    if isinstance(value, dict) and _DATACLASS_TYPE in value:
        return _decode_dataclass(cast(dict[str, object], value))
    raise ValueError("Office wire value is invalid")


@cache
def _resolved_field_types(cls: type[Any]) -> dict[str, Any]:
    return get_type_hints(cls)


def _restore_enum_field(value: object, field_type: object) -> object:
    if not isinstance(field_type, type) or not issubclass(field_type, Enum):
        return value
    if isinstance(value, field_type):
        return value
    try:
        return field_type(value)
    except (TypeError, ValueError):
        return value


def _slot_to_wire(slot: OfficeSlot) -> dict[str, object]:
    if isinstance(slot, NativeSlot):
        return {
            "type": "native_slot",
            "source_index": slot.source_index,
            "blocks": tuple(_value_to_wire(block) for block in slot.blocks),
        }
    if isinstance(slot, ImageSlot):
        return {
            "type": "image_slot",
            "source_index": slot.source_index,
            "artifact_name": slot.artifact_name,
            "content_sha256": slot.content_sha256,
            "bbox": (
                float(slot.bbox.left),
                float(slot.bbox.top),
                float(slot.bbox.right),
                float(slot.bbox.bottom),
            ),
            "alt_text": slot.alt_text,
        }
    return {"type": "break_slot", "source_index": slot.source_index}


def _bbox_from_wire(value: object) -> BBox:
    if not isinstance(value, tuple) or len(value) != 4:
        raise ValueError("Office image slot bbox is invalid")
    numbers: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int | float):
            raise ValueError("Office image slot bbox is invalid")
        numbers.append(float(item))
    try:
        return BBox(*numbers).require_normalized("bbox")
    except (TypeError, ValueError) as error:
        raise ValueError("Office image slot bbox is invalid") from error


def _slot_from_wire(value: object) -> OfficeSlot:
    if not isinstance(value, dict) or not isinstance(value.get("type"), str):
        raise ValueError("Office slot wire is invalid")
    payload = cast(dict[str, object], value)
    kind = payload["type"]
    if kind == "native_slot":
        if set(payload) != {"type", "source_index", "blocks"}:
            raise ValueError("Office native slot wire is invalid")
        blocks_value = payload["blocks"]
        if not isinstance(blocks_value, tuple):
            raise ValueError("Office native slot blocks are invalid")
        blocks = tuple(cast(Block, _value_from_wire(block)) for block in blocks_value)
        return NativeSlot(
            source_index=_require_source_index("source_index", payload["source_index"]),
            blocks=blocks,
        )
    if kind == "image_slot":
        if set(payload) != {
            "type",
            "source_index",
            "artifact_name",
            "content_sha256",
            "bbox",
            "alt_text",
        }:
            raise ValueError("Office image slot wire is invalid")
        return ImageSlot(
            source_index=_require_source_index("source_index", payload["source_index"]),
            artifact_name=_require_basename("artifact_name", payload["artifact_name"]),
            content_sha256=_require_sha256("content_sha256", payload["content_sha256"]),
            bbox=_bbox_from_wire(payload["bbox"]),
            alt_text=_require_optional_string("alt_text", payload["alt_text"]),
        )
    if kind == "break_slot":
        if set(payload) != {"type", "source_index"}:
            raise ValueError("Office break slot wire is invalid")
        return BreakSlot(
            source_index=_require_source_index("source_index", payload["source_index"])
        )
    raise ValueError("Office slot type is invalid")


def document_to_wire(document: OfficeDocument) -> dict[str, object]:
    return {
        "type": "office_document",
        "document_type": document.document_type.value,
        "pages": tuple(
            {
                "type": "office_page",
                "page_number": page.page_number,
                "slots": tuple(_slot_to_wire(slot) for slot in page.slots),
            }
            for page in document.pages
        ),
        "warnings": tuple(_value_to_wire(warning) for warning in document.warnings),
    }


def document_from_wire(value: object) -> OfficeDocument:
    if not isinstance(value, dict) or value.get("type") != "office_document":
        raise ValueError("Office document wire is invalid")
    payload = cast(dict[str, object], value)
    if set(payload) != {"type", "document_type", "pages", "warnings"}:
        raise ValueError("Office document wire is invalid")
    document_type_value = payload["document_type"]
    if not isinstance(document_type_value, str):
        raise ValueError("Office document type is invalid")
    try:
        document_type = DocumentType(document_type_value)
    except ValueError as error:
        raise ValueError("Office document type is invalid") from error
    if document_type not in {DocumentType.DOCX, DocumentType.PPTX}:
        raise ValueError("Office document type is invalid")
    pages_value = payload["pages"]
    if not isinstance(pages_value, tuple):
        raise ValueError("Office document pages are invalid")
    pages: list[OfficePage] = []
    for page_value in pages_value:
        if not isinstance(page_value, dict) or page_value.get("type") != "office_page":
            raise ValueError("Office page wire is invalid")
        page_payload = cast(dict[str, object], page_value)
        if set(page_payload) != {"type", "page_number", "slots"}:
            raise ValueError("Office page wire is invalid")
        slots_value = page_payload["slots"]
        if not isinstance(slots_value, tuple):
            raise ValueError("Office page slots are invalid")
        pages.append(
            OfficePage(
                page_number=_require_int("page_number", page_payload["page_number"]),
                slots=tuple(_slot_from_wire(slot) for slot in slots_value),
            )
        )
    warnings_value = payload["warnings"]
    if not isinstance(warnings_value, tuple):
        raise ValueError("Office document warnings are invalid")
    warnings = tuple(cast(WarningRecord, _value_from_wire(warning)) for warning in warnings_value)
    return OfficeDocument(document_type=document_type, pages=tuple(pages), warnings=warnings)
