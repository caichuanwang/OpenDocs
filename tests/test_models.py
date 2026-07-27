from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Any, cast

import pytest

from opendocs._models import (
    DocumentType,
    MarkdownBlock,
    ParsedDocument,
    RenderResult,
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


@pytest.mark.parametrize(
    ("factory", "field_name"),
    [
        (lambda value: TextBlock(value), "text"),
        (lambda value: MarkdownBlock(value), "markdown"),
        (lambda value: WarningRecord(code=value, message="message"), "code"),
        (lambda value: WarningRecord(code="code", message=value), "message"),
        (lambda value: RenderResult(markdown=value), "markdown"),
    ],
)
def test_models_reject_non_string_fields(
    factory: Any,
    field_name: str,
) -> None:
    with pytest.raises(TypeError, match=field_name):
        factory(cast(Any, 123))


def test_parsed_document_rejects_non_document_type() -> None:
    with pytest.raises(TypeError, match="document_type"):
        ParsedDocument(
            document_type=cast(Any, "text"),
            blocks=(TextBlock("alpha"),),
        )


@pytest.mark.parametrize(
    ("field_name", "kwargs"),
    [
        (
            "blocks",
            {
                "document_type": DocumentType.TEXT,
                "blocks": cast(Any, [TextBlock("alpha")]),
            },
        ),
        (
            "warnings",
            {
                "document_type": DocumentType.TEXT,
                "blocks": (TextBlock("alpha"),),
                "warnings": cast(Any, [WarningRecord(code="code", message="message")]),
            },
        ),
    ],
)
def test_parsed_document_requires_tuple_fields(
    field_name: str,
    kwargs: dict[str, Any],
) -> None:
    with pytest.raises(TypeError, match=field_name):
        ParsedDocument(**kwargs)


def test_render_result_requires_tuple_warnings() -> None:
    with pytest.raises(TypeError, match="warnings"):
        RenderResult(
            markdown="alpha",
            warnings=cast(Any, [WarningRecord(code="code", message="message")]),
        )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        (
            {
                "document_type": DocumentType.TEXT,
                "blocks": (TextBlock("alpha"), cast(Any, "beta")),
            },
            "blocks\\[1\\]",
        ),
        (
            {
                "document_type": DocumentType.TEXT,
                "blocks": (TextBlock("alpha"),),
                "warnings": (WarningRecord(code="code", message="message"), cast(Any, "beta")),
            },
            "warnings\\[1\\]",
        ),
    ],
)
def test_parsed_document_rejects_invalid_tuple_elements(
    kwargs: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises(TypeError, match=message):
        ParsedDocument(**kwargs)
