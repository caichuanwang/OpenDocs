from __future__ import annotations

from typing import Protocol, runtime_checkable

from opendocs._models import ParsedDocument
from opendocs.options import ParseOptions
from opendocs.source import ResolvedSource


@runtime_checkable
class DocumentParser(Protocol):
    async def parse(
        self,
        source: ResolvedSource,
        *,
        options: ParseOptions,
    ) -> ParsedDocument: ...
