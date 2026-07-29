from __future__ import annotations

GENERAL_IMAGE_PROMPT = """Extract the document content from this image in source order.
Return concise Markdown or the requested structured JSON. Do not describe decorative elements.
"""

TABLE_IMAGE_PROMPT = """Extract the complete table from this image.
Return JSON only using the supplied schema. Preserve every row and column, represent merged or
multi-row headers with header_rows, and use an empty string for a visually empty cell.
"""

REPAIR_PROMPT = """Repair the prior response into JSON matching the supplied schema exactly.
Return JSON only. Do not add explanation or Markdown fences.
"""

VISION_RESPONSE_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "elements": {
            "type": "array",
            "items": {
                "oneOf": [
                    {
                        "type": "object",
                        "properties": {
                            "type": {"const": "text"},
                            "text": {"type": "string"},
                            "source_index": {"type": "integer", "minimum": 0},
                            "bbox": {
                                "type": ["array", "null"],
                                "items": {"type": "number", "minimum": 0, "maximum": 1},
                                "minItems": 4,
                                "maxItems": 4,
                            },
                        },
                        "required": ["type", "text", "source_index"],
                        "additionalProperties": False,
                    },
                    {
                        "type": "object",
                        "properties": {
                            "type": {"const": "table"},
                            "grid": {
                                "type": "array",
                                "minItems": 1,
                                "items": {
                                    "type": "array",
                                    "minItems": 1,
                                    "items": {"type": ["string", "null"]},
                                },
                            },
                            "header_rows": {"type": "integer", "minimum": 0},
                            "source_index": {"type": "integer", "minimum": 0},
                            "bbox": {
                                "type": ["array", "null"],
                                "items": {"type": "number", "minimum": 0, "maximum": 1},
                                "minItems": 4,
                                "maxItems": 4,
                            },
                        },
                        "required": ["type", "grid", "header_rows", "source_index"],
                        "additionalProperties": False,
                    },
                ]
            },
        }
    },
    "required": ["elements"],
    "additionalProperties": False,
}

VISION_RESPONSE_FORMAT: dict[str, object] = {
    "type": "json_schema",
    "json_schema": {
        "name": "opendocs_vision_elements",
        "strict": True,
        "schema": VISION_RESPONSE_SCHEMA,
    },
}
