from __future__ import annotations

from typing import Protocol

from opendocs._models import ParsedDocument
from opendocs.options import ParseOptions
from opendocs.source import ResolvedSource


class DocumentParser(Protocol):
    async def parse(
        self,
        source: ResolvedSource,
        *,
        options: ParseOptions,
    ) -> ParsedDocument: ...
