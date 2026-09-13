import type { TemporaryDisable } from "@/types/accounts";

export type DurationUnit = "hours" | "minutes";

export const MIN_DISABLE_MINUTES = 1;
export const MAX_DISABLE_MINUTES = 60 * 24 * 30;
export const DEFAULT_DISABLE_MINUTES = 60 * 4;
export const MAX_REMAINING_TEXT = "已到期";

export function disableMinutes(
  amount: number,
  unit: DurationUnit,
): number | null {
  const minutes = unit === "hours" ? amount * 60 : amount;
  if (!Number.isFinite(minutes) || Math.floor(minutes) !== minutes) return null;
  if (minutes < MIN_DISABLE_MINUTES || minutes > MAX_DISABLE_MINUTES) {
    return null;
  }
  return minutes;
}

export function minutesToDuration(minutes: number): {
  amount: number;
  unit: DurationUnit;
} {
  if (minutes >= 60 && minutes % 60 === 0) {
    return { amount: minutes / 60, unit: "hours" };
  }
  return { amount: minutes, unit: "minutes" };
}

export function remainingSeconds(
  restoreAt: string | null,
  now: number,
): number | null {
  if (!restoreAt) return null;
  const target = Date.parse(restoreAt);
  if (Number.isNaN(target)) return null;
  return Math.max(0, Math.round((target - now) / 1000));
}

export function formatRemaining(seconds: number | null): string {
  if (seconds == null) return "";
  if (seconds <= 0) return MAX_REMAINING_TEXT;
  const days = Math.floor(seconds / 86_400);
  const hours = Math.floor((seconds % 86_400) / 3_600);
  const minutes = Math.floor((seconds % 3_600) / 60);
  if (days > 0) return `${days} 天 ${hours} 小时`;
  if (hours > 0) return `${hours} 小时 ${minutes} 分钟`;
  if (minutes > 0) return `${minutes} 分钟`;
  return "不到 1 分钟";
}

export function describeDisable(
  disable: TemporaryDisable,
  now: number,
): string {
  const subject =
    disable.scope === "account"
      ? "该账号已被禁用"
      : `模型 ${disable.model} 已被禁用`;
  const remaining = formatRemaining(remainingSeconds(disable.restore_at, now));
  if (!remaining) return subject;
  if (remaining === MAX_REMAINING_TEXT) return `${subject}，等待恢复`;
  return `${subject}，${remaining}后恢复`;
}
