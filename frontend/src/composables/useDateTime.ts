import { useAuthStore } from "@/stores/auth";

function displayDateTime(
  value: string | null | undefined,
  timeZone: string,
  fallback: string,
): string {
  if (!value) return fallback;
  const instant = new Date(value);
  if (Number.isNaN(instant.getTime())) return fallback;
  try {
    return instant.toLocaleString("zh-CN", {
      timeZone,
      hour12: false,
    });
  } catch {
    // 旧数据库若保存过无效时区，仍以 UTC 稳定展示，避免再次受浏览器本地时区影响。
    return instant.toLocaleString("zh-CN", {
      timeZone: "UTC",
      hour12: false,
    });
  }
}

function offsetAt(instant: Date, timeZone: string): number {
  const formatter = new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  });
  const parts = Object.fromEntries(
    formatter
      .formatToParts(instant)
      .filter((part) => part.type !== "literal")
      .map((part) => [part.type, part.value]),
  );
  const wallClockAsUtc = Date.UTC(
    Number(parts.year),
    Number(parts.month) - 1,
    Number(parts.day),
    Number(parts.hour),
    Number(parts.minute),
    Number(parts.second),
  );
  return wallClockAsUtc - instant.getTime();
}

function zonedDateTimeToIso(value: string, timeZone: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(
    value,
  );
  if (!match) return new Date(value).toISOString();

  const wallClockAsUtc = Date.UTC(
    Number(match[1]),
    Number(match[2]) - 1,
    Number(match[3]),
    Number(match[4]),
    Number(match[5]),
    Number(match[6] ?? 0),
  );
  let instant = wallClockAsUtc;
  // 两轮足以处理绝大多数带夏令时的 IANA 时区；上海时区第一轮即可收敛。
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const adjusted = wallClockAsUtc - offsetAt(new Date(instant), timeZone);
    if (adjusted === instant) break;
    instant = adjusted;
  }
  return new Date(instant).toISOString();
}

export function useDateTime(fallback = "—") {
  const auth = useAuthStore();
  return (value: string | null | undefined) =>
    displayDateTime(value, auth.timezone, fallback);
}

export function useZonedDateTimeIso() {
  const auth = useAuthStore();
  return (value: string) => zonedDateTimeToIso(value, auth.timezone);
}
