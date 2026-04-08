from __future__ import annotations

from pathlib import Path

from lms_migration.template_overlay import (
    TemplateOverlayConfig,
    apply_template_overlay,
    build_template_overlay_context,
    materialize_template_assets,
)


_TEMPLATE_PACKAGE = (
    Path(__file__).resolve().parents[1]
    / "resources/examples/template/elearn-standard-template-export-20260324.imscc"
)


def test_materialize_template_assets_uses_canonical_root_when_not_full_shell(
    tmp_path: Path,
) -> None:
    context = build_template_overlay_context(
        TemplateOverlayConfig(
            template_package=_TEMPLATE_PACKAGE,
            use_template_web_resources=False,
        )
    )

    summary = materialize_template_assets(context=context, destination_root=tmp_path)

    assert summary["asset_dir"] == "template-images"
    assert (tmp_path / "template-images" / "icons" / "book.png").exists()
    assert not (tmp_path / "web_resources" / "template-images" / "icons" / "book.png").exists()


def test_materialize_template_assets_keeps_web_resources_root_for_full_shell(
    tmp_path: Path,
) -> None:
    context = build_template_overlay_context(
        TemplateOverlayConfig(
            template_package=_TEMPLATE_PACKAGE,
            use_template_web_resources=True,
        )
    )

    summary = materialize_template_assets(context=context, destination_root=tmp_path)

    assert summary["asset_dir"] == "web_resources/template-images"
    assert (tmp_path / "web_resources" / "template-images" / "icons" / "book.png").exists()


def test_apply_template_overlay_rewrites_brightspace_asset_to_canonical_root() -> None:
    context = build_template_overlay_context(
        TemplateOverlayConfig(
            template_package=_TEMPLATE_PACKAGE,
            use_template_web_resources=False,
        )
    )
    html = (
        '<html><body><p><img src="/shared/brightspace_html_template/pages/templateimages/book.png" '
        'alt="Book"></p></body></html>'
    )

    updated, changes, manual_issues, summary = apply_template_overlay(
        html,
        file_path="module1/page.html",
        context=context,
    )

    assert "../template-images/icons/book.png" in updated
    assert changes
    assert not manual_issues
    assert summary["mapped_direct"] >= 1
