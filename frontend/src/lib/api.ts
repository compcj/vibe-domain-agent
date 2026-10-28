let csrfToken = "";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export function setCsrfToken(token: string) {
  csrfToken = token;
}

type Options = Omit<RequestInit, "body"> & { body?: unknown };

export async function api<T = unknown>(
  path: string,
  options: Options = {},
): Promise<T> {
  const headers = new Headers(options.headers);
  const method = options.method ?? "GET";
  if (!["GET", "HEAD", "OPTIONS"].includes(method))
    headers.set("X-CSRF-Token", csrfToken);
  const multipart = options.body instanceof FormData;
  if (options.body !== undefined && !multipart)
    headers.set("Content-Type", "application/json");
  const response = await fetch(`/api/v1${path}`, {
    ...options,
    method,
    headers,
    credentials: "same-origin",
    body: multipart
      ? (options.body as FormData)
      : options.body === undefined
        ? undefined
        : JSON.stringify(options.body),
  });
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try {
      const payload = (await response.json()) as {
        detail?: string | { loc: string[]; msg: string }[];
      };
      if (typeof payload.detail === "string") message = payload.detail;
      else if (Array.isArray(payload.detail))
        message = payload.detail
          .map((item) => `${item.loc.slice(1).join(".")}: ${item.msg}`)
          .join("；");
    } catch {
      /* Non-JSON upstream pages must never be rendered as HTML. */
    }
    if (response.status === 401 && path !== "/auth/login")
      window.dispatchEvent(new Event("session-expired"));
    throw new ApiError(response.status, message);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "操作未完成，请重试";
}
