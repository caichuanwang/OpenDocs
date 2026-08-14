from __future__ import annotations

import asyncio

from opendocs._models import ParsedDocument
from opendocs.errors import UnsupportedDocumentError
from opendocs.options import ParseOptions
from opendocs.parsers.xlsx.preflight import preflight_xlsx
from opendocs.source import ResolvedSource


class XlsxParser:
    async def parse(
        self,
        source: ResolvedSource,
        *,
        options: ParseOptions,
    ) -> ParsedDocument:
        del options
        await asyncio.to_thread(preflight_xlsx, source.path)
        raise UnsupportedDocumentError("XLSX content parsing is not implemented in this release")
