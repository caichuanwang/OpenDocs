from __future__ import annotations

import base64
import json
import struct
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

PROTOCOL_VERSION = 1
MAX_INLINE_BYTES = 8 * 1024 * 1024
MAX_FRAME_BYTES = 12 * 1024 * 1024
MAX_CONTAINER_ITEMS = 100_000
_HEADER = struct.Struct("!Q")
_TYPE = "__opendocs_type__"
_NODE_OVERHEAD = 32


def _preflight(value: object, remaining: int, active: set[int]) -> int:
    if remaining < 0:
        raise ValueError(f"native worker inline values exceed {MAX_INLINE_BYTES} bytes")
    if value is None or isinstance(value, bool | int | float):
        return remaining - _NODE_OVERHEAD
    if isinstance(value, str):
        # Four bytes per code point is a conservative UTF-8 upper bound and avoids
        # allocating a second copy merely to enforce the inline policy.
        return remaining - _NODE_OVERHEAD - len(value) * 4
    if isinstance(value, bytes):
        return remaining - _NODE_OVERHEAD - len(value)
    if isinstance(value, Path):
        return remaining - _NODE_OVERHEAD - len(str(value)) * 4
    if not isinstance(value, tuple | list | dict):
        raise TypeError(f"native worker value type is not supported: {type(value).__name__}")

    identity = id(value)
    if identity in active:
        raise ValueError("native worker values must not contain cycles")
    if len(value) > MAX_CONTAINER_ITEMS:
        raise ValueError(f"native worker container exceeds {MAX_CONTAINER_ITEMS} items")
    active.add(identity)
    try:
        remaining -= _NODE_OVERHEAD
        if isinstance(value, dict):
            for key, item in value.items():
                if not isinstance(key, str):
                    raise TypeError("native worker dict keys must be strings")
                remaining = _preflight(key, remaining, active)
                remaining = _preflight(item, remaining, active)
                if remaining < 0:
                    raise ValueError(f"native worker inline values exceed {MAX_INLINE_BYTES} bytes")
        else:
            for item in value:
                remaining = _preflight(item, remaining, active)
                if remaining < 0:
                    raise ValueError(f"native worker inline values exceed {MAX_INLINE_BYTES} bytes")
    finally:
        active.remove(identity)
    return remaining


def _to_wire(value: object) -> object:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, bytes):
        return {_TYPE: "bytes", "data": base64.b64encode(value).decode("ascii")}
    if isinstance(value, Path):
        return {_TYPE: "path", "data": str(value)}
    if isinstance(value, tuple):
        return {_TYPE: "tuple", "items": [_to_wire(item) for item in value]}
    if isinstance(value, list):
        return {_TYPE: "list", "items": [_to_wire(item) for item in value]}
    if isinstance(value, dict):
        return {
            _TYPE: "dict",
            "items": [[key, _to_wire(item)] for key, item in value.items()],
        }
    raise TypeError(f"native worker value type is not supported: {type(value).__name__}")


def _from_wire(value: object) -> object:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if not isinstance(value, dict) or not isinstance(value.get(_TYPE), str):
        raise ValueError("native worker value is invalid")
    mapping = cast(dict[str, object], value)
    kind = cast(str, mapping[_TYPE])
    data = mapping.get("data")
    items_value = mapping.get("items")
    if kind == "bytes" and isinstance(data, str):
        try:
            return base64.b64decode(data, validate=True)
        except ValueError as error:
            raise ValueError("native worker bytes value is invalid") from error
    if kind == "path" and isinstance(data, str):
        return Path(data)
    if kind in {"tuple", "list"} and isinstance(items_value, list):
        items = [_from_wire(item) for item in items_value]
        return tuple(items) if kind == "tuple" else items
    if kind == "dict" and isinstance(items_value, list):
        result: dict[str, object] = {}
        for item in items_value:
            if not isinstance(item, list) or len(item) != 2 or not isinstance(item[0], str):
                raise ValueError("native worker dict value is invalid")
            if item[0] in result:
                raise ValueError("native worker dict contains duplicate keys")
            result[item[0]] = _from_wire(item[1])
        return result
    raise ValueError("native worker value type is invalid")


def encode_message(message: Mapping[str, object]) -> bytes:
    try:
        materialized = dict(message)
        _preflight(materialized, MAX_INLINE_BYTES, set())
        payload = json.dumps(
            _to_wire(materialized),
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (OverflowError, RecursionError, TypeError, ValueError) as error:
        raise ValueError(f"native worker message is not serializable: {error}") from error
    if len(payload) > MAX_FRAME_BYTES:
        raise ValueError(f"native worker message exceeds {MAX_FRAME_BYTES} bytes")
    return _HEADER.pack(len(payload)) + payload


def decode_payload(payload: bytes) -> dict[str, Any]:
    if len(payload) > MAX_FRAME_BYTES:
        raise ValueError(f"native worker message exceeds {MAX_FRAME_BYTES} bytes")
    try:
        decoded = json.loads(payload)
        message = _from_wire(decoded)
        _preflight(message, MAX_INLINE_BYTES, set())
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise ValueError("native worker message is invalid") from error
    if not isinstance(message, dict) or message.get("version") != PROTOCOL_VERSION:
        raise ValueError("native worker protocol version is invalid")
    return cast(dict[str, Any], message)


def read_sync(stream: Any) -> dict[str, Any] | None:
    header = stream.read(_HEADER.size)
    if not header:
        return None
    if len(header) != _HEADER.size:
        raise ValueError("native worker frame header is truncated")
    (size,) = _HEADER.unpack(header)
    if size > MAX_FRAME_BYTES:
        raise ValueError(f"native worker message exceeds {MAX_FRAME_BYTES} bytes")
    payload = stream.read(size)
    if len(payload) != size:
        raise ValueError("native worker frame payload is truncated")
    return decode_payload(payload)


def write_sync(stream: Any, message: Mapping[str, object]) -> None:
    frame = encode_message(message)
    stream.write(frame)
    stream.flush()
