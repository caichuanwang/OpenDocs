import json
from dataclasses import replace
from pathlib import Path

import pytest

from benchmarks.document_parsing.evaluate_quality import (
    QualityObservation,
    evaluate_quality,
)
from benchmarks.document_parsing.render_evidence import (
    EvidenceError,
    render_release_evidence,
    validate_safe_quality_record,
)
from benchmarks.document_parsing.run_quality import (
    RunConfigurationError,
    freeze_tuning_record,
    redact_log_message,
    validate_cached_record,
    validate_holdout_freeze,
    validate_workspace,
)
from benchmarks.document_parsing.schema import RunIdentity, load_policy

ROOT = Path(__file__).resolve().parents[1]
POLICY = load_policy(ROOT / "benchmarks/document_parsing/policy-v0.1.json")
IDENTITY = RunIdentity(
    candidate_commit="a" * 40,
    package_version="0.1.0",
    policy_digest=POLICY.digest,
    manifest_digest="b" * 64,
    evaluator_version="0.1",
    model_identity="controlled-replay",
    environment_identity="ubuntu-python-3.13",
    replay_identity="fixture-v1",
)


def _passing_record(split: str) -> dict[str, object]:
    observations = [
        QualityObservation(
            item_id=f"{category}-{metric.name}",
            category=category,
            metric=metric.name,
            numerator=0 if metric.maximum is not None else 10,
            denominator=10,
        )
        for category in POLICY.categories
        for metric in POLICY.metrics
    ]
    return evaluate_quality(
        observations,
        policy=POLICY,
        identity=IDENTITY,
        split=split,
        split_counts={category: 5 for category in POLICY.categories},
    ).to_safe_dict()


def test_tuning_must_pass_before_policy_can_be_frozen(tmp_path: Path) -> None:
    failing = _passing_record("tuning") | {"passed": False}

    with pytest.raises(RunConfigurationError, match="passing tuning"):
        freeze_tuning_record(failing, tmp_path / "freeze.json")


def test_holdout_requires_a_matching_frozen_identity(tmp_path: Path) -> None:
    freeze_path = tmp_path / "freeze.json"
    freeze = freeze_tuning_record(_passing_record("tuning"), freeze_path)

    validate_holdout_freeze(freeze, IDENTITY)
    with pytest.raises(RunConfigurationError, match="candidate_commit"):
        validate_holdout_freeze(
            freeze,
            replace(IDENTITY, candidate_commit="c" * 40),
        )


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("candidate_commit", "c" * 40),
        ("package_version", "0.1.1"),
        ("policy_digest", "d" * 64),
        ("manifest_digest", "e" * 64),
        ("evaluator_version", "0.2"),
        ("model_identity", "different-model"),
        ("environment_identity", "macos-python-3.13"),
        ("replay_identity", "fixture-v2"),
    ],
)
def test_changed_run_identity_invalidates_cached_evidence(
    field: str,
    replacement: str,
) -> None:
    record = _passing_record("tuning")
    changed = replace(IDENTITY, **{field: replacement})

    with pytest.raises(RunConfigurationError, match=field):
        validate_cached_record(record, changed, expected_split="tuning")


def test_workspace_must_be_below_the_ignored_run_root(tmp_path: Path) -> None:
    accepted = ROOT / "benchmarks/document_parsing/runs/candidate-001"

    assert validate_workspace(accepted) == accepted.resolve()
    with pytest.raises(RunConfigurationError, match="ignored"):
        validate_workspace(tmp_path / "raw-output")


def test_failed_or_incomplete_records_cannot_render_as_passing() -> None:
    failed = _passing_record("holdout") | {"passed": False}
    incomplete = _passing_record("holdout")
    incomplete.pop("metrics")

    with pytest.raises(EvidenceError):
        render_release_evidence([_passing_record("tuning"), failed])
    with pytest.raises(EvidenceError):
        validate_safe_quality_record(incomplete)


@pytest.mark.parametrize(
    "unsafe_field",
    [
        "source_filename",
        "local_path",
        "extracted_text",
        "annotation",
        "prompt",
        "provider_payload",
        "raw_markdown",
        "api_key",
    ],
)
def test_public_evidence_rejects_content_bearing_fields(unsafe_field: str) -> None:
    record = _passing_record("tuning")
    record[unsafe_field] = "private"

    with pytest.raises(EvidenceError, match=unsafe_field):
        validate_safe_quality_record(record)


def test_public_evidence_contains_only_safe_aggregates() -> None:
    rendered = render_release_evidence([_passing_record("tuning"), _passing_record("holdout")])

    assert "# OpenDocs v0.1.0 发布证据" in rendered
    assert f"Candidate commit: {IDENTITY.candidate_commit}" in rendered
    assert IDENTITY.candidate_commit in rendered
    assert "tuning" in rendered
    assert "holdout" in rendered
    assert "private" not in rendered


def test_logs_redact_paths_and_secret_like_values(tmp_path: Path) -> None:
    message = (
        f"failed {tmp_path}/private.docx api_key=sk-private provider_payload=secret prompt=private"
    )

    redacted = redact_log_message(message)

    assert str(tmp_path) not in redacted
    assert "sk-private" not in redacted
    assert "provider_payload=secret" not in redacted
    assert "prompt=private" not in redacted
    assert "[REDACTED]" in redacted


def test_freeze_record_is_machine_readable_and_contains_no_raw_result(
    tmp_path: Path,
) -> None:
    freeze_path = tmp_path / "freeze.json"

    freeze_tuning_record(_passing_record("tuning"), freeze_path)
    saved = json.loads(freeze_path.read_text(encoding="utf-8"))

    assert saved["status"] == "frozen"
    assert saved["tuning_result_digest"]
    assert "metrics" not in saved
