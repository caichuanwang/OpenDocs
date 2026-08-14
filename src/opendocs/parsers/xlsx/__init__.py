from __future__ import annotations

import asyncio

from opendocs._models import DocumentType, ParsedDocument
from opendocs.errors import UnsupportedDocumentError
from opendocs.options import ParseOptions
from opendocs.parsers.office.package import validate_office_package
from opendocs.source import ResolvedSource


class XlsxParser:
    async def parse(
        self,
        source: ResolvedSource,
        *,
        options: ParseOptions,
    ) -> ParsedDocument:
        del options
        await asyncio.to_thread(
            validate_office_package,
            source.path,
            document_type=DocumentType.XLSX,
        )
        raise UnsupportedDocumentError("XLSX content parsing is not implemented in this release")
