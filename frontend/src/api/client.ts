/** Same-origin JSON client: session cookie + Django CSRF header on unsafe methods. */

export class ApiError extends Error {
  readonly status: number;
  readonly data: unknown;

  constructor(status: number, data: unknown, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.data = data;
  }
}

type Params = Record<string, string | number | boolean | null | undefined>;

interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  body?: unknown;
  params?: Params;
}

let unauthorizedHandler: (() => void) | null = null;

/** Called whenever the API answers 401 (e.g. the session expired). */
export function setUnauthorizedHandler(handler: () => void) {
  unauthorizedHandler = handler;
}

export function readCookie(name: string, cookie = document.cookie): string | null {
  for (const part of cookie.split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key === name) return decodeURIComponent(rest.join("="));
  }
  return null;
}

/** Turn a DRF error payload into one readable sentence. */
export function errorMessage(data: unknown, status: number): string {
  if (data && typeof data === "object") {
    const record = data as Record<string, unknown>;
    if (typeof record.detail === "string") return record.detail;
    for (const [field, value] of Object.entries(record)) {
      const first = Array.isArray(value) ? value[0] : value;
      if (typeof first === "string") {
        return field === "non_field_errors" ? first : `${field}: ${first}`;
      }
    }
  }
  if (status === 0) return "Network error: is the server running?";
  return `Request failed (HTTP ${status})`;
}

export function buildUrl(path: string, params?: Params): string {
  const url = new URL(`/api/${path.replace(/^\/+/, "")}`, "http://placeholder");
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, String(value));
  }
  return url.pathname + url.search;
}

export async function api<T>(path: string, { method = "GET", body, params }: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET") headers["X-CSRFToken"] = readCookie("csrftoken") ?? "";

  let response: Response;
  try {
    response = await fetch(buildUrl(path, params), {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "same-origin",
    });
  } catch {
    throw new ApiError(0, null, errorMessage(null, 0));
  }

  if (response.status === 204) return undefined as T;
  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 401) unauthorizedHandler?.();
    throw new ApiError(response.status, data, errorMessage(data, response.status));
  }
  return data as T;
}
