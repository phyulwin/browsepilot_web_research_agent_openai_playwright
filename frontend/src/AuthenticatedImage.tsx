// AuthenticatedImage.tsx — fetch private screenshots without exposing tokens in image URLs.
import { useEffect, useState } from "react";
import { apiFetch } from "./api";

export function AuthenticatedImage({ path, alt }: { path: string; alt: string }) {
  // Revoke each object URL and cancel outdated downloads when the screenshot changes.
  const [source, setSource] = useState<string>();
  useEffect(() => {
    const controller = new AbortController();
    let url: string | undefined;
    setSource(undefined);
    async function load() {
      try {
        const response = await apiFetch(path, { signal: controller.signal });
        if (!response.ok) return;
        const blob = await response.blob();
        if (controller.signal.aborted) return;
        url = URL.createObjectURL(blob);
        setSource(url);
      } catch { /* A later screenshot or reconnect can retry a failed download. */ }
    }
    void load();
    return () => { controller.abort(); if (url) URL.revokeObjectURL(url); };
  }, [path]);
  return source ? <img src={source} alt={alt} /> : <span>{alt}</span>;
}
