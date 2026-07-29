from __future__ import annotations

import importlib
import os
import subprocess
import sys
import time

from opendocs.errors import CorruptDocumentError, LimitExceededError


def dependency_versions() -> tuple[str, str]:
    pdfplumber = importlib.import_module("pdfplumber")
    pillow = importlib.import_module("PIL")
    return str(pillow.__version__), str(pdfplumber.__version__)


def echo(value: object) -> object:
    return value


def sleep_and_echo(delay: float, value: object) -> object:
    time.sleep(delay)
    return value


def raise_corrupt(message: str) -> None:
    raise CorruptDocumentError(message)


def raise_limit(message: str) -> None:
    raise LimitExceededError(message)


def raise_unknown(message: str) -> None:
    raise LookupError(message)


def unsupported_result() -> object:
    return object()


def noisy_echo(value: object) -> object:
    print("python stdout noise", flush=True)
    os.write(1, b"fd stdout noise\n")
    subprocess.run(
        [sys.executable, "-c", "import os; os.write(1, b'child stdout noise\\n')"],
        check=True,
    )
    return value


def make_bytes(size: int) -> bytes:
    return b"x" * size
