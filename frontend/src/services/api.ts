export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public details?: unknown,
  ) {
    super(message);
  }
}

interface ApiPayload {
  ok?: boolean;
  data?: unknown;
  message?: string;
  details?: unknown;
}

let accessToken = "";
let refreshRequest: Promise<string> | null = null;

export function setAccessToken(token: string): void {
  // Access Token 只保存在当前页面内存，刷新页面后由 HttpOnly Cookie 静默续期。
  accessToken = token;
}

export function clearAccessToken(): void {
  accessToken = "";
}

function apiPath(path: string): string {
  return path.replace(/^\//, "");
}

async function parsePayload(response: Response): Promise<ApiPayload> {
  return response
    .json()
    .catch(() => ({ ok: false, message: "服务器返回了无效响应" }));
}

async function performRefresh(): Promise<string> {
  const response = await fetch("/api/auth/refresh", {
    method: "POST",
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  });
  const payload = await parsePayload(response);
  const data = payload.data as { access?: string } | undefined;
  if (!response.ok || !payload.ok || !data?.access) {
    clearAccessToken();
    throw new ApiError(
      payload.message ?? "登录已过期，请重新登录",
      response.status,
      payload.details,
    );
  }
  setAccessToken(data.access);
  return data.access;
}

async function refreshAccessToken(): Promise<string> {
  if (!refreshRequest) {
    // 多个并发请求同时遇到 401 时只刷新一次，其余请求共用同一个 Promise。
    refreshRequest = performRefresh().finally(() => {
      refreshRequest = null;
    });
  }
  return refreshRequest;
}

function mayRefresh(path: string): boolean {
  return path !== "auth/login" && path !== "auth/refresh";
}

async function requestResponse(
  path: string,
  options: RequestInit,
  retried: boolean,
): Promise<Response> {
  const headers = new Headers(options.headers);
  const isFormData =
    typeof FormData !== "undefined" && options.body instanceof FormData;
  if (options.body && !headers.has("Content-Type") && !isFormData) {
    headers.set("Content-Type", "application/json");
  }
  headers.set("Accept", "application/json");
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);

  const response = await fetch(`/api/${path}`, {
    ...options,
    headers,
    credentials: "same-origin",
  });

  if (response.status === 401 && !retried && mayRefresh(path)) {
    const hadAccessToken = Boolean(accessToken);
    try {
      await refreshAccessToken();
    } catch (error) {
      if (hadAccessToken) {
        window.dispatchEvent(new CustomEvent("pinche:auth-expired"));
      }
      throw error;
    }
    return requestResponse(path, options, true);
  }
  return response;
}

async function request<T>(
  path: string,
  options: RequestInit,
  retried: boolean,
): Promise<T> {
  const response = await requestResponse(path, options, retried);
  const payload = await parsePayload(response);
  if (!response.ok || !payload.ok) {
    throw new ApiError(
      payload.message ?? `请求失败 (${response.status})`,
      response.status,
      payload.details,
    );
  }
  return payload.data as T;
}

export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  return request<T>(apiPath(path), options, false);
}

export async function apiBlob(
  path: string,
  options: RequestInit = {},
): Promise<Blob> {
  const normalizedPath = apiPath(path);
  const response = await requestResponse(normalizedPath, options, false);
  if (!response.ok) {
    const payload = await parsePayload(response);
    throw new ApiError(
      payload.message ?? `请求失败 (${response.status})`,
      response.status,
      payload.details,
    );
  }
  return response.blob();
}

export function jsonBody(value: unknown): string {
  return JSON.stringify(value);
}
