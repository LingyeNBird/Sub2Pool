"""Exercise upstream pricing rules and their usable bounds on narrow screens."""
import json
from pathlib import Path

from playwright.sync_api import Locator, Page, expect


def check_rule_layout(page: Page, card: Locator, output: Path) -> None:
    original_viewport = page.viewport_size
    measurements = []
    try:
        for width in (1440, 894, 768, 640, 390, 320):
            page.set_viewport_size({"width": width, "height": 1000})
            card.scroll_into_view_if_needed()
            geometry = card.locator("input:not([type=checkbox]), select").evaluate_all("""inputs => inputs.map(input => {
                const r = input.getBoundingClientRect();
                return {type: input.type, width: r.width, left: r.left, right: r.right};
            })""")
            for field in geometry:
                assert field["width"] >= (100 if field["type"] == "text" else 72), (width, field)
                assert 0 <= field["left"] < field["right"] <= width, (width, field)
            measurements.append({"viewport": width, "fields": geometry})
            if width in (1440, 390):
                card.screenshot(path=str(output / f"pricing-card-{width}.png"))
        page.set_viewport_size({"width": 1440, "height": 1000})
        for section in card.locator("section").filter(has=page.get_by_role("button", name="添加规则", exact=True)).all():
            patterns = section.get_by_label("模型匹配", exact=True)
            original = patterns.evaluate_all("inputs => inputs.map(input => input.value)")
            section.get_by_role("button", name="添加规则", exact=True).click()
            patterns.last.fill("synthetic-model-*")
            section.get_by_role("button", name="上移规则", exact=True).last.click()
            expect(patterns.first).to_have_value("synthetic-model-*")
            section.get_by_role("button", name="下移规则", exact=True).first.click()
            section.get_by_role("button", name="删除规则", exact=True).last.click()
            assert patterns.evaluate_all("inputs => inputs.map(input => input.value)") == original
        (output / "rule-layout-results.json").write_text(json.dumps({
            "passed": True, "measurements": measurements,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        if original_viewport:
            page.set_viewport_size(original_viewport)
