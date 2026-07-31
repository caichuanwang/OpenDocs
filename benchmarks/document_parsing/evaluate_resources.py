from __future__ import annotations

import argparse
import asyncio
import json
import resource
import time
from dataclasses import dataclass
from pathlib import Path

from opendocs.vision.base import (
    DispatchAttemptKind,
    VisionDispatcher,
    VisionRequest,
    VisionRequestKind,
    VisionResult,
)

RESOURCE_EVALUATOR_VERSION = "0.1"


@dataclass(frozen=True, slots=True)
class ResourceProbeConfig:
    per_parse_limits: tuple[int, ...]
    requests_per_parse: int
    caller_limit: int
    operation_delay: float
    timeout: float
    environment_identity: str
    candidate_commit: str = "unbound"
    package_version: str = "unbound"
    policy_digest: str = "unbound"
    manifest_digest: str = "unbound"

    def __post_init__(self) -> None:
        if not self.per_parse_limits or any(
            isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0
            for limit in self.per_parse_limits
        ):
            raise ValueError("per_parse_limits must contain positive integers")
        if (
            isinstance(self.requests_per_parse, bool)
            or not isinstance(self.requests_per_parse, int)
            or self.requests_per_parse <= 0
        ):
            raise ValueError("requests_per_parse must be a positive integer")
        if (
            isinstance(self.caller_limit, bool)
            or not isinstance(self.caller_limit, int)
            or self.caller_limit <= 0
        ):
            raise ValueError("caller_limit must be a positive integer")
        if (
            isinstance(self.operation_delay, bool)
            or not isinstance(self.operation_delay, int | float)
            or self.operation_delay < 0
        ):
            raise ValueError("operation_delay must be a non-negative number")
        if (
            isinstance(self.timeout, bool)
            or not isinstance(self.timeout, int | float)
            or self.timeout <= 0
        ):
            raise ValueError("timeout must be a positive number")
        if not isinstance(self.environment_identity, str) or not self.environment_identity.strip():
            raise ValueError("environment_identity must not be blank")
        for name in (
            "candidate_commit",
            "package_version",
            "policy_digest",
            "manifest_digest",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must not be blank")


@dataclass(frozen=True, slots=True)
class ResourceReport:
    candidate_commit: str
    package_version: str
    policy_digest: str
    manifest_digest: str
    environment_identity: str
    per_parse_limits: tuple[int, ...]
    requests_per_parse: int
    caller_limit: int
    timeout_seconds: float
    elapsed_seconds: float
    peak_documents: int
    peak_visual_by_parse: tuple[int, ...]
    peak_visual_global: int
    peak_rss_raw: int
    completed: bool
    timed_out: bool
    dispatchers_closed: bool
    passed: bool

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "evaluator_version": RESOURCE_EVALUATOR_VERSION,
            "candidate_commit": self.candidate_commit,
            "package_version": self.package_version,
            "policy_digest": self.policy_digest,
            "manifest_digest": self.manifest_digest,
            "environment_identity": self.environment_identity,
            "per_parse_limits": list(self.per_parse_limits),
            "requests_per_parse": self.requests_per_parse,
            "caller_limit": self.caller_limit,
            "timeout_seconds": self.timeout_seconds,
            "elapsed_seconds": self.elapsed_seconds,
            "peak_documents": self.peak_documents,
            "peak_visual_by_parse": list(self.peak_visual_by_parse),
            "peak_visual_global": self.peak_visual_global,
            "peak_rss_raw": self.peak_rss_raw,
            "completed": self.completed,
            "timed_out": self.timed_out,
            "dispatchers_closed": self.dispatchers_closed,
            "passed": self.passed,
        }


async def measure_dispatchers(config: ResourceProbeConfig) -> ResourceReport:
    dispatchers = tuple(VisionDispatcher(limit) for limit in config.per_parse_limits)
    caller_semaphore = asyncio.Semaphore(config.caller_limit)
    active_by_parse = [0 for _ in config.per_parse_limits]
    peak_by_parse = [0 for _ in config.per_parse_limits]
    active_documents = 0
    peak_documents = 0
    active_global = 0
    peak_global = 0
    dispatchers_closed = False
    timed_out = False
    completed = False
    started = time.perf_counter()

    async def run_parse(index: int, dispatcher: VisionDispatcher) -> None:
        nonlocal active_documents, peak_documents, active_global, peak_global

        async def handler(
            request: VisionRequest,
            kind: DispatchAttemptKind,
            retry_index: int,
            repair_index: int,
            repair_payload: str | None,
        ) -> VisionResult:
            del request, kind, retry_index, repair_index, repair_payload
            nonlocal active_global, peak_global
            active_by_parse[index] += 1
            active_global += 1
            peak_by_parse[index] = max(
                peak_by_parse[index],
                active_by_parse[index],
            )
            peak_global = max(peak_global, active_global)
            try:
                await asyncio.sleep(config.operation_delay)
                return VisionResult(())
            finally:
                active_by_parse[index] -= 1
                active_global -= 1

        requests = tuple(
            VisionRequest(
                image_path=Path("synthetic-image.png"),
                prompt="synthetic",
                source_index=request_index,
                kind=VisionRequestKind.FULL_PAGE,
            )
            for request_index in range(config.requests_per_parse)
        )
        async with caller_semaphore:
            active_documents += 1
            peak_documents = max(peak_documents, active_documents)
            try:
                await dispatcher.dispatch(requests, handler)
            finally:
                active_documents -= 1

    tasks = tuple(
        asyncio.create_task(run_parse(index, dispatcher))
        for index, dispatcher in enumerate(dispatchers)
    )
    try:
        async with asyncio.timeout(config.timeout):
            await asyncio.gather(*tasks)
        completed = True
    except TimeoutError:
        timed_out = True
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        await asyncio.gather(
            *(dispatcher.aclose() for dispatcher in dispatchers),
            return_exceptions=False,
        )
        dispatchers_closed = True

    elapsed = time.perf_counter() - started
    peak_rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    limits_respected = all(
        observed <= limit
        for observed, limit in zip(peak_by_parse, config.per_parse_limits, strict=True)
    )
    passed = (
        completed
        and not timed_out
        and dispatchers_closed
        and limits_respected
        and peak_documents <= config.caller_limit
        and active_global == 0
        and all(active == 0 for active in active_by_parse)
    )
    return ResourceReport(
        candidate_commit=config.candidate_commit,
        package_version=config.package_version,
        policy_digest=config.policy_digest,
        manifest_digest=config.manifest_digest,
        environment_identity=config.environment_identity,
        per_parse_limits=config.per_parse_limits,
        requests_per_parse=config.requests_per_parse,
        caller_limit=config.caller_limit,
        timeout_seconds=float(config.timeout),
        elapsed_seconds=elapsed,
        peak_documents=peak_documents,
        peak_visual_by_parse=tuple(peak_by_parse),
        peak_visual_global=peak_global,
        peak_rss_raw=peak_rss,
        completed=completed,
        timed_out=timed_out,
        dispatchers_closed=dispatchers_closed,
        passed=passed,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Measure the bounded M3 dispatcher resource envelope."
    )
    parser.add_argument("--limits", default="1,4")
    parser.add_argument("--requests-per-parse", type=int, default=8)
    parser.add_argument("--caller-limit", type=int, default=2)
    parser.add_argument("--operation-delay", type=float, default=0.01)
    parser.add_argument("--timeout", type=float, default=10)
    parser.add_argument("--environment-identity", required=True)
    parser.add_argument("--candidate-commit", required=True)
    parser.add_argument("--package-version", default="0.1.0")
    parser.add_argument("--policy-digest", required=True)
    parser.add_argument("--manifest-digest", required=True)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        limits = tuple(int(value) for value in args.limits.split(","))
    except ValueError as error:
        raise ValueError("--limits must be a comma-separated list of integers") from error
    report = asyncio.run(
        measure_dispatchers(
            ResourceProbeConfig(
                per_parse_limits=limits,
                requests_per_parse=args.requests_per_parse,
                caller_limit=args.caller_limit,
                operation_delay=args.operation_delay,
                timeout=args.timeout,
                environment_identity=args.environment_identity,
                candidate_commit=args.candidate_commit,
                package_version=args.package_version,
                policy_digest=args.policy_digest,
                manifest_digest=args.manifest_digest,
            )
        )
    )
    serialized = json.dumps(report.to_safe_dict(), indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(serialized, end="")
    else:
        args.output.write_text(serialized, encoding="utf-8")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
