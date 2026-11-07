// api.ts — a same-origin client that surfaces actionable backend errors.
export async function api<T>(path: string, body?: unknown): Promise<T> {
  // Never place the OpenAI key in frontend code; the server owns all model requests.
  const response = await fetch(`/api${path}`, {
    credentials: "same-origin",
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
  return response.json() as Promise<T>;
}
