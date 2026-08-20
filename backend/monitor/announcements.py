from dataclasses import dataclass


@dataclass(frozen=True)
class SystemAnnouncement:
    code: str
    title: str
    published_at: str
    severity: str
    paragraphs: tuple[str, ...]


ANNOUNCEMENTS = (
    SystemAnnouncement(
        code="sub2api-fast-model-correction-0-1-179",
        title="Sub2API 0.1.179 FAST 计费调整",
        published_at="2026-08-20T00:00:00Z",
        severity="warning",
        paragraphs=(
            "Sub2API 0.1.179 起支持在渠道模型定价规则中配置 FAST 倍率，但目前没有统一配置入口，需要针对各模型规则分别设置。建议优先在 Sub2API 中将需要的模型 FAST 倍率配置为 2.5。",
            "系统设置新增了更详细的 FAST 模型修正功能，支持模型通配符和从上到下的优先匹配。默认会把所有模型的 2 倍 FAST 成本修正为 2.5 倍。",
            "如果某个模型已在 Sub2API 中配置为 2.5 倍，可以在系统设置中为该模型添加 2.5 倍到 2.5 倍的规则，避免重复修正。历史 FAST 修正事实不受影响。",
        ),
    ),
)

ANNOUNCEMENTS_BY_CODE = {item.code: item for item in ANNOUNCEMENTS}
