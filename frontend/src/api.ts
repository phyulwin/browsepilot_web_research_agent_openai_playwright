// api.ts — authenticated local and cross-origin requests, exports, and streaming progress.
const configuredOrigin = import.meta.env.VITE_API_URL || "";
const origin = configuredOrigin.endsWith("/") ? configuredOrigin.slice(0, -1) : configuredOrigin;
const tokenKey = `browsepilot-access:${origin}`;

// Keep the short-lived workspace token in this tab; never put credentials in URLs.
export async function apiFetch(path: string, options: RequestInit = {}): Promise<Response> {
  const headers = new Headers(options.headers);
  const token = sessionStorage.getItem(tokenKey);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(`${origin}/api${path}`, { ...options, headers, credentials: "same-origin" });
  if (response.status === 401) sessionStorage.removeItem(tokenKey);
  return response;
}

// Read authenticated SSE with reconnects and full snapshots, without URL tokens.
export function subscribe(path: string, snapshot: (data: unknown) => boolean, connected: (value: boolean) => void) {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  async function run() {
    let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
    try {
      const response = await apiFetch(path, { signal: controller.signal });
      if (!response.ok || !response.body) {
        connected(false);
        if (response.status === 401 || response.status === 404) controller.abort();
        throw new Error("Live progress unavailable");
      }
      connected(true);
      reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (!controller.signal.aborted) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let end: number;
        while ((end = buffer.indexOf("\n\n")) >= 0) {
          const frame = buffer.slice(0, end);
          buffer = buffer.slice(end + 2);
          const lines = frame.split("\n");
          if (!lines.includes("event: snapshot")) continue;
          const data = lines.filter(line => line.startsWith("data: ")).map(line => line.slice(6)).join("\n");
          if (!snapshot(JSON.parse(data))) controller.abort();
        }
      }
    } catch {
      if (!controller.signal.aborted) connected(false);
    } finally {
      await reader?.cancel().catch(() => undefined);
      if (!controller.signal.aborted) {
        connected(false);
        timer = setTimeout(() => void run(), 2000);
      }
    }
  }
  void run();
  return () => { controller.abort(); clearTimeout(timer); };
}

export async function api<T>(path: string, body?: unknown): Promise<T> {
  // Never place the OpenAI key in frontend code; the server owns all model requests.
  const response = await apiFetch(path, {
    ...(body !== undefined
      ? {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }
      : {}),
  });
  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ detail: "The server could not complete this request." }));
    throw new Error(
      typeof error.detail === "string"
        ? error.detail
        : "Please check your request and try again.",
    );
  }
  const result = await response.json();
  if (path === "/login" && result.access_token) sessionStorage.setItem(tokenKey, result.access_token);
  if (path === "/logout") sessionStorage.removeItem(tokenKey);
  return result as T;
}
