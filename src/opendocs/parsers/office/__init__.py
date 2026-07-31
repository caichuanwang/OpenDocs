"""Private Office parsing package."""

from .models import (
    BreakSlot,
    ImageSlot,
    NativeSlot,
    OfficeDocument,
    OfficePage,
    document_from_wire,
    document_to_wire,
)
from .package import (
    MAX_ARCHIVE_MEMBERS,
    MAX_COMPRESSION_RATIO,
    MAX_MEDIA_PART_BYTES,
    MAX_MEDIA_PARTS,
    MAX_TOTAL_ARCHIVE_BYTES,
    MAX_TOTAL_MEDIA_BYTES,
    MAX_XML_PART_BYTES,
    extract_package_media,
    open_validated_office_document,
    validate_office_package,
)

__all__ = [
    "MAX_ARCHIVE_MEMBERS",
    "MAX_COMPRESSION_RATIO",
    "MAX_MEDIA_PARTS",
    "MAX_MEDIA_PART_BYTES",
    "MAX_TOTAL_ARCHIVE_BYTES",
    "MAX_TOTAL_MEDIA_BYTES",
    "MAX_XML_PART_BYTES",
    "BreakSlot",
    "ImageSlot",
    "NativeSlot",
    "OfficeDocument",
    "OfficePage",
    "document_from_wire",
    "document_to_wire",
    "extract_package_media",
    "open_validated_office_document",
    "validate_office_package",
]
