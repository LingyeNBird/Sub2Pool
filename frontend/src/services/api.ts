export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public details?: unknown,
  ) {
    super(message);
  }
}

let csrfReady = false;

function cookie(name: string): string {
  const prefix = `${name}=`;
  const item = document.cookie
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith(prefix));
  return item ? decodeURIComponent(item.slice(prefix.length)) : "";
}

export async function ensureCsrf(): Promise<void> {
  if (csrfReady && cookie("csrftoken")) return;
  const response = await fetch("/api/auth/csrf", {
    credentials: "same-origin",
  });
  if (!response.ok) throw new ApiError("无法初始化安全会话", response.status);
  csrfReady = true;
}

export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const method = (options.method ?? "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) await ensureCsrf();
  const headers = new Headers(options.headers);
  if (options.body && !headers.has("Content-Type"))
    headers.set("Content-Type", "application/json");
  if (!["GET", "HEAD", "OPTIONS"].includes(method))
    headers.set("X-CSRFToken", cookie("csrftoken"));
  const response = await fetch(`/api/${path.replace(/^\//, "")}`, {
    ...options,
    headers,
    credentials: "same-origin",
  });
  const payload = await response
    .json()
    .catch(() => ({ ok: false, message: "服务器返回了无效响应" }));
  if (!response.ok || !payload.ok) {
    throw new ApiError(
      payload.message ?? `请求失败 (${response.status})`,
      response.status,
      payload.details,
    );
  }
  return payload.data as T;
}

export function jsonBody(value: unknown): string {
  return JSON.stringify(value);
}
