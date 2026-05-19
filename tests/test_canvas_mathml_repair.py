from lms_migration.canvas_mathml_repair import repair_mathml_json_in_html


def test_repair_mathml_json_in_html_repairs_bare_payload():
    broken = (
        '<p>{"version":"1.1","math":"&lt;math xmlns=\\"http://www.w3.org/1998/Math/MathML\\"&gt;'
        '&lt;mn&gt;8&lt;/mn&gt;&lt;mo&gt;+&lt;/mo&gt;&lt;mn&gt;4&lt;/mn&gt;&lt;/math&gt;"}</p>'
    )
    updated, repaired = repair_mathml_json_in_html(broken)
    assert repaired == 1
    assert '<math xmlns="http://www.w3.org/1998/Math/MathML"><mn>8</mn><mo>+</mo><mn>4</mn></math>' in updated
    assert '{"version":"1.1","math":' not in updated


def test_repair_mathml_json_in_html_preserves_valid_annotation():
    valid = (
        '<math xmlns="http://www.w3.org/1998/Math/MathML"><semantics><mn>8</mn>'
        '<annotation encoding="wiris">'
        '{"version":"1.1","math":"&lt;math xmlns=\\"http://www.w3.org/1998/Math/MathML\\"&gt;'
        '&lt;mn&gt;8&lt;/mn&gt;&lt;/math&gt;"}'
        "</annotation></semantics></math>"
    )
    updated, repaired = repair_mathml_json_in_html(valid)
    assert repaired == 0
    assert updated == valid
