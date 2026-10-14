import { forward } from "../../fastapi";

export async function GET() {
  return forward("/api/downlink/samples");
}
