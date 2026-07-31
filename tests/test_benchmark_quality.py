import json
from dataclasses import replace
from pathlib import Path

import pytest

from benchmarks.document_parsing.evaluate_quality import (
    QualityObservation,
    evaluate_quality,
    render_quality_summary,
)
from benchmarks.document_parsing.office_quality import (
    OfficeChecklist,
    evaluate_office_determinism,
    evaluate_office_structure,
)
from benchmarks.document_parsing.schema import (
    BenchmarkPolicy,
    RunIdentity,
    load_policy,
)

ROOT = Path(__file__).resolve().parents[1]
POLICY = load_policy(ROOT / "benchmarks/document_parsing/policy-v0.1.json")
IDENTITY = RunIdentity(
    candidate_commit="a" * 40,
    package_version="0.1.0",
    policy_digest=POLICY.digest,
    manifest_digest="b" * 64,
    evaluator_version="0.1",
    model_identity="controlled-replay",
    environment_identity="synthetic-test",
    replay_identity="fixture-v1",
)


def _observations_for_all_metrics(
    *,
    numerator: int = 10,
    denominator: int = 10,
) -> list[QualityObservation]:
    return [
        QualityObservation(
            item_id=f"{category}-{metric.name}",
            category=category,
            metric=metric.name,
            numerator=0 if metric.maximum is not None else numerator,
            denominator=denominator,
        )
        for category in POLICY.categories
        for metric in POLICY.metrics
    ]


def _checklist() -> OfficeChecklist:
    return OfficeChecklist(
        format="pptx",
        ordered_anchors=("Alpha", "Omega"),
        required_boundaries=("<!-- page:1 -->", "<!-- slide:2 -->"),
        minimum_table_count=1,
        required_visual_slots=("<!-- visual:1 -->",),
        allowed_warnings=("expected warning",),
    )


def _valid_office_markdown(prose: str = "provider prose") -> str:
    return "\n".join(
        (
            "# Alpha",
            "<!-- page:1 -->",
            "| A | B |",
            "| --- | --- |",
            "| 1 | 2 |",
            prose,
            "<!-- visual:1 -->",
            "<!-- slide:2 -->",
            "Omega",
        )
    )


def test_metric_math_covers_all_accepted_quality_measures() -> None:
    observations = [
        QualityObservation("1", "native_text", "native_text_accuracy", 9, 10),
        QualityObservation("2", "native_text", "scanned_text_accuracy", 8, 10),
        QualityObservation("3", "native_text", "native_table_cell_accuracy", 7, 10),
        QualityObservation("4", "native_text", "visual_table_cell_accuracy", 6, 10),
        QualityObservation("5", "native_text", "visual_content_recall", 5, 10),
        QualityObservation("6", "native_text", "visual_content_precision", 4, 10),
        QualityObservation("7", "native_text", "native_visual_call_rate", 3, 10),
    ]

    report = evaluate_quality(
        observations,
        policy=POLICY,
        identity=IDENTITY,
        split="tuning",
        split_counts={"native_text": 7},
    )
    values = {metric.name: metric.value for metric in report.metrics}

    assert values == {
        "native_text_accuracy": 0.9,
        "scanned_text_accuracy": 0.8,
        "native_table_cell_accuracy": 0.7,
        "visual_table_cell_accuracy": 0.6,
        "visual_content_recall": 0.5,
        "visual_content_precision": 0.4,
        "native_visual_call_rate": 0.3,
    }


def test_category_failure_cannot_hide_behind_passing_aggregate() -> None:
    observations = _observations_for_all_metrics()
    observations.extend(
        (
            QualityObservation(
                "large-pass",
                "native_text",
                "native_text_accuracy",
                99,
                99,
            ),
            QualityObservation(
                "small-fail",
                "scanned_text",
                "native_text_accuracy",
                0,
                1,
            ),
        )
    )

    report = evaluate_quality(
        observations,
        policy=POLICY,
        identity=IDENTITY,
        split="holdout",
        split_counts={category: 5 for category in POLICY.categories},
    )
    metric = next(item for item in report.metrics if item.name == "native_text_accuracy")

    assert metric.value is not None
    assert metric.value > 0.95
    assert metric.passed is False
    assert report.passed is False


def test_unresolved_results_are_disclosed_and_follow_frozen_policy() -> None:
    observations = _observations_for_all_metrics()
    observations.append(
        QualityObservation(
            "unresolved",
            "native_text",
            "native_text_accuracy",
            0,
            0,
            unresolved=True,
        )
    )

    failing = evaluate_quality(
        observations,
        policy=POLICY,
        identity=IDENTITY,
        split="holdout",
        split_counts={category: 5 for category in POLICY.categories},
    )
    disclosed_policy: BenchmarkPolicy = replace(POLICY, unresolved_handling="disclose")
    disclosed = evaluate_quality(
        observations,
        policy=disclosed_policy,
        identity=IDENTITY,
        split="tuning",
        split_counts={category: 5 for category in POLICY.categories},
    )

    failing_metric = next(
        metric for metric in failing.metrics if metric.name == "native_text_accuracy"
    )
    assert failing_metric.unresolved_count == 1
    assert failing_metric.passed is False
    assert disclosed.passed is True


def test_thresholds_come_only_from_the_bound_policy() -> None:
    observations = _observations_for_all_metrics(numerator=9, denominator=10)

    report = evaluate_quality(
        observations,
        policy=POLICY,
        identity=IDENTITY,
        split="holdout",
        split_counts={category: 5 for category in POLICY.categories},
    )
    native_text = next(metric for metric in report.metrics if metric.name == "native_text_accuracy")

    assert native_text.minimum == POLICY.threshold("native_text_accuracy").minimum
    assert native_text.passed is False


def test_quality_report_is_privacy_safe_and_machine_readable() -> None:
    report = evaluate_quality(
        _observations_for_all_metrics(),
        policy=POLICY,
        identity=IDENTITY,
        split="tuning",
        split_counts={category: 5 for category in POLICY.categories},
    )

    serialized = json.dumps(report.to_safe_dict(), sort_keys=True)
    summary = render_quality_summary(report)

    assert report.passed is True
    assert "source_filename" not in serialized
    assert "extracted_text" not in serialized
    assert "annotation" not in serialized
    assert "provider_payload" not in serialized
    assert "raw_markdown" not in serialized
    assert "native_text-native_text_accuracy" not in serialized
    assert "PASS" in summary


@pytest.mark.parametrize(
    ("markdown", "warnings", "failed_check"),
    [
        (_valid_office_markdown().replace("Omega", ""), (), "anchors_present"),
        (_valid_office_markdown().replace("# Alpha", "# Omega"), (), "anchor_order"),
        (
            _valid_office_markdown().replace("<!-- slide:2 -->", ""),
            (),
            "boundaries",
        ),
        (
            _valid_office_markdown().replace("| --- | --- |", "not a delimiter"),
            (),
            "tables",
        ),
        (
            _valid_office_markdown().replace("<!-- visual:1 -->", ""),
            (),
            "visual_slots",
        ),
        (_valid_office_markdown(), ("unexpected",), "warnings"),
    ],
)
def test_office_structure_detects_each_accepted_failure(
    markdown: str,
    warnings: tuple[str, ...],
    failed_check: str,
) -> None:
    result = evaluate_office_structure(markdown, warnings, _checklist(), mode="replay")

    assert result.passed is False
    assert result.checks[failed_check] is False
    assert "Alpha" not in json.dumps(result.to_safe_dict())


def test_office_determinism_compares_markdown_and_normalized_warning_sequences() -> None:
    markdown = _valid_office_markdown()

    assert evaluate_office_determinism(
        (markdown, markdown),
        (("  expected   warning ",), ("expected warning",)),
    )
    assert not evaluate_office_determinism(
        (markdown, f"{markdown}\nchanged"),
        (("expected warning",), ("expected warning",)),
    )
    assert not evaluate_office_determinism(
        (markdown, markdown),
        (("first", "second"), ("second", "first")),
    )


def test_live_office_checks_structure_without_snapshotting_provider_prose() -> None:
    first = evaluate_office_structure(
        _valid_office_markdown("first provider description"),
        ("expected warning",),
        _checklist(),
        mode="live",
    )
    second = evaluate_office_structure(
        _valid_office_markdown("different provider wording"),
        ("expected warning",),
        _checklist(),
        mode="live",
    )

    assert first.passed is True
    assert second.passed is True
    assert first.to_safe_dict() == second.to_safe_dict()
