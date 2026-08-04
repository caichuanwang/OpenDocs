from __future__ import annotations

import math
import warnings
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TypedDict, cast

from PIL import (  # pyright: ignore[reportMissingImports]
    Image,
    ImageChops,
    ImageFilter,
    ImageOps,
)

from opendocs._models import BBox
from opendocs.errors import (
    CorruptDocumentError,
    DocumentTypeMismatchError,
    LimitExceededError,
    UnsupportedDocumentError,
)
from opendocs.vision.base import (
    VisionElement,
    VisionResult,
    VisionTableElement,
    VisionTextElement,
)

MAX_WIDTH = 50_000
MAX_HEIGHT = 50_000
MAX_PIXELS = 40_000_000
MAX_MODEL_LONG_SIDE = 2_048
MAX_TILES = 32
TILE_OVERLAP_RATIO = 0.10
LONG_IMAGE_RATIO = 3.0
MIN_PROJECTED_SHORT_SIDE = 768
ULTRA_WIDE_RATIO = 4.0
_ALLOWED_FORMATS = frozenset({"PNG", "JPEG", "WEBP"})


class ImageFacts(TypedDict):
    alpha_coverage: float
    foreground_coverage: float
    components: int
    edge_density: float
    color_count: int
    nearly_blank: bool


class PreparedPart(TypedDict):
    name: str
    top: float
    bottom: float
    core_top: float
    core_bottom: float
    width: int
    height: int


class PreparedImage(TypedDict):
    skipped: bool
    reason: str | None
    width: int
    height: int
    parts: list[PreparedPart]
    facts: ImageFacts


_SUFFIX_FORMATS = {
    ".png": "PNG",
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".webp": "WEBP",
}


def _connected_components(mask: Image.Image) -> int:
    width, height = mask.size
    pixels = mask.load()
    if pixels is None:
        return 0
    seen: set[tuple[int, int]] = set()
    components = 0
    for y in range(height):
        for x in range(width):
            if not pixels[x, y] or (x, y) in seen:
                continue
            components += 1
            if components > 3:
                return components
            stack = [(x, y)]
            seen.add((x, y))
            while stack:
                current_x, current_y = stack.pop()
                for next_x, next_y in (
                    (current_x - 1, current_y),
                    (current_x + 1, current_y),
                    (current_x, current_y - 1),
                    (current_x, current_y + 1),
                ):
                    if not (0 <= next_x < width and 0 <= next_y < height):
                        continue
                    if (next_x, next_y) in seen or not pixels[next_x, next_y]:
                        continue
                    seen.add((next_x, next_y))
                    stack.append((next_x, next_y))
    return components


def _alpha_mask(rgba: Image.Image) -> Image.Image:
    alpha = rgba.getchannel("A")
    try:
        return alpha.point(lambda value: 255 if value > 16 else 0, mode="1")
    finally:
        alpha.close()


def _transparent_background(rgba: Image.Image) -> str:
    alpha = rgba.getchannel("A")
    try:
        foreground = alpha.point(lambda value: 255 if value > 16 else 0, mode="1")
    finally:
        alpha.close()
    try:
        if not foreground.getbbox():
            return "white"
        rgb = rgba.convert("RGB")
        try:
            grayscale = ImageOps.grayscale(rgb)
        finally:
            rgb.close()
        try:
            histogram = grayscale.histogram(mask=foreground)
        finally:
            grayscale.close()
        count = sum(histogram)
        mean = sum(value * amount for value, amount in enumerate(histogram)) / max(1, count)
        return "black" if mean >= 192 else "white"
    finally:
        foreground.close()


def _image_facts(image: Image.Image) -> ImageFacts:
    scale = min(1.0, 256 / max(image.width, image.height))
    sample_size = (
        max(1, round(image.width * scale)),
        max(1, round(image.height * scale)),
    )
    sample = (
        image.copy()
        if sample_size == image.size
        else image.resize(sample_size, Image.Resampling.LANCZOS)
    )
    try:
        rgba = sample.convert("RGBA")
        try:
            alpha_mask = _alpha_mask(rgba)
            try:
                alpha_values = alpha_mask.histogram()
                alpha_coverage = alpha_values[255] / max(1, sample.width * sample.height)
                alpha_components = _connected_components(alpha_mask)
            finally:
                alpha_mask.close()

            background_color = (
                _transparent_background(rgba) if _has_transparency(image) else "white"
            )
            background = Image.new("RGBA", rgba.size, background_color)
            try:
                background.alpha_composite(rgba)
                rgb = background.convert("RGB")
            finally:
                background.close()
        finally:
            rgba.close()
        try:
            reference = Image.new("RGB", rgb.size, background_color)
            try:
                difference = ImageChops.difference(rgb, reference).convert("L")
            finally:
                reference.close()
            foreground_mask = difference.point(
                lambda value: 255 if value > 32 else 0,
                mode="1",
            )
            foreground_values = foreground_mask.histogram()
            foreground_coverage = foreground_values[255] / max(
                1,
                sample.width * sample.height,
            )
            visual_components = _connected_components(foreground_mask)
            foreground_mask.close()

            gray = ImageOps.grayscale(rgb)
            extrema = cast(tuple[int, int], gray.getextrema())
            edges = gray.filter(ImageFilter.FIND_EDGES)
            edge_histogram = edges.histogram()
            edge_pixels = sum(edge_histogram[25:])
            edge_density = edge_pixels / max(1, sample.width * sample.height)
            colors = rgb.quantize(colors=16).getcolors(maxcolors=17) or []
            color_count = len(colors)
            components = alpha_components if alpha_coverage < 0.999 else visual_components
            nearly_blank = (
                max(alpha_coverage, foreground_coverage) <= 0.002 and edge_density <= 0.002
            ) or (
                alpha_coverage >= 0.999
                and foreground_coverage <= 0.002
                and extrema[0] >= 250
                and extrema[1] - extrema[0] <= 2
            )
            return {
                "alpha_coverage": alpha_coverage,
                "foreground_coverage": foreground_coverage,
                "components": components,
                "edge_density": edge_density,
                "color_count": color_count,
                "nearly_blank": nearly_blank,
            }
        finally:
            rgb.close()
    finally:
        sample.close()


def _validate_candidate(
    source_path: Path,
    original_name: str | None,
    *,
    max_width: int = MAX_WIDTH,
    max_height: int = MAX_HEIGHT,
    max_pixels: int = MAX_PIXELS,
) -> tuple[str, int, int]:
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(source_path) as candidate:
            detected_format = candidate.format
            width, height = candidate.size
            frames = getattr(candidate, "n_frames", 1)
            candidate.verify()
    if detected_format not in _ALLOWED_FORMATS:
        raise UnsupportedDocumentError("image format is not supported in this release")
    if original_name:
        declared = _SUFFIX_FORMATS.get(Path(original_name).suffix.lower())
        if declared is not None and declared != detected_format:
            raise DocumentTypeMismatchError(
                f"image extension declares {declared.lower()} but content is "
                f"{detected_format.lower()}"
            )
    if frames != 1:
        raise UnsupportedDocumentError("animated images are not supported in this release")
    if width <= 0 or height <= 0:
        raise CorruptDocumentError("image dimensions are invalid")
    if width > max_width or height > max_height or width * height > max_pixels:
        raise LimitExceededError("image dimensions exceed the safety budget")
    return detected_format, width, height


def _oriented_image(source_path: Path) -> Image.Image:
    opened = Image.open(source_path)
    try:
        opened.load()
        oriented = ImageOps.exif_transpose(opened)
    except BaseException:
        opened.close()
        raise
    if oriented is opened:
        return opened
    opened.close()
    return oriented


def _has_transparency(image: Image.Image) -> bool:
    return "A" in image.getbands() or "transparency" in image.info


def _flatten_image(image: Image.Image) -> Image.Image:
    if image.mode == "RGB" and not _has_transparency(image):
        image.info.clear()
        return image
    if not _has_transparency(image):
        clean = image.convert("RGB")
        clean.info.clear()
        return clean
    rgba = image if image.mode == "RGBA" else image.convert("RGBA")
    try:
        clean = Image.new("RGB", rgba.size, _transparent_background(rgba))
        clean.paste(rgba, (0, 0), rgba)
        clean.info.clear()
        return clean
    finally:
        if rgba is not image:
            rgba.close()


def _clean_image(source_path: Path) -> Image.Image:
    oriented = _oriented_image(source_path)
    clean = _flatten_image(oriented)
    if clean is not oriented:
        oriented.close()
    return clean


def _is_long_vertical(width: int, height: int) -> bool:
    scale = min(1.0, MAX_MODEL_LONG_SIDE / max(width, height))
    return height / width >= LONG_IMAGE_RATIO and width * scale < MIN_PROJECTED_SHORT_SIDE


def _tile_ranges(width: int, height: int) -> list[tuple[int, int, float, float]]:
    tile_height = min(height, max(MAX_MODEL_LONG_SIDE, width * 2))
    overlap = max(1, round(tile_height * TILE_OVERLAP_RATIO))
    step = tile_height - overlap
    estimated = max(1, math.ceil(max(0, height - overlap) / step))
    if estimated > MAX_TILES:
        raise LimitExceededError("long image exceeds the tile safety budget")
    ranges: list[tuple[int, int, float, float]] = []
    top = 0
    while top < height:
        bottom = min(height, top + tile_height)
        local_height = bottom - top
        core_top = 0.0 if top == 0 else min(1.0, (overlap / 2) / local_height)
        core_bottom = 1.0 if bottom == height else max(0.0, 1 - (overlap / 2) / local_height)
        ranges.append((top, bottom, core_top, core_bottom))
        if bottom == height:
            break
        top += step
    if len(ranges) > MAX_TILES:
        raise LimitExceededError("long image exceeds the tile safety budget")
    return ranges


def _coverage(facts: ImageFacts) -> float:
    foreground = facts.get("foreground_coverage", facts["alpha_coverage"])
    return min(facts["alpha_coverage"], foreground)


def _decorative_embedded(
    *,
    width: int,
    height: int,
    facts: ImageFacts,
    placement_area_ratio: float | None,
) -> bool:
    if placement_area_ratio is None or placement_area_ratio > 0.03:
        return False
    return (
        width <= 256
        and height <= 256
        and _coverage(facts) <= 0.20
        and facts["components"] <= 2
        and facts["color_count"] <= 16
        and facts["edge_density"] <= 0.25
    )


def crop_image(
    source_path: Path,
    output_path: Path,
    pixel_box: tuple[int, int, int, int],
) -> None:
    try:
        with Image.open(source_path) as opened:
            opened.load()
            cropped = opened.crop(pixel_box)
            try:
                rgba = cropped.convert("RGBA")
            finally:
                cropped.close()
        white = Image.new("RGBA", rgba.size, "white")
        white.alpha_composite(rgba)
        rgba.close()
        clean = white.convert("RGB")
        white.close()
        clean.info.clear()
        clean.save(output_path, format="PNG", optimize=False)
        clean.close()
    except (OSError, SyntaxError, TypeError, ValueError) as error:
        raise CorruptDocumentError("image crop could not be prepared") from error


def sanitize_image(
    source_path: Path,
    output_path: Path,
    original_name: str | None,
    *,
    max_width: int = MAX_WIDTH,
    max_height: int = MAX_HEIGHT,
    max_pixels: int = MAX_PIXELS,
) -> tuple[int, int]:
    """Preserve the original single-image sanitizer contract for internal callers."""
    try:
        _validate_candidate(
            source_path,
            original_name,
            max_width=max_width,
            max_height=max_height,
            max_pixels=max_pixels,
        )
        clean = _clean_image(source_path)
        try:
            clean.thumbnail(
                (MAX_MODEL_LONG_SIDE, MAX_MODEL_LONG_SIDE),
                Image.Resampling.LANCZOS,
            )
            clean.info.clear()
            final_size = clean.size
            clean.save(output_path, format="PNG", optimize=False)
            return final_size
        finally:
            clean.close()
    except (Image.DecompressionBombWarning, Image.DecompressionBombError) as error:
        raise LimitExceededError("image dimensions exceed the safety budget") from error
    except (DocumentTypeMismatchError, LimitExceededError, UnsupportedDocumentError):
        raise
    except (OSError, SyntaxError, ValueError) as error:
        raise CorruptDocumentError("image is corrupt or cannot be decoded") from error


def is_decorative_embedded(
    prepared: Mapping[str, object],
    placement_area_ratio: float | None,
    *,
    meaningful_alt_text: bool = False,
) -> bool:
    facts = prepared.get("facts")
    width = prepared.get("width")
    height = prepared.get("height")
    if meaningful_alt_text or not isinstance(facts, dict):
        return False
    if not isinstance(width, int) or not isinstance(height, int):
        return False
    typed_facts = cast(ImageFacts, facts)
    if placement_area_ratio is None:
        return (
            width <= 128
            and height <= 128
            and _coverage(typed_facts) <= 0.15
            and typed_facts["components"] <= 2
            and typed_facts["color_count"] <= 8
            and typed_facts["edge_density"] <= 0.20
        )
    return _decorative_embedded(
        width=width,
        height=height,
        facts=typed_facts,
        placement_area_ratio=placement_area_ratio,
    )


def prepare_image(
    source_path: Path,
    output_directory: Path,
    output_stem: str,
    original_name: str | None,
    context: str,
    placement_area_ratio: float | None = None,
) -> PreparedImage:
    """Validate and prepare one image. The return value is native-worker wire compatible."""
    if context not in {"standalone", "embedded", "hybrid_crop", "full_page"}:
        raise ValueError("image context is invalid")
    try:
        _, width, height = _validate_candidate(source_path, original_name)
        oriented = _oriented_image(source_path)
        width, height = oriented.size
        clean: Image.Image | None = None
        try:
            facts = _image_facts(oriented)
            clean = _flatten_image(oriented)
        finally:
            if clean is None or oriented is not clean:
                oriented.close()
        if clean is None:
            raise CorruptDocumentError("image could not be normalized")
        try:
            if context in {"embedded", "hybrid_crop"} and bool(facts["nearly_blank"]):
                return {
                    "skipped": True,
                    "reason": "nearly_blank",
                    "width": width,
                    "height": height,
                    "parts": [],
                    "facts": facts,
                }
            if context == "embedded" and _decorative_embedded(
                width=width,
                height=height,
                facts=facts,
                placement_area_ratio=placement_area_ratio,
            ):
                return {
                    "skipped": True,
                    "reason": "decorative_icon",
                    "width": width,
                    "height": height,
                    "parts": [],
                    "facts": facts,
                }

            ranges = (
                _tile_ranges(clean.width, clean.height)
                if _is_long_vertical(clean.width, clean.height)
                else [(0, clean.height, 0.0, 1.0)]
            )
            parts: list[PreparedPart] = []
            created: list[Path] = []
            try:
                for index, (top, bottom, core_top, core_bottom) in enumerate(ranges):
                    part = clean.crop((0, top, clean.width, bottom))
                    try:
                        part.thumbnail(
                            (MAX_MODEL_LONG_SIDE, MAX_MODEL_LONG_SIDE),
                            Image.Resampling.LANCZOS,
                        )
                        part.info.clear()
                        name = f"{output_stem}-{index}.png"
                        output_path = output_directory / name
                        created.append(output_path)
                        part.save(output_path, format="PNG", optimize=False)
                        parts.append(
                            {
                                "name": name,
                                "top": top / clean.height,
                                "bottom": bottom / clean.height,
                                "core_top": core_top,
                                "core_bottom": core_bottom,
                                "width": part.width,
                                "height": part.height,
                            }
                        )
                    finally:
                        part.close()
            except BaseException:
                for output_path in created:
                    output_path.unlink(missing_ok=True)
                raise
            return {
                "skipped": False,
                "reason": None,
                "width": width,
                "height": height,
                "parts": parts,
                "facts": facts,
            }
        finally:
            clean.close()
    except (Image.DecompressionBombWarning, Image.DecompressionBombError) as error:
        raise LimitExceededError("image dimensions exceed the safety budget") from error
    except (DocumentTypeMismatchError, LimitExceededError, UnsupportedDocumentError):
        raise
    except (OSError, SyntaxError, ValueError) as error:
        raise CorruptDocumentError("image is corrupt or cannot be decoded") from error


def tile_prompt(prompt: str, index: int, total: int) -> str:
    if total == 1:
        return prompt
    return (
        f"{prompt.rstrip()}\nThis is vertical tile {index + 1} of {total}. "
        "Overlap is context only. Return content once, in top-to-bottom order.\n"
    )


def _real(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{name} must be a real number")
    return float(value)


def prepared_paths(prepared: Mapping[str, object], directory: Path) -> tuple[Path, ...]:
    raw_parts = prepared.get("parts")
    if not isinstance(raw_parts, list):
        raise TypeError("prepared image parts must be a list")
    if len(raw_parts) > MAX_TILES:
        raise ValueError("prepared image exceeds the tile safety budget")
    if not bool(prepared.get("skipped")) and not raw_parts:
        raise ValueError("prepared image must contain at least one part")
    root = directory.resolve()
    paths: list[Path] = []
    for part in raw_parts:
        if not isinstance(part, dict):
            raise TypeError("prepared image part is invalid")
        name = part.get("name")
        if not isinstance(name, str):
            raise TypeError("prepared image part is invalid")
        candidate = Path(name)
        if candidate.is_absolute() or candidate.name != name or name in {"", ".", ".."}:
            raise ValueError("prepared image part name must be a basename")
        path = directory / candidate
        if path.resolve().parent != root:
            raise ValueError("prepared image part escapes the workspace")
        width = part.get("width")
        height = part.get("height")
        if (
            isinstance(width, bool)
            or not isinstance(width, int)
            or width <= 0
            or isinstance(height, bool)
            or not isinstance(height, int)
            or height <= 0
        ):
            raise TypeError("prepared image part dimensions are invalid")
        top = _real(part.get("top"), "top")
        bottom = _real(part.get("bottom"), "bottom")
        core_top = _real(part.get("core_top"), "core_top")
        core_bottom = _real(part.get("core_bottom"), "core_bottom")
        if not (0 <= top < bottom <= 1 and 0 <= core_top < core_bottom <= 1):
            raise ValueError("prepared image part geometry is invalid")
        paths.append(path)
    return tuple(paths)


def _mapped_element(element: VisionElement, part: Mapping[str, object]) -> VisionElement | None:
    bbox = element.bbox
    if bbox is None:
        return element
    core_top = _real(part.get("core_top"), "core_top")
    core_bottom = _real(part.get("core_bottom"), "core_bottom")
    center = (bbox.top + bbox.bottom) / 2
    if not core_top <= center <= core_bottom:
        return None
    top = _real(part.get("top"), "top")
    bottom = _real(part.get("bottom"), "bottom")
    mapped = BBox(
        bbox.left,
        top + bbox.top * (bottom - top),
        bbox.right,
        top + bbox.bottom * (bottom - top),
    ).require_normalized("tiled image bbox")
    if isinstance(element, VisionTableElement):
        return VisionTableElement(element.grid, element.header_rows, element.source_index, mapped)
    return VisionTextElement(element.text, element.source_index, mapped)


def _trim_adjacent_text_overlap(previous: str, current: str) -> str:
    previous_lines = [" ".join(line.split()) for line in previous.splitlines() if line.strip()]
    current_lines = [" ".join(line.split()) for line in current.splitlines() if line.strip()]
    limit = min(10, len(previous_lines), len(current_lines))
    for size in range(limit, 0, -1):
        if previous_lines[-size:] == current_lines[:size]:
            remaining = current_lines[size:]
            return "\n".join(remaining)
    return current


def merge_tiled_results(
    prepared: Mapping[str, object],
    results: Sequence[VisionResult | None],
) -> VisionResult:
    raw_parts = prepared.get("parts")
    if not isinstance(raw_parts, list) or len(raw_parts) != len(results):
        raise ValueError("tile results do not match prepared image parts")
    merged: list[VisionElement] = []
    previous_tile_index: int | None = None
    previous_boundary_text: str | None = None
    for tile_index, (part, result) in enumerate(zip(raw_parts, results, strict=True)):
        if not isinstance(part, dict):
            raise TypeError("prepared image part is invalid")
        if result is None:
            previous_tile_index = None
            previous_boundary_text = None
            continue
        tile_elements: list[VisionElement] = []
        for element in result.elements:
            mapped = _mapped_element(element, cast(dict[str, object], part))
            if mapped is not None:
                tile_elements.append(mapped)
        if (
            previous_tile_index is not None
            and tile_index == previous_tile_index + 1
            and previous_boundary_text is not None
            and tile_elements
            and isinstance(tile_elements[0], VisionTextElement)
            and tile_elements[0].bbox is None
        ):
            first = tile_elements[0]
            trimmed = _trim_adjacent_text_overlap(previous_boundary_text, first.text)
            if trimmed.strip():
                if trimmed != first.text:
                    tile_elements[0] = VisionTextElement(trimmed, first.source_index, None)
            else:
                tile_elements.pop(0)
        merged.extend(tile_elements)
        previous_tile_index = tile_index
        previous_boundary_text = next(
            (
                element.text
                for element in reversed(tile_elements)
                if isinstance(element, VisionTextElement) and element.bbox is None
            ),
            None,
        )
    ordered: list[VisionElement] = []
    for source_index, element in enumerate(merged):
        if isinstance(element, VisionTableElement):
            ordered.append(
                VisionTableElement(element.grid, element.header_rows, source_index, element.bbox)
            )
        else:
            ordered.append(VisionTextElement(element.text, source_index, element.bbox))
    return VisionResult(tuple(ordered))


def map_result_to_bbox(result: VisionResult, outer: BBox) -> VisionResult:
    mapped: list[VisionElement] = []
    for element in result.elements:
        if element.bbox is None:
            raise ValueError("mapped vision elements require bboxes")
        bbox = BBox(
            outer.left + element.bbox.left * (outer.right - outer.left),
            outer.top + element.bbox.top * (outer.bottom - outer.top),
            outer.left + element.bbox.right * (outer.right - outer.left),
            outer.top + element.bbox.bottom * (outer.bottom - outer.top),
        ).require_normalized("mapped image bbox")
        if isinstance(element, VisionTableElement):
            mapped.append(
                VisionTableElement(element.grid, element.header_rows, element.source_index, bbox)
            )
        else:
            mapped.append(VisionTextElement(element.text, element.source_index, bbox))
    return VisionResult(tuple(mapped))
