// Passes the backend's Server-Sent Events stream through unbuffered.
import { FASTAPI_URL } from "../../fastapi";

export async function GET(request: Request) {
  const { search } = new URL(request.url);
  const headers: Record<string, string> = { accept: "text/event-stream" };
  const lastEventId = request.headers.get("last-event-id");
  if (lastEventId) headers["last-event-id"] = lastEventId;

  let upstream: Response;
  try {
    upstream = await fetch(`${FASTAPI_URL}/api/mission/stream${search}`, { cache: "no-store", headers, signal: request.signal });
  } catch {
    return Response.json({ detail: "Backend service is unreachable" }, { status: 502 });
  }
  if (!upstream.ok || !upstream.body) {
    return new Response(await upstream.text(), { status: upstream.status, headers: { "content-type": "application/json" } });
  }
  return new Response(upstream.body, {
    headers: { "content-type": "text/event-stream", "cache-control": "no-cache, no-transform", "x-accel-buffering": "no" },
  });
}
