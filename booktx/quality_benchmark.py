"""Small deterministic benchmark for first-pass linguistic regression cases."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from booktx.linguistic_audit import audit_text


@dataclass(frozen=True, slots=True)
class QualityBenchmarkReport:
    total_cases: int
    expected_builtin_cases: int
    detected_builtin_cases: int
    false_positive_cases: int

    @property
    def builtin_recall(self) -> float:
        if not self.expected_builtin_cases:
            return 1.0
        return self.detected_builtin_cases / self.expected_builtin_cases

    def as_dict(self) -> dict[str, object]:
        return {
            "total_cases": self.total_cases,
            "expected_builtin_cases": self.expected_builtin_cases,
            "detected_builtin_cases": self.detected_builtin_cases,
            "false_positive_cases": self.false_positive_cases,
            "builtin_recall": self.builtin_recall,
        }


def load_cases(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text("utf-8"))
    if not isinstance(payload, list):
        raise ValueError("quality fixture must contain a JSON list")
    return payload


def run_builtin_benchmark(path: Path) -> QualityBenchmarkReport:
    cases = load_cases(path)
    expected = 0
    detected = 0
    false_positive = 0
    for case in cases:
        should_detect = bool(case.get("should_builtin_detect"))
        findings = audit_text(
            str(case["source"]),
            str(case["bad_target"]),
            str(case["id"]),
            locale=str(case.get("locale", "de-DE")),
        )
        found = bool(findings)
        expected += should_detect
        detected += should_detect and found
        false_positive += not should_detect and found
    return QualityBenchmarkReport(
        total_cases=len(cases),
        expected_builtin_cases=expected,
        detected_builtin_cases=detected,
        false_positive_cases=false_positive,
    )


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    args = parser.parse_args()
    print(json.dumps(run_builtin_benchmark(args.fixture).as_dict(), indent=2))


if __name__ == "__main__":
    main()
