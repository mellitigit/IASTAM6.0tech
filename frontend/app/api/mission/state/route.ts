import { forward } from "../../fastapi";

export async function GET(request: Request) {
  const { search } = new URL(request.url);
  return forward(`/api/mission/state${search}`);
}
