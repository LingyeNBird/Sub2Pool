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
        code="sub2api-wallet-conversion-2026-09-08",
        title="建议优先在 Sub2API 中统一计费倍率",
        published_at="2026-09-08T00:00:00Z",
        severity="info",
        paragraphs=(
            "为使额度测算与用户余额扣费保持一致，建议优先在 Sub2API 中配置适合您部署的 FAST、长上下文及模型计费倍率，并在 Sub2Pool 中使用实际扣费口径。确认某项倍率已在上游生效后，再关闭本地对应修正，避免重复计算；无法调整上游时，可继续使用本地修正。",
            "使用本地修正时，建议余额现在会按参与者在对应账号、当前测算区间的历史消费结构，换算为预计需要的 Sub2API 实际余额；本人没有消费样本时使用账号样本。模型、FAST 或其他用法改变后，换算估计会随新增采样更新，不保证未来消费绝对精确。",
            "上游倍率调整不代表旧请求成本也会改变；本地修正规则变更又会重算已有历史。请以调整后的观测建立新的测算起点，避免一个测算区间混用新旧扣费规则。本次更新不会替您关闭修正或改写原始请求成本。",
        ),
    ),
    SystemAnnouncement(
        code="auto-apply-recommendations-default-2026-09-08",
        title="自动应用建议额度默认开启",
        published_at="2026-09-08T00:00:00Z",
        severity="info",
        paragraphs=(
            "因为现在额度测算较为完善，所以默认为您打开“自动应用建议额度”。如不需要，请在系统设置里面关闭。",
        ),
    ),
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
