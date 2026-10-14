// Server-side helper for route handlers that proxy to the FastAPI backend (FASTAPI_URL, see .env.example).
export const FASTAPI_URL = process.env.FASTAPI_URL ?? "http://localhost:8000";

export async function forward(path: string, init?: RequestInit): Promise<Response> {
  let upstream: Response;
  try {
    upstream = await fetch(`${FASTAPI_URL}${path}`, { cache: "no-store", ...init });
  } catch {
    return Response.json({ detail: "Backend service is unreachable" }, { status: 502 });
  }
  return new Response(await upstream.text(), {
    status: upstream.status,
    headers: { "content-type": upstream.headers.get("content-type") ?? "application/json" },
  });
}
