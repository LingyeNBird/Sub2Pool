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
        code="sub2api-long-context-correction-2026-09",
        title="长上下文双倍倍率修正默认启用",
        published_at="2026-09-05T00:00:00Z",
        severity="info",
        paragraphs=(
            '根据科学的测算研究，发现在 OpenAI 订阅中，5.6 以及 6 系列的模型并没有双倍倍率。因此，默认为您打开了双倍倍率修正为 1。如果你在 sub2API 手动设置了双倍倍率已经为 1,那么可以前往系统设置页面，“双倍倍率修正”选项那里，将 Sub2API 双倍倍率改写成 1。在默认情况下，您不需要进行任何操作',
        ),
    ),
    SystemAnnouncement(
        code="sub2api-gpt6-model-correction-2026-09",
        title="GPT-6 系列默认计费倍率设为 1.8",
        published_at="2026-09-05T00:00:00Z",
        severity="info",
        paragraphs=(
            '根据这两篇帖子的相关研究：https://linux.do/t/topic/2861126 和https://linux.do/t/topic/2860543 ，目前已将 GPT-6 系列的默认计费倍率设置成了 1.8。如果您在 sub2API 中已经关闭了长上下文阶梯计费，还请您前往系统设置的模型计费倍率设置项中，将计费倍率改成 1。 在默认情况下，您不需要进行任何操作',
        ),
    ),
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
