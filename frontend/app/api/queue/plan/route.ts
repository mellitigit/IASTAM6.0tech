import { forward } from "../../fastapi";

export async function POST(request: Request) {
  return forward("/api/queue/plan", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: await request.text(),
  });
}
