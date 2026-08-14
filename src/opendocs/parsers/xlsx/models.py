from __future__ import annotations

import re
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum, StrEnum
from functools import cache
from pathlib import Path
from typing import Any, TypeAlias, cast, get_type_hints

import opendocs._models as core_models
from opendocs._models import Block, WarningRecord
from opendocs.errors import LimitExceededError

MAX_NATIVE_WIRE_ESTIMATE = 8 * 1024 * 1024

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_A1_RE = re.compile(r"^([A-Z]{1,3})([1-9][0-9]{0,6})(?::([A-Z]{1,3})([1-9][0-9]{0,6}))?$")
_DATACLASS_TYPE = "__xlsx_dataclass__"
_XLSX_MAX_COLUMN = 16_384
_XLSX_MAX_ROW = 1_048_576
_WIRE_NODE_OVERHEAD = 32


def _require_int(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int")
    return value


def _require_string(name: str, value: object) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a str")
    return value


def _require_non_empty_string(name: str, value: object) -> str:
    normalized = _require_string(name, value)
    if not normalized:
        raise ValueError(f"{name} must not be empty")
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise ValueError(f"{name} must not contain control characters")
    return normalized


def _require_sheet_name(value: object) -> str:
    name = _require_non_empty_string("name", value)
    if len(name) > 31 or any(character in "[]:*?/\\" for character in name):
        raise ValueError("name must be a valid XLSX sheet name")
    return name


def _require_optional_string(name: str, value: object) -> str | None:
    if value is None:
        return None
    return _require_string(name, value)


def _require_tuple(name: str, value: object) -> tuple[object, ...]:
    if not isinstance(value, tuple):
        raise TypeError(f"{name} must be a tuple")
    return value


def _column_number(label: str) -> int:
    number = 0
    for character in label:
        number = number * 26 + ord(character) - ord("A") + 1
    return number


def _require_anchor(name: str, value: object) -> str:
    anchor = _require_string(name, value)
    match = _A1_RE.fullmatch(anchor)
    if match is None:
        raise ValueError(f"{name} must be a canonical A1 anchor or range")
    start_column = _column_number(match.group(1))
    start_row = int(match.group(2))
    end_column = _column_number(match.group(3) or match.group(1))
    end_row = int(match.group(4) or match.group(2))
    if (
        start_column > _XLSX_MAX_COLUMN
        or end_column > _XLSX_MAX_COLUMN
        or start_row > _XLSX_MAX_ROW
        or end_row > _XLSX_MAX_ROW
        or end_column < start_column
        or end_row < start_row
    ):
        raise ValueError(f"{name} must be a canonical A1 anchor or range")
    return anchor


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


def _require_source_index(name: str, value: object) -> int:
    source_index = _require_int(name, value)
    if source_index < 0:
        raise ValueError(f"{name} must be greater than or equal to zero")
    return source_index


def _block_class_names() -> tuple[str, ...]:
    return (
        "TextBlock",
        "MarkdownBlock",
        "TableBlock",
        "InlineText",
        "InlineLink",
        "ParagraphBlock",
        "HeadingBlock",
        "ListItemBlock",
        "SpannedTableCell",
        "SpannedTableBlock",
    )


_MODEL_REGISTRY: dict[str, type[Any]] = {}
for _name in (*_block_class_names(), "WarningRecord"):
    _class = getattr(core_models, _name, None)
    if isinstance(_class, type) and is_dataclass(_class):
        _MODEL_REGISTRY[_name] = _class

_KNOWN_BLOCK_TYPES = tuple(
    value for name, value in _MODEL_REGISTRY.items() if name != "WarningRecord"
)


def _require_blocks(value: object) -> tuple[Block, ...]:
    blocks = _require_tuple("blocks", value)
    if not blocks:
        raise ValueError("blocks must contain at least one block")
    for index, block in enumerate(blocks):
        if not isinstance(block, _KNOWN_BLOCK_TYPES):
            raise TypeError(f"blocks[{index}] is not a supported block type")
    return cast(tuple[Block, ...], blocks)


class XlsxSheetKind(StrEnum):
    WORKSHEET = "worksheet"
    CHARTSHEET = "chartsheet"


class XlsxSheetState(StrEnum):
    VISIBLE = "visible"
    HIDDEN = "hidden"
    VERY_HIDDEN = "veryHidden"


@dataclass(frozen=True, slots=True)
class XlsxNativeSlot:
    source_index: int
    anchor: str
    blocks: tuple[Block, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "source_index", _require_source_index("source_index", self.source_index)
        )
        object.__setattr__(self, "anchor", _require_anchor("anchor", self.anchor))
        object.__setattr__(self, "blocks", _require_blocks(self.blocks))


@dataclass(frozen=True, slots=True)
class XlsxImageSlot:
    source_index: int
    anchor: str
    artifact_name: str
    content_sha256: str
    alt_text: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "source_index", _require_source_index("source_index", self.source_index)
        )
        object.__setattr__(self, "anchor", _require_anchor("anchor", self.anchor))
        object.__setattr__(
            self, "artifact_name", _require_basename("artifact_name", self.artifact_name)
        )
        object.__setattr__(
            self,
            "content_sha256",
            _require_sha256("content_sha256", self.content_sha256),
        )
        object.__setattr__(self, "alt_text", _require_optional_string("alt_text", self.alt_text))


@dataclass(frozen=True, slots=True)
class XlsxChartSlot:
    source_index: int
    anchor: str
    artifact_name: str
    content_sha256: str
    blocks: tuple[Block, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "source_index", _require_source_index("source_index", self.source_index)
        )
        object.__setattr__(self, "anchor", _require_anchor("anchor", self.anchor))
        object.__setattr__(
            self, "artifact_name", _require_basename("artifact_name", self.artifact_name)
        )
        object.__setattr__(
            self,
            "content_sha256",
            _require_sha256("content_sha256", self.content_sha256),
        )
        object.__setattr__(self, "blocks", _require_blocks(self.blocks))


XlsxSlot: TypeAlias = XlsxNativeSlot | XlsxImageSlot | XlsxChartSlot


@dataclass(frozen=True, slots=True)
class XlsxSheet:
    sheet_index: int
    name: str
    kind: XlsxSheetKind
    state: XlsxSheetState
    slots: tuple[XlsxSlot, ...]

    def __post_init__(self) -> None:
        sheet_index = _require_int("sheet_index", self.sheet_index)
        if sheet_index <= 0:
            raise ValueError("sheet_index must be greater than zero")
        if not isinstance(self.kind, XlsxSheetKind):
            raise TypeError("kind must be an XlsxSheetKind")
        if not isinstance(self.state, XlsxSheetState):
            raise TypeError("state must be an XlsxSheetState")
        slots = _require_tuple("slots", self.slots)
        seen: set[int] = set()
        for index, slot in enumerate(slots):
            if not isinstance(slot, XlsxNativeSlot | XlsxImageSlot | XlsxChartSlot):
                raise TypeError(f"slots[{index}] must be an XLSX slot")
            if slot.source_index in seen:
                raise ValueError("XLSX sheet source indexes must be unique")
            seen.add(slot.source_index)
        object.__setattr__(self, "sheet_index", sheet_index)
        object.__setattr__(self, "name", _require_sheet_name(self.name))
        object.__setattr__(self, "slots", cast(tuple[XlsxSlot, ...], slots))


@dataclass(frozen=True, slots=True)
class XlsxDocument:
    sheets: tuple[XlsxSheet, ...]
    warnings: tuple[WarningRecord, ...] = ()

    def __post_init__(self) -> None:
        sheets = _require_tuple("sheets", self.sheets)
        seen: set[int] = set()
        for position, sheet in enumerate(sheets, start=1):
            if not isinstance(sheet, XlsxSheet):
                raise TypeError(f"sheets[{position - 1}] must be an XlsxSheet")
            if sheet.sheet_index in seen or sheet.sheet_index != position:
                raise ValueError("XLSX sheet indexes must be unique and preserve source order")
            seen.add(sheet.sheet_index)
        warnings = _require_tuple("warnings", self.warnings)
        for index, warning in enumerate(warnings):
            if not isinstance(warning, WarningRecord):
                raise TypeError(f"warnings[{index}] must be a WarningRecord")
        object.__setattr__(self, "sheets", cast(tuple[XlsxSheet, ...], sheets))
        object.__setattr__(self, "warnings", cast(tuple[WarningRecord, ...], warnings))


def _dataclass_to_wire(value: object) -> dict[str, object]:
    return {
        _DATACLASS_TYPE: type(value).__name__,
        "fields": {
            field.name: _value_to_wire(getattr(value, field.name))
            for field in fields(cast(Any, value))
        },
    }


def _value_to_wire(value: object) -> object:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return tuple(_value_to_wire(item) for item in value)
    if is_dataclass(value) and type(value).__name__ in _MODEL_REGISTRY:
        return _dataclass_to_wire(value)
    raise TypeError(f"XLSX wire value type is not supported: {type(value).__name__}")


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


def _decode_dataclass(value: dict[str, object]) -> object:
    if set(value) != {_DATACLASS_TYPE, "fields"}:
        raise ValueError("XLSX dataclass wire is invalid")
    class_name = value[_DATACLASS_TYPE]
    fields_value = value["fields"]
    if not isinstance(class_name, str) or not isinstance(fields_value, dict):
        raise ValueError("XLSX dataclass wire is invalid")
    cls = _MODEL_REGISTRY.get(class_name)
    if cls is None:
        raise ValueError("XLSX dataclass type is invalid")
    field_names = {field.name for field in fields(cast(Any, cls))}
    typed_fields = cast(dict[str, object], fields_value)
    if set(typed_fields) != field_names:
        raise ValueError("XLSX dataclass wire is invalid")
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
    raise ValueError("XLSX wire value is invalid")


def _slot_to_wire(slot: XlsxSlot) -> dict[str, object]:
    common: dict[str, object] = {
        "source_index": slot.source_index,
        "anchor": slot.anchor,
    }
    if isinstance(slot, XlsxNativeSlot):
        return {
            "type": "xlsx_native_slot",
            **common,
            "blocks": tuple(_value_to_wire(block) for block in slot.blocks),
        }
    if isinstance(slot, XlsxImageSlot):
        return {
            "type": "xlsx_image_slot",
            **common,
            "artifact_name": slot.artifact_name,
            "content_sha256": slot.content_sha256,
            "alt_text": slot.alt_text,
        }
    return {
        "type": "xlsx_chart_slot",
        **common,
        "artifact_name": slot.artifact_name,
        "content_sha256": slot.content_sha256,
        "blocks": tuple(_value_to_wire(block) for block in slot.blocks),
    }


def _slot_from_wire(value: object) -> XlsxSlot:
    if not isinstance(value, dict) or not isinstance(value.get("type"), str):
        raise ValueError("XLSX slot wire is invalid")
    payload = cast(dict[str, object], value)
    kind = payload["type"]
    if kind == "xlsx_native_slot":
        if set(payload) != {"type", "source_index", "anchor", "blocks"}:
            raise ValueError("XLSX native slot wire is invalid")
        blocks_value = payload["blocks"]
        if not isinstance(blocks_value, tuple):
            raise ValueError("XLSX native slot blocks are invalid")
        return XlsxNativeSlot(
            source_index=_require_source_index("source_index", payload["source_index"]),
            anchor=_require_anchor("anchor", payload["anchor"]),
            blocks=tuple(cast(Block, _value_from_wire(block)) for block in blocks_value),
        )
    if kind == "xlsx_image_slot":
        if set(payload) != {
            "type",
            "source_index",
            "anchor",
            "artifact_name",
            "content_sha256",
            "alt_text",
        }:
            raise ValueError("XLSX image slot wire is invalid")
        return XlsxImageSlot(
            source_index=_require_source_index("source_index", payload["source_index"]),
            anchor=_require_anchor("anchor", payload["anchor"]),
            artifact_name=_require_basename("artifact_name", payload["artifact_name"]),
            content_sha256=_require_sha256("content_sha256", payload["content_sha256"]),
            alt_text=_require_optional_string("alt_text", payload["alt_text"]),
        )
    if kind == "xlsx_chart_slot":
        if set(payload) != {
            "type",
            "source_index",
            "anchor",
            "artifact_name",
            "content_sha256",
            "blocks",
        }:
            raise ValueError("XLSX chart slot wire is invalid")
        blocks_value = payload["blocks"]
        if not isinstance(blocks_value, tuple):
            raise ValueError("XLSX chart slot blocks are invalid")
        return XlsxChartSlot(
            source_index=_require_source_index("source_index", payload["source_index"]),
            anchor=_require_anchor("anchor", payload["anchor"]),
            artifact_name=_require_basename("artifact_name", payload["artifact_name"]),
            content_sha256=_require_sha256("content_sha256", payload["content_sha256"]),
            blocks=tuple(cast(Block, _value_from_wire(block)) for block in blocks_value),
        )
    raise ValueError("XLSX slot type is invalid")


def _primitive_wire_estimate(value: object) -> int:
    if value is None or isinstance(value, bool | int | float):
        return _WIRE_NODE_OVERHEAD
    if isinstance(value, str):
        return _WIRE_NODE_OVERHEAD + len(value) * 4
    if isinstance(value, Enum):
        return _primitive_wire_estimate(value.value)
    if isinstance(value, tuple):
        return _WIRE_NODE_OVERHEAD + sum(_primitive_wire_estimate(item) for item in value)
    if is_dataclass(value) and type(value).__name__ in _MODEL_REGISTRY:
        estimate = _WIRE_NODE_OVERHEAD
        estimate += _primitive_wire_estimate(_DATACLASS_TYPE)
        estimate += _primitive_wire_estimate(type(value).__name__)
        estimate += _primitive_wire_estimate("fields") + _WIRE_NODE_OVERHEAD
        for field in fields(cast(Any, value)):
            estimate += _primitive_wire_estimate(field.name)
            estimate += _primitive_wire_estimate(getattr(value, field.name))
        return estimate
    raise TypeError(f"XLSX wire value type is not supported: {type(value).__name__}")


def _wire_estimate(document: XlsxDocument) -> int:
    estimate = 512
    for sheet in document.sheets:
        estimate += 512 + len(sheet.name) * 4
        for slot in sheet.slots:
            estimate += 512 + len(slot.anchor) * 4
            if isinstance(slot, XlsxNativeSlot | XlsxChartSlot):
                estimate += sum(_primitive_wire_estimate(block) for block in slot.blocks)
            if isinstance(slot, XlsxImageSlot | XlsxChartSlot):
                estimate += 512 + len(slot.artifact_name) * 4 + len(slot.content_sha256) * 4
            if isinstance(slot, XlsxImageSlot) and slot.alt_text is not None:
                estimate += len(slot.alt_text) * 4
    estimate += sum(_primitive_wire_estimate(warning) for warning in document.warnings)
    return estimate


def document_to_wire(document: XlsxDocument) -> dict[str, object]:
    if not isinstance(document, XlsxDocument):
        raise TypeError("document must be an XlsxDocument")
    if _wire_estimate(document) > MAX_NATIVE_WIRE_ESTIMATE:
        raise LimitExceededError("XLSX native document exceeds the inline result budget")
    return {
        "type": "xlsx_document",
        "sheets": tuple(
            {
                "type": "xlsx_sheet",
                "sheet_index": sheet.sheet_index,
                "name": sheet.name,
                "kind": sheet.kind.value,
                "state": sheet.state.value,
                "slots": tuple(_slot_to_wire(slot) for slot in sheet.slots),
            }
            for sheet in document.sheets
        ),
        "warnings": tuple(_value_to_wire(warning) for warning in document.warnings),
    }


def document_from_wire(value: object) -> XlsxDocument:
    if not isinstance(value, dict) or value.get("type") != "xlsx_document":
        raise ValueError("XLSX document wire is invalid")
    payload = cast(dict[str, object], value)
    if set(payload) != {"type", "sheets", "warnings"}:
        raise ValueError("XLSX document wire is invalid")
    sheets_value = payload["sheets"]
    if not isinstance(sheets_value, tuple):
        raise ValueError("XLSX document sheets are invalid")
    sheets: list[XlsxSheet] = []
    for sheet_value in sheets_value:
        if not isinstance(sheet_value, dict) or sheet_value.get("type") != "xlsx_sheet":
            raise ValueError("XLSX sheet wire is invalid")
        sheet_payload = cast(dict[str, object], sheet_value)
        if set(sheet_payload) != {"type", "sheet_index", "name", "kind", "state", "slots"}:
            raise ValueError("XLSX sheet wire is invalid")
        slots_value = sheet_payload["slots"]
        if not isinstance(slots_value, tuple):
            raise ValueError("XLSX sheet slots are invalid")
        try:
            kind = XlsxSheetKind(sheet_payload["kind"])
            state = XlsxSheetState(sheet_payload["state"])
        except (TypeError, ValueError) as error:
            raise ValueError("XLSX sheet kind or state is invalid") from error
        sheets.append(
            XlsxSheet(
                sheet_index=_require_int("sheet_index", sheet_payload["sheet_index"]),
                name=_require_sheet_name(sheet_payload["name"]),
                kind=kind,
                state=state,
                slots=tuple(_slot_from_wire(slot) for slot in slots_value),
            )
        )
    warnings_value = payload["warnings"]
    if not isinstance(warnings_value, tuple):
        raise ValueError("XLSX document warnings are invalid")
    warnings = tuple(cast(WarningRecord, _value_from_wire(item)) for item in warnings_value)
    return XlsxDocument(sheets=tuple(sheets), warnings=warnings)
