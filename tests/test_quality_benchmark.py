from pathlib import Path

from booktx.quality_benchmark import run_builtin_benchmark


def test_book6_style_fixture_reports_builtin_metrics() -> None:
    fixture = Path("tests/fixtures/translation_quality/book6.json")
    report = run_builtin_benchmark(fixture)
    assert report.total_cases == 6
    assert report.expected_builtin_cases == 5
    assert report.detected_builtin_cases == 5
    assert report.false_positive_cases == 0
    assert report.builtin_recall == 1.0
