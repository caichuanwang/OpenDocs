from __future__ import annotations

import importlib
import os
import sys
from collections.abc import Callable
from typing import Any, BinaryIO

from opendocs._native_protocol import PROTOCOL_VERSION, read_sync, write_sync
from opendocs.errors import OpenDocsError


def _resolve_callable(module_name: str, qualname: str) -> Callable[..., object]:
    module = importlib.import_module(module_name)
    value: object = module
    for component in qualname.split("."):
        value = getattr(value, component)
    if not callable(value):
        raise TypeError(f"native target is not callable: {module_name}.{qualname}")
    return value


def _error_message(error: BaseException) -> dict[str, object]:
    if isinstance(error, OpenDocsError):
        return {
            "version": PROTOCOL_VERSION,
            "operation": "error",
            "kind": "opendocs",
            "class": error.__class__.__name__,
            "code": error.code.value,
            "message": str(error),
            "retryable": error.retryable,
        }
    return {
        "version": PROTOCOL_VERSION,
        "operation": "error",
        "kind": "unknown",
        "class": error.__class__.__name__,
        "message": str(error),
        "retryable": False,
    }


def _open_protocol_stream() -> BinaryIO:
    # Keep one private duplicate of the original stdout for frames, then route
    # process FD 1 to stderr. Python print(), os.write(1, ...), and descendants
    # therefore cannot corrupt the protocol stream.
    protocol_fd = os.dup(1)
    try:
        sys.stdout.flush()
        os.dup2(2, 1)
        return os.fdopen(protocol_fd, "wb", buffering=0)
    except BaseException:
        os.close(protocol_fd)
        raise


def main() -> int:
    stdin = sys.stdin.buffer
    protocol = _open_protocol_stream()
    try:
        while True:
            try:
                request = read_sync(stdin)
            except BaseException as error:
                write_sync(protocol, _error_message(error))
                return 2
            if request is None or request.get("operation") == "stop":
                return 0
            if request.get("operation") != "call":
                write_sync(protocol, _error_message(ValueError("unknown native worker operation")))
                continue
            try:
                module_name = request["module"]
                qualname = request["qualname"]
                args = request["args"]
                kwargs = request["kwargs"]
                if not isinstance(module_name, str) or not isinstance(qualname, str):
                    raise TypeError("native target identity is invalid")
                if not isinstance(args, tuple) or not isinstance(kwargs, dict):
                    raise TypeError("native target arguments are invalid")
                function = _resolve_callable(module_name, qualname)
                value = function(*args, **kwargs)
                response: dict[str, Any] = {
                    "version": PROTOCOL_VERSION,
                    "operation": "result",
                    "value": value,
                }
                write_sync(protocol, response)
            except BaseException as error:
                try:
                    write_sync(protocol, _error_message(error))
                except BaseException as envelope_error:
                    write_sync(protocol, _error_message(envelope_error))
    finally:
        protocol.close()


if __name__ == "__main__":
    raise SystemExit(main())
