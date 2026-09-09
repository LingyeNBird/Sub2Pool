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
        code="sub2api-upstream-pricing-2026-09-08",
        title="计费倍率改由 Sub2API 统一配置",
        published_at="2026-09-08T00:00:00Z",
        severity="info",
        paragraphs=(
            "推荐点击“一键应用修正”，选择要应用的 Sub2API OpenAI 分组并确认：GPT-6 系列 FAST 2 倍、其他模型 FAST 2.5 倍、gpt-6* 模型 1.8 倍、关闭长上下文阶梯计费，影响所选组内所有用户。取消不执行；确认请求被接受后，一键应用入口永久消失，失败或部分成功可在设置页核对并重试。",
            "升级前的本地修正规则已冻结并继续用于旧记录重放。新记录只使用上游原始成本，不再叠加本地修正；仅确认上游配置成功的记录显示“已修正”。计费状态改变时会自动建立新的测算区间。",
            "撤回会恢复本服务接管前的上游计费字段，不会修改旧请求、不重新开启本地修正，也不会再次自动应用。若上游字段已被其他人修改，系统会拒绝覆盖并说明冲突。",
        ),
    ),
    SystemAnnouncement(
        code="auto-apply-recommendations-default-2026-09-08",
        title="自动应用建议额度默认开启",
        published_at="2026-09-08T00:00:00Z",
        severity="info",
        paragraphs=(
            "因为现在额度测算较为完善，所以默认为您打开“自动应用建议额度”。如不需要，请在系统设置里面关闭。",
            "如果您之前关闭了此功能，可点击“一键开启”。开启后按监控流程自动应用符合条件的额度建议；仍可随时在设置页关闭。",
        ),
    ),
)

ANNOUNCEMENTS_BY_CODE = {item.code: item for item in ANNOUNCEMENTS}
