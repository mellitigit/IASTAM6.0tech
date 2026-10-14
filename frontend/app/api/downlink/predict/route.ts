import { forward } from "../../fastapi";

export async function POST(request: Request) {
  return forward("/api/downlink/predict", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: await request.text(),
  });
}
