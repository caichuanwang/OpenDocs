from __future__ import annotations

import io
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
OFFICE_DOCUMENT_RELATIONSHIP = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
)


def minimal_xlsx_entries(
    *,
    include_content_types: bool = True,
    include_root_relationships: bool = True,
    include_workbook: bool = True,
    workbook_content_type: str = XLSX_CONTENT_TYPE,
    root_target: str = "xl/workbook.xml",
    root_target_mode: str | None = None,
) -> list[tuple[str, bytes]]:
    entries: list[tuple[str, bytes]] = []
    if include_content_types:
        entries.append(
            (
                "[Content_Types].xml",
                f"""<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Override PartName="/xl/workbook.xml" ContentType="{workbook_content_type}"/>
</Types>
""".encode(),
            )
        )
    if include_root_relationships:
        target_mode = f' TargetMode="{root_target_mode}"' if root_target_mode else ""
        entries.append(
            (
                "_rels/.rels",
                f"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="{OFFICE_DOCUMENT_RELATIONSHIP}"
   Target="{root_target}"{target_mode}/>
</Relationships>
""".encode(),
            )
        )
    if include_workbook:
        entries.append(
            (
                "xl/workbook.xml",
                b"<workbook xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'/>",
            )
        )
    return entries


def xlsx_bytes(
    *,
    include_content_types: bool = True,
    include_root_relationships: bool = True,
    include_workbook: bool = True,
    workbook_content_type: str = XLSX_CONTENT_TYPE,
    root_target: str = "xl/workbook.xml",
    root_target_mode: str | None = None,
) -> bytes:
    output = io.BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for name, data in minimal_xlsx_entries(
            include_content_types=include_content_types,
            include_root_relationships=include_root_relationships,
            include_workbook=include_workbook,
            workbook_content_type=workbook_content_type,
            root_target=root_target,
            root_target_mode=root_target_mode,
        ):
            archive.writestr(name, data)
    return output.getvalue()


def write_xlsx(
    path: Path,
    *,
    include_content_types: bool = True,
    include_root_relationships: bool = True,
    include_workbook: bool = True,
    workbook_content_type: str = XLSX_CONTENT_TYPE,
    root_target: str = "xl/workbook.xml",
    root_target_mode: str | None = None,
) -> None:
    path.write_bytes(
        xlsx_bytes(
            include_content_types=include_content_types,
            include_root_relationships=include_root_relationships,
            include_workbook=include_workbook,
            workbook_content_type=workbook_content_type,
            root_target=root_target,
            root_target_mode=root_target_mode,
        )
    )
