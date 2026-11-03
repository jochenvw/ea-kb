"""Render an `ekb assess` JSON report as Markdown, for example a GitHub Actions job summary.

Usage: uv run python scripts\\assessment_summary.py report.json >> $GITHUB_STEP_SUMMARY
"""

import sys
from pathlib import Path

from ekb.domain.assessment import SolutionAssessment


def cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def render(report: SolutionAssessment) -> str:
    coverage = report.coverage
    verdict = "Conforms" if report.conforms else "Conformance not established"
    if report.acceptable_with_exceptions and not report.conforms:
        verdict += " (acceptable with approved exceptions)"
    lines = [
        f"## EKB assessment: {verdict}",
        "",
        f"As of {report.target.as_of}"
        + (f" - commit `{report.target.commit}`" if report.target.commit else "")
        + f" - template SHA-256 `{report.target.artifact_sha256[:12]}`",
        "",
        f"{coverage.applicable} applicable of {coverage.total} baseline requirements - "
        f"{coverage.violated} violated - {coverage.excepted} excepted - "
        f"{coverage.unresolved} unknown. Unknown never passes.",
        "",
    ]
    lines += [f"- {cell(gap)}" for gap in report.gaps]
    if report.gaps:
        lines.append("")
    lines += [
        "| Requirement | Disposition | Design | Implementation | Remediation |",
        "| --- | --- | --- | --- | --- |",
    ]
    lines += [
        f"| {cell(finding.requirement.statement)} | {finding.disposition} | {finding.design} "
        f"| {finding.implementation} | {cell(finding.remediation or '')} |"
        for finding in report.findings
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: assessment_summary.py REPORT.json", file=sys.stderr)
        return 2
    report = SolutionAssessment.model_validate_json(Path(argv[1]).read_text(encoding="utf-8"))
    sys.stdout.write(render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
