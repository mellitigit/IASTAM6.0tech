import { forward } from "../../fastapi";

export async function POST(request: Request) {
  let form: FormData;
  try {
    form = await request.formData(); // fields: patch (.npy file), battery_pct
  } catch {
    return Response.json({ detail: "Expected multipart form data" }, { status: 400 });
  }
  return forward("/api/diagnostics/health", { method: "POST", body: form });
}
