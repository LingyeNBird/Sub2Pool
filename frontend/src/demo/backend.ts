import type { DemoState } from "./state";
import { demoIdentity, loadDemoState } from "./state";
import { handleProtectedAuth, handlePublicAuth } from "./handlers/auth";
import { handleDashboard } from "./handlers/dashboard";
import { handleObservations } from "./handlers/observations";
import { handleParticipants } from "./handlers/participants";
import { handleReporting } from "./handlers/reporting";
import { handleSecurity } from "./handlers/security";
import { handleSettings } from "./handlers/settings";
import { handleSystemUsers } from "./handlers/systemUsers";

const JSON_HEADERS = { "Content-Type": "application/json" };
const DEMO_DELAY_MS = 140;

interface DemoPagination {
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

export interface DemoRequestContext {
  readonly url: URL;
  readonly pathname: string;
  readonly method: string;
  readonly payload: Record<string, unknown>;
  readonly state: DemoState;
  readonly ok: (data?: unknown, status?: number) => Response;
  readonly fail: (
    message: string,
    status?: number,
    details?: unknown,
  ) => Response;
  readonly paginate: <T>(items: T[]) => {
    items: T[];
    pagination: DemoPagination;
  };
}

type DemoRequestHandler = (context: DemoRequestContext) => Response | null;

const protectedHandlers: DemoRequestHandler[] = [
  handleProtectedAuth,
  handleSecurity,
  handleDashboard,
  handleParticipants,
  handleSystemUsers,
  handleObservations,
  handleReporting,
  handleSettings,
];

function envelope(data: unknown = null, status = 200): Response {
  return new Response(JSON.stringify({ ok: true, data }), {
    status,
    headers: JSON_HEADERS,
  });
}

function failure(message: string, status = 400, details?: unknown): Response {
  return new Response(JSON.stringify({ ok: false, message, details }), {
    status,
    headers: JSON_HEADERS,
  });
}

function delay(): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, DEMO_DELAY_MS));
}

async function body(options: RequestInit): Promise<Record<string, unknown>> {
  if (!options.body) return {};
  if (typeof options.body === "string") {
    try {
      const parsed = JSON.parse(options.body) as unknown;
      return parsed && typeof parsed === "object" && !Array.isArray(parsed)
        ? (parsed as Record<string, unknown>)
        : {};
    } catch {
      return {};
    }
  }
  return {};
}

function authorized(): Response | null {
  return demoIdentity() ? null : failure("登录已过期，请重新登录", 401);
}

export async function demoRequest(
  path: string,
  options: RequestInit = {},
): Promise<Response> {
  await delay();
  const url = new URL(path.replace(/^\//, ""), "https://demo.invalid/");
  const pathname = url.pathname.replace(/^\//, "").replace(/\/$/, "");
  const method = (options.method ?? "GET").toUpperCase();
  const payload = await body(options);
  const state = loadDemoState();
  const context: DemoRequestContext = {
    url,
    pathname,
    method,
    payload,
    state,
    ok: envelope,
    fail: failure,
    paginate: <T>(items: T[]) => {
      const page = Math.max(1, Number(url.searchParams.get("page") ?? 1));
      const pageSize = Math.min(
        100,
        Math.max(1, Number(url.searchParams.get("page_size") ?? 20)),
      );
      const totalPages = Math.max(1, Math.ceil(items.length / pageSize));
      const safePage = Math.min(page, totalPages);
      return {
        items: items.slice((safePage - 1) * pageSize, safePage * pageSize),
        pagination: {
          page: safePage,
          page_size: pageSize,
          total: items.length,
          total_pages: totalPages,
        },
      };
    },
  };

  const publicResponse = handlePublicAuth(context);
  if (publicResponse) return publicResponse;

  const denied = authorized();
  if (denied) return denied;

  for (const handler of protectedHandlers) {
    const response = handler(context);
    if (response) return response;
  }

  console.error(`Unhandled demo endpoint: ${method} ${pathname}`);
  return failure(`演示接口尚未覆盖：${method} ${pathname}`, 501);
}
