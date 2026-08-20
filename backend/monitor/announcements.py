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
        code="sub2api-fast-pricing-0-1-179",
        title="Sub2API 0.1.179 FAST 计费调整",
        published_at="2026-08-20T00:00:00Z",
        severity="warning",
        paragraphs=(
            "Sub2API 0.1.179 起支持在渠道定价中配置 FAST 倍率。未配置时仍可能沿用 2 倍口径；OpenAI OAuth 渠道需要由管理员确认并设置为 2.5。",
            "为避免上游已经按 2.5 倍计费后，Sub2Pool 再额外补足 25% 造成重复计算，本次升级会自动关闭 Sub2Pool 的兼容 FAST 修正。",
            "已经保存的历史 FAST 修正事实不会删除，仍会参与历史重放和成本拆分。若你的 Sub2API 渠道仍按 2 倍计费，请在系统设置中重新开启兼容修正。",
        ),
    ),
)

ANNOUNCEMENTS_BY_CODE = {item.code: item for item in ANNOUNCEMENTS}
