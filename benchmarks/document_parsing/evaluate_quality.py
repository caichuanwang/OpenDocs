from __future__ import annotations

from dataclasses import dataclass

from benchmarks.document_parsing.schema import (
    SUPPORTED_SPLITS,
    BenchmarkPolicy,
    MetricThreshold,
    RunIdentity,
)

EVALUATOR_VERSION = "0.1"


@dataclass(frozen=True, slots=True)
class QualityObservation:
    item_id: str
    category: str
    metric: str
    numerator: int
    denominator: int
    unresolved: bool = False


@dataclass(frozen=True, slots=True)
class CategoryMetricResult:
    category: str
    value: float | None
    unresolved_count: int
    passed: bool

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "category": self.category,
            "value": self.value,
            "unresolved_count": self.unresolved_count,
            "passed": self.passed,
        }


@dataclass(frozen=True, slots=True)
class MetricResult:
    name: str
    value: float | None
    minimum: float | None
    maximum: float | None
    unresolved_count: int
    categories: tuple[CategoryMetricResult, ...]
    passed: bool

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "value": self.value,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "unresolved_count": self.unresolved_count,
            "categories": [category.to_safe_dict() for category in self.categories],
            "passed": self.passed,
        }


@dataclass(frozen=True, slots=True)
class QualityReport:
    policy_version: str
    split: str
    split_counts: tuple[tuple[str, int], ...]
    identity: RunIdentity
    metrics: tuple[MetricResult, ...]
    passed: bool

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "evaluator_version": EVALUATOR_VERSION,
            "candidate_commit": self.identity.candidate_commit,
            "package_version": self.identity.package_version,
            "policy_version": self.policy_version,
            "policy_digest": self.identity.policy_digest,
            "manifest_digest": self.identity.manifest_digest,
            "split": self.split,
            "split_counts": dict(self.split_counts),
            "model_identity": self.identity.model_identity,
            "environment_identity": self.identity.environment_identity,
            "replay_identity": self.identity.replay_identity,
            "metrics": [metric.to_safe_dict() for metric in self.metrics],
            "passed": self.passed,
        }


def evaluate_quality(
    observations: list[QualityObservation],
    *,
    policy: BenchmarkPolicy,
    identity: RunIdentity,
    split: str,
    split_counts: dict[str, int],
) -> QualityReport:
    if split not in SUPPORTED_SPLITS:
        raise ValueError(f"unsupported split {split!r}")
    if identity.policy_digest != policy.digest:
        raise ValueError("run identity does not match the bound policy")
    _validate_observations(observations, policy)
    if set(split_counts) - set(policy.categories):
        raise ValueError("split counts contain an unknown category")
    if any(isinstance(count, bool) or count < 0 for count in split_counts.values()):
        raise ValueError("split counts must be non-negative integers")

    metrics = tuple(
        _evaluate_metric(observations, policy, threshold) for threshold in policy.metrics
    )
    return QualityReport(
        policy_version=policy.policy_version,
        split=split,
        split_counts=tuple(sorted(split_counts.items())),
        identity=identity,
        metrics=metrics,
        passed=all(metric.passed for metric in metrics),
    )


def render_quality_summary(report: QualityReport) -> str:
    status = "PASS" if report.passed else "FAIL"
    lines = [
        f"M3 document quality: {status}",
        f"Policy: {report.policy_version} ({report.identity.policy_digest})",
        f"Candidate: {report.identity.candidate_commit}",
        f"Split: {report.split}",
    ]
    for metric in report.metrics:
        value = "unresolved" if metric.value is None else f"{metric.value:.4f}"
        lines.append(
            f"- {metric.name}: {value}; unresolved={metric.unresolved_count}; "
            f"{'PASS' if metric.passed else 'FAIL'}"
        )
    return "\n".join(lines)


def _evaluate_metric(
    observations: list[QualityObservation],
    policy: BenchmarkPolicy,
    threshold: MetricThreshold,
) -> MetricResult:
    relevant = [item for item in observations if item.metric == threshold.name]
    categories = tuple(
        _evaluate_category(relevant, category, threshold, policy) for category in policy.categories
    )
    numerator, denominator, unresolved_count = _totals(relevant)
    value = _ratio(numerator, denominator)
    threshold_passed = _passes_threshold(value, threshold)
    unresolved_passed = unresolved_count == 0 or policy.unresolved_handling == "disclose"
    passed = (
        threshold_passed and unresolved_passed and all(category.passed for category in categories)
    )
    return MetricResult(
        name=threshold.name,
        value=value,
        minimum=threshold.minimum,
        maximum=threshold.maximum,
        unresolved_count=unresolved_count,
        categories=categories,
        passed=passed,
    )


def _evaluate_category(
    observations: list[QualityObservation],
    category: str,
    threshold: MetricThreshold,
    policy: BenchmarkPolicy,
) -> CategoryMetricResult:
    relevant = [item for item in observations if item.category == category]
    numerator, denominator, unresolved_count = _totals(relevant)
    if not relevant:
        unresolved_count = 1
    value = _ratio(numerator, denominator)
    threshold_passed = _passes_threshold(value, threshold)
    if value is None and policy.unresolved_handling == "disclose":
        threshold_passed = True
    unresolved_passed = unresolved_count == 0 or policy.unresolved_handling == "disclose"
    return CategoryMetricResult(
        category=category,
        value=value,
        unresolved_count=unresolved_count,
        passed=threshold_passed and unresolved_passed,
    )


def _totals(observations: list[QualityObservation]) -> tuple[int, int, int]:
    resolved = [item for item in observations if not item.unresolved and item.denominator > 0]
    unresolved_count = sum(item.unresolved or item.denominator == 0 for item in observations)
    return (
        sum(item.numerator for item in resolved),
        sum(item.denominator for item in resolved),
        unresolved_count,
    )


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def _passes_threshold(
    value: float | None,
    threshold: MetricThreshold,
) -> bool:
    if value is None:
        return False
    if threshold.minimum is not None:
        return value >= threshold.minimum
    if threshold.maximum is not None:
        return value <= threshold.maximum
    return False


def _validate_observations(
    observations: list[QualityObservation],
    policy: BenchmarkPolicy,
) -> None:
    metric_names = {metric.name for metric in policy.metrics}
    item_ids: set[str] = set()
    for observation in observations:
        if not observation.item_id.strip():
            raise ValueError("observation item id must not be blank")
        identity = f"{observation.item_id}:{observation.metric}"
        if identity in item_ids:
            raise ValueError(f"duplicate observation {identity!r}")
        item_ids.add(identity)
        if observation.metric not in metric_names:
            raise ValueError(f"unknown metric {observation.metric!r}")
        if observation.category not in policy.categories:
            raise ValueError(f"unknown category {observation.category!r}")
        if (
            isinstance(observation.numerator, bool)
            or isinstance(observation.denominator, bool)
            or observation.numerator < 0
            or observation.denominator < 0
            or observation.numerator > observation.denominator
        ):
            raise ValueError("observation counts must satisfy 0 <= numerator <= denominator")
