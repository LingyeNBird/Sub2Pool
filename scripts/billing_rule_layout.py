"""Geometry and interaction checks for the three shared billing rule editors.

Called by billing_browser_smoke.py against its real frontend. Screenshots and
measurements go to the existing isolated CI evidence directory.
"""
import json
from pathlib import Path

from playwright.sync_api import Locator, Page, expect


# Test both the supplied screenshot's width and narrower/tablet/mobile layouts.
WIDTHS = (1440, 894, 768, 640, 390, 320)
KINDS = (("fast", 3, 1), ("long", 4, 2), ("model", 2, 1))


def measure_rule(rule: Locator) -> dict:
    return rule.evaluate("""element => {
        const bounds = node => {
            const r = node.getBoundingClientRect();
            return {x: r.x, y: r.y, width: r.width, height: r.height,
                    right: r.right, bottom: r.bottom};
        };
        const fields = [...element.querySelectorAll('label')];
        const grid = fields[0].parentElement;
        const actions = element.querySelector('[aria-label="上移规则"]').parentElement;
        const canvas = document.createElement('canvas');
        const context = canvas.getContext('2d');
        return {
            card: bounds(element),
            title: bounds(element.querySelector('[aria-hidden="true"] span')),
            actions: bounds(actions),
            actionsPosition: getComputedStyle(actions).position,
            rowGap: parseFloat(getComputedStyle(grid).rowGap),
            fields: fields.map(field => {
                const label = field.querySelector('span');
                const input = field.querySelector('input');
                const style = getComputedStyle(input);
                context.font = `${style.fontSize} ${style.fontFamily}`;
                return {
                    label: bounds(label), input: bounds(input), type: input.type,
                    labelWidth: label.clientWidth, labelScrollWidth: label.scrollWidth,
                    // Reserve room for padding and the native number spinner.
                    requiredWidth: context.measureText(input.value).width +
                        parseFloat(style.paddingLeft) + parseFloat(style.paddingRight) + 18,
                };
            }),
        };
    }""")


def check_rule_layout(page: Page, configure: Locator, output: Path) -> None:
    original_viewport = page.viewport_size
    measurements = []
    try:
        for editor_index, (kind, field_count, rule_count) in enumerate(KINDS):
            page.set_viewport_size({"width": 1440, "height": 1000})
            configure.nth(editor_index).click()
            dialog = page.locator("dialog[open]").last
            rules = dialog.locator("fieldset")
            expect(rules).to_have_count(rule_count)
            # A real legend still provides the fieldset's accessible name.
            expect(dialog.get_by_role("group", name="规则 1", exact=True)).to_have_count(1)
            original_patterns = dialog.get_by_label("模型匹配", exact=True).evaluate_all(
                "inputs => inputs.map(input => input.value)"
            )
            if kind == "long":
                # Keep the existing upper bound readable, not only the six-digit default.
                rules.first.get_by_label("兜底阈值（输入 Token）", exact=True).fill("100000000")
            for width in WIDTHS:
                page.set_viewport_size({"width": width, "height": 1000 if width >= 640 else 844})
                rules.first.scroll_into_view_if_needed()
                geometry = measure_rule(rules.first)
                measurements.append({"kind": kind, "viewport": width, **geometry})
                fields, card, title, actions = (
                    geometry[key] for key in ("fields", "card", "title", "actions")
                )
                assert len(fields) == field_count, geometry
                assert geometry["actionsPosition"] == "absolute", geometry
                assert 0 <= card["right"] - actions["right"] <= 12, geometry
                assert 0 <= actions["y"] - card["y"] <= 12, geometry
                assert actions["x"] >= title["right"] + 4, geometry
                assert actions["y"] <= title["y"] + title["height"] / 2 <= actions["bottom"], geometry
                # Neither a separate toolbar row nor stacked fieldset padding.
                assert 0 <= fields[0]["label"]["y"] - title["bottom"] <= 18, geometry
                assert geometry["rowGap"] <= 8, geometry
                for field in fields:
                    label, box = field["label"], field["input"]
                    assert 0 <= box["y"] - label["bottom"] <= 5, geometry
                    assert card["x"] <= box["x"] < box["right"] <= card["right"], geometry
                    assert field["labelScrollWidth"] <= field["labelWidth"] + 1, geometry
                    assert box["height"] >= 32, geometry
                    if field["type"] == "number":
                        assert field["requiredWidth"] <= box["width"], geometry
                    assert actions["bottom"] < box["y"], geometry
                input_rows = {round(field["input"]["y"]) for field in fields}
                if width >= 894:
                    assert len(input_rows) == 1, geometry
                    assert card["height"] <= 140, geometry
                    for field in fields[1:]:
                        assert 120 <= field["input"]["width"] <= 170, geometry
                    assert fields[0]["input"]["width"] > fields[1]["input"]["width"], geometry
                if width <= 390:
                    assert len(input_rows) == (3 if kind == "long" else 2), geometry
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), geometry
                if width in (894, 390):
                    if kind == "long":
                        rules.first.get_by_label("兜底阈值（输入 Token）", exact=True).fill("272000")
                    dialog.get_by_role("heading").click()  # Screenshot the default, unfocused fields.
                    dialog.locator(".modal-box").screenshot(path=str(output / f"rule-{kind}-{width}.png"))
                    if kind == "long":
                        rules.first.get_by_label("兜底阈值（输入 Token）", exact=True).fill("100000000")

            # All three editor modes retain add/move/delete/cancel behavior.
            page.set_viewport_size({"width": 894, "height": 1000})
            dialog.get_by_role("button", name="添加模型规则", exact=True).click()
            expect(rules).to_have_count(rule_count + 1)
            patterns = dialog.get_by_label("模型匹配", exact=True)
            new_index = next(i for i in range(rule_count + 1) if patterns.nth(i).input_value() == "")
            patterns.nth(new_index).fill("ui-layout-test-*")
            before_move = patterns.evaluate_all("inputs => inputs.map(input => input.value)")
            offset, move, undo = (1, "下移规则", "上移规则") if new_index == 0 else (-1, "上移规则", "下移规则")
            rules.nth(new_index).get_by_role("button", name=move, exact=True).click()
            expected = list(before_move)
            expected[new_index], expected[new_index + offset] = expected[new_index + offset], expected[new_index]
            assert patterns.evaluate_all("inputs => inputs.map(input => input.value)") == expected
            rules.nth(new_index + offset).get_by_role("button", name=undo, exact=True).click()
            assert patterns.evaluate_all("inputs => inputs.map(input => input.value)") == before_move
            rules.nth(new_index).get_by_role("button", name="删除规则", exact=True).click()
            expect(rules).to_have_count(rule_count)
            expect(rules.first.get_by_role("button", name="上移规则", exact=True)).to_be_disabled()
            expect(rules.last.get_by_role("button", name="下移规则", exact=True)).to_be_disabled()
            dialog.get_by_role("button", name="取消", exact=True).click()
            configure.nth(editor_index).click()
            assert dialog.get_by_label("模型匹配", exact=True).evaluate_all("inputs => inputs.map(input => input.value)") == original_patterns
            if kind == "long":
                expect(rules.first.get_by_label("兜底阈值（输入 Token）", exact=True)).to_have_value("272000")
            dialog.get_by_role("button", name="取消", exact=True).click()
        (output / "rule-layout-results.json").write_text(json.dumps({
            "passed": True, "viewport_checks": len(measurements), "measurements": measurements,
        }, ensure_ascii=False, indent=2))
    except Exception:
        (output / "rule-layout-failure.json").write_text(json.dumps(measurements, ensure_ascii=False, indent=2))
        page.screenshot(path=str(output / "rule-layout-failure.png"))
        raise
    finally:
        if original_viewport:
            page.set_viewport_size(original_viewport)
