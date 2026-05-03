from __future__ import annotations

from lms_migration.canvas_template_accessibility import (
    apply_template_page_accessibility_presets,
)


def test_template_image_customizations_demotes_body_h1_and_normalizes_gray_heading() -> None:
    html = (
        '<h2 style="color: #ffffff; background: #CACACA;">'
        '<span style="color: #ffffff;"><strong>Licensing and Image Source Documentation</strong></span>'
        "</h2>"
        '<h1><span style="font-size: 18pt;"><strong>General Tips</strong></span></h1>'
    )

    updated, changes = apply_template_page_accessibility_presets(
        title="Template: Image Customizations",
        body_html=html,
    )

    assert 'background: #CACACA' in updated
    assert 'color: #000000' in updated
    assert '<span style="color: #ffffff;' not in updated
    assert "<h1" not in updated
    assert updated.count("<h2") == 2
    assert any("gray template section headings" in item.description for item in changes)
    assert any("Demoted body H1 headings to H2" in item.description for item in changes)


def test_non_target_page_is_unchanged() -> None:
    html = '<h1 style="color: #ffffff; background: #CACACA;">Heading</h1>'
    updated, changes = apply_template_page_accessibility_presets(
        title="Ordinary Page",
        body_html=html,
    )

    assert updated == html
    assert changes == []
