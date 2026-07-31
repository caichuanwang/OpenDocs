from __future__ import annotations

import argparse
import json
import re
from dataclasses import fields
from pathlib import Path
from typing import cast

from benchmarks.document_parsing.evaluate_quality import (
    EVALUATOR_VERSION,
    QualityObservation,
    evaluate_quality,
)
from benchmarks.document_parsing.render_evidence import validate_safe_quality_record
from benchmarks.document_parsing.schema import (
    BenchmarkManifest,
    BenchmarkPolicy,
    RunIdentity,
    canonical_digest,
    load_manifest,
    load_policy,
    sha256_file,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
IGNORED_RUN_ROOT = REPOSITORY_ROOT / "benchmarks/document_parsing/runs"


class RunConfigurationError(ValueError):
    """Raised when a private quality run is unsafe, stale, or incomplete."""


def validate_workspace(path: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(IGNORED_RUN_ROOT.resolve())
    except ValueError as error:
        raise RunConfigurationError(
            "raw benchmark workspace must be below the ignored runs directory"
        ) from error
    return resolved


def validate_cached_record(
    record: object,
    identity: RunIdentity,
    *,
    expected_split: str,
) -> dict[str, object]:
    validated = validate_safe_quality_record(record)
    if validated["split"] != expected_split:
        raise RunConfigurationError(f"cached record split does not match {expected_split!r}")
    for field in fields(RunIdentity):
        expected = getattr(identity, field.name)
        if validated[field.name] != expected:
            raise RunConfigurationError(f"cached evidence does not match {field.name}")
    return validated


def freeze_tuning_record(
    record: object,
    output_path: Path,
) -> dict[str, object]:
    validated = validate_safe_quality_record(record)
    if validated["split"] != "tuning" or validated["passed"] is not True:
        raise RunConfigurationError("policy freeze requires a passing tuning record")
    identity = {field.name: validated[field.name] for field in fields(RunIdentity)}
    freeze = {
        "schema_version": 1,
        "status": "frozen",
        "identity": identity,
        "tuning_result_digest": canonical_digest(validated),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(freeze, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return freeze


def validate_holdout_freeze(
    value: object,
    identity: RunIdentity,
) -> dict[str, object]:
    freeze = _object(value, "freeze record")
    expected_fields = {
        "schema_version",
        "status",
        "identity",
        "tuning_result_digest",
    }
    if set(freeze) != expected_fields:
        raise RunConfigurationError("freeze record fields are incomplete or unknown")
    if freeze["schema_version"] != 1 or freeze["status"] != "frozen":
        raise RunConfigurationError("holdout requires a frozen policy record")
    frozen_identity = _object(freeze["identity"], "freeze identity")
    expected_identity_fields = {field.name for field in fields(RunIdentity)}
    if set(frozen_identity) != expected_identity_fields:
        raise RunConfigurationError("freeze identity fields are incomplete or unknown")
    for field in fields(RunIdentity):
        if frozen_identity[field.name] != getattr(identity, field.name):
            raise RunConfigurationError(f"holdout freeze does not match {field.name}")
    digest = freeze["tuning_result_digest"]
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise RunConfigurationError("freeze tuning result digest is invalid")
    return freeze


def redact_log_message(message: str) -> str:
    redacted = re.sub(
        r"(?i)\b(api_key|token|provider_payload|prompt)=\S+",
        r"\1=[REDACTED]",
        message,
    )
    return re.sub(r"(?<![\w.])/(?:[^\s]+)", "[REDACTED]", redacted)


def load_and_validate_manifest(
    policy_path: Path,
    manifest_path: Path,
) -> tuple[BenchmarkPolicy, BenchmarkManifest]:
    policy = load_policy(policy_path)
    manifest = load_manifest(manifest_path, policy)
    _verify_manifest_files(manifest, manifest_path.parent)
    return policy, manifest


def _verify_manifest_files(
    manifest: BenchmarkManifest,
    base_directory: Path,
) -> None:
    verified_sources: set[tuple[str, str]] = set()
    for item in manifest.items:
        source_path = _resolve_private_path(item.source.path, base_directory)
        source_key = (str(source_path), item.source.sha256)
        if source_key not in verified_sources:
            if sha256_file(source_path) != item.source.sha256:
                raise RunConfigurationError(
                    f"source hash mismatch for opaque item {item.item_id!r}"
                )
            verified_sources.add(source_key)
        annotation_path = _resolve_private_path(item.annotation.ref, base_directory)
        if sha256_file(annotation_path) != item.annotation.sha256:
            raise RunConfigurationError(
                f"annotation hash mismatch for opaque item {item.item_id!r}"
            )


def _resolve_private_path(value: str, base_directory: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else base_directory / path


def _load_observations(path: Path) -> list[QualityObservation]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RunConfigurationError("cannot load structured quality observations") from error
    data = _object(value, "observations file")
    if set(data) != {"schema_version", "observations"} or data["schema_version"] != 1:
        raise RunConfigurationError("observations file schema is invalid")
    values = data["observations"]
    if not isinstance(values, list):
        raise RunConfigurationError("observations must be an array")
    observations: list[QualityObservation] = []
    expected = {
        "item_id",
        "category",
        "metric",
        "numerator",
        "denominator",
        "unresolved",
    }
    for index, item_value in enumerate(values):
        item = _object(item_value, f"observation {index}")
        if set(item) != expected:
            raise RunConfigurationError(f"observation {index} fields are invalid")
        try:
            observations.append(
                QualityObservation(
                    item_id=cast(str, item["item_id"]),
                    category=cast(str, item["category"]),
                    metric=cast(str, item["metric"]),
                    numerator=cast(int, item["numerator"]),
                    denominator=cast(int, item["denominator"]),
                    unresolved=cast(bool, item["unresolved"]),
                )
            )
        except (TypeError, ValueError) as error:
            raise RunConfigurationError(f"observation {index} values are invalid") from error
    return observations


def _object(value: object, where: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise RunConfigurationError(f"{where} must be an object")
    return cast(dict[str, object], value)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run private M3 quality gates without publishing raw content."
    )
    parser.add_argument("--mode", choices=("tuning", "freeze", "holdout"), required=True)
    parser.add_argument(
        "--policy",
        type=Path,
        default=Path(__file__).with_name("policy-v0.1.json"),
    )
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--observations", type=Path)
    parser.add_argument("--tuning-result", type=Path)
    parser.add_argument("--freeze-record", type=Path)
    parser.add_argument("--candidate-commit")
    parser.add_argument("--package-version", default="0.1.0")
    parser.add_argument("--model-identity")
    parser.add_argument("--environment-identity")
    parser.add_argument("--replay-identity")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.mode == "freeze":
        if args.tuning_result is None or args.freeze_record is None:
            raise RunConfigurationError("freeze mode requires --tuning-result and --freeze-record")
        record = json.loads(args.tuning_result.read_text(encoding="utf-8"))
        freeze_tuning_record(record, args.freeze_record)
        return 0

    required = {
        "--manifest": args.manifest,
        "--workspace": args.workspace,
        "--observations": args.observations,
        "--candidate-commit": args.candidate_commit,
        "--model-identity": args.model_identity,
        "--environment-identity": args.environment_identity,
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        raise RunConfigurationError(f"missing required arguments: {', '.join(missing)}")

    workspace = validate_workspace(cast(Path, args.workspace))
    policy, manifest = load_and_validate_manifest(
        args.policy,
        cast(Path, args.manifest),
    )
    identity = RunIdentity(
        candidate_commit=cast(str, args.candidate_commit),
        package_version=args.package_version,
        policy_digest=policy.digest,
        manifest_digest=manifest.digest,
        evaluator_version=EVALUATOR_VERSION,
        model_identity=cast(str, args.model_identity),
        environment_identity=cast(str, args.environment_identity),
        replay_identity=args.replay_identity,
    )
    if args.mode == "holdout":
        if args.freeze_record is None:
            raise RunConfigurationError("holdout mode requires --freeze-record")
        freeze = json.loads(args.freeze_record.read_text(encoding="utf-8"))
        validate_holdout_freeze(freeze, identity)

    observations = _load_observations(cast(Path, args.observations))
    selected_items = manifest.for_split(args.mode)
    selected_ids = {item.item_id for item in selected_items}
    if any(observation.item_id not in selected_ids for observation in observations):
        raise RunConfigurationError("observations contain an item outside the selected split")
    split_counts = {
        category: sum(item.category == category for item in selected_items)
        for category in policy.categories
    }
    report = evaluate_quality(
        observations,
        policy=policy,
        identity=identity,
        split=args.mode,
        split_counts=split_counts,
    )
    workspace.mkdir(parents=True, exist_ok=True)
    output_path = workspace / f"safe-quality-{args.mode}.json"
    output_path.write_text(
        json.dumps(report.to_safe_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
