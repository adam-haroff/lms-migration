from __future__ import annotations

from lms_migration.canvas_link_validator_triage import (
    build_link_validator_triage_report,
    parse_link_validator_text,
)


def test_parse_link_validator_text_preserves_issue_order() -> None:
    source = """
Module 6: Learning Activities
Page
External links in this resource were unreachable:
Same-Sex Marriage Legalization By Country

Matriarchy, A Visual
Page
Unpublished content referenced in this resource:
Discussion: Matriarchy and Psych Perspectives
""".strip()

    issues = parse_link_validator_text(source)
    assert [issue.resource_title for issue in issues] == [
        "Module 6: Learning Activities",
        "Matriarchy, A Visual",
    ]
    assert issues[0].resource_type == "Page"
    assert issues[1].issue_heading == "Unpublished content referenced in this resource:"


def test_build_link_validator_triage_report_classifies_known_patterns(tmp_path) -> None:
    source = """
Module 7: Learning Activities
Page
External links in this resource were unreachable:
Worldwide education statistics
Sorry, page not found.

Module 9: Learning Activities
Page
External links in this resource were unreachable:
Washington Post: 5 Myths About the Wage Gap
paywall
""".strip()

    report = build_link_validator_triage_report(
        source_text=source,
        output_json_path=tmp_path / "triage.json",
        output_markdown_path=tmp_path / "triage.md",
    )

    assert report["summary"]["issues"] == 2
    assert report["issues"][0]["category"] == "likely_dead_or_offline_external_link"
    assert report["issues"][1]["category"] == "likely_paywalled_external_link"
    assert (tmp_path / "triage.md").exists()
