import { forward } from "../../fastapi";

export async function GET(request: Request) {
  const { search } = new URL(request.url);
  return forward(`/api/mission/commands${search}`);
}

export async function POST(request: Request) {
  return forward("/api/mission/commands", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: await request.text(),
  });
}
