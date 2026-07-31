"""Independent OpenDocs consumer using only the public package contract."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from urllib.parse import urlsplit

from opendocs import ParseOptions, VisionConfig, aparse, parse

_REMOTE_SCHEMES = frozenset({"http", "https", "oss", "s3"})


def require_local_path(value: str | Path) -> Path:
    raw = str(value)
    if urlsplit(raw).scheme.lower() in _REMOTE_SCHEMES:
        raise ValueError("OpenDocs examples accept local paths only")
    path = Path(value)
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def parse_sync(
    source: str | Path,
    output_directory: Path,
    *,
    options: ParseOptions,
    vision: VisionConfig | None,
) -> Path:
    source_path = require_local_path(source)
    output_directory.mkdir(parents=True, exist_ok=True)
    output_path = output_directory / f"{source_path.stem}.md"
    output_path.write_text(
        parse(source_path, options=options, vision=vision),
        encoding="utf-8",
    )
    return output_path


async def parse_many(
    sources: list[Path],
    output_directory: Path,
    *,
    document_concurrency: int,
    options: ParseOptions,
    vision: VisionConfig | None,
) -> list[Path]:
    if (
        isinstance(document_concurrency, bool)
        or not isinstance(document_concurrency, int)
        or document_concurrency <= 0
    ):
        raise ValueError("document_concurrency must be a positive integer")
    source_paths = [require_local_path(source) for source in sources]
    output_paths = [output_directory / f"{source.stem}.md" for source in source_paths]
    if len(output_paths) != len(set(output_paths)):
        raise ValueError("input paths must produce unique output filenames")

    output_directory.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(document_concurrency)

    async def parse_one(source: Path, output: Path) -> Path:
        async with semaphore:
            markdown = await aparse(source, options=options, vision=vision)
        output.write_text(markdown, encoding="utf-8")
        return output

    return list(
        await asyncio.gather(
            *(
                parse_one(source, output)
                for source, output in zip(source_paths, output_paths, strict=True)
            )
        )
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Parse local documents into Markdown.")
    parser.add_argument("sources", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--async", dest="async_mode", action="store_true")
    parser.add_argument("--document-concurrency", type=int, default=2)
    parser.add_argument("--vision-concurrency", type=int, default=4)
    parser.add_argument("--vision-model")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    options = ParseOptions(vision_concurrency=args.vision_concurrency)
    vision = VisionConfig(model=args.vision_model) if args.vision_model else None
    if args.async_mode:
        asyncio.run(
            parse_many(
                args.sources,
                args.output,
                document_concurrency=args.document_concurrency,
                options=options,
                vision=vision,
            )
        )
    else:
        if len(args.sources) != 1:
            raise ValueError("sync mode accepts exactly one source")
        parse_sync(
            args.sources[0],
            args.output,
            options=options,
            vision=vision,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
