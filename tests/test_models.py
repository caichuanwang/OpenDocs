from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from opendocs._models import (
    DocumentType,
    MarkdownBlock,
    ParsedDocument,
    TextBlock,
    WarningRecord,
)


def test_document_type_exposes_stable_values() -> None:
    assert DocumentType.TEXT == "text"
    assert DocumentType.MARKDOWN == "markdown"
    assert DocumentType.PDF == "pdf"
    assert DocumentType.IMAGE == "image"
    assert DocumentType.DOCX == "docx"
    assert DocumentType.PPTX == "pptx"


def test_parsed_document_preserves_block_order_and_tuple_storage() -> None:
    first = TextBlock("alpha")
    second = MarkdownBlock("**beta**")
    warning = WarningRecord(code="kept", message="original warning")
    document = ParsedDocument(
        document_type=DocumentType.MARKDOWN,
        blocks=(first, second),
        warnings=(warning,),
    )

    assert document.blocks == (first, second)
    assert isinstance(document.blocks, tuple)
    assert document.warnings == (warning,)
    assert isinstance(document.warnings, tuple)


def test_models_are_frozen() -> None:
    document = ParsedDocument(
        document_type=DocumentType.TEXT,
        blocks=(TextBlock("alpha"),),
    )
    attribute_name = "blocks"

    with pytest.raises(FrozenInstanceError):
        setattr(document, attribute_name, ())
