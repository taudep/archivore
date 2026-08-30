import type { Env } from "./types";

function checkAuth(request: Request, env: Env): boolean {
  return request.headers.get("Authorization") === `Bearer ${env.QUEUE_API_TOKEN}`;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    if (!checkAuth(request, env)) {
      return Response.json({ error: "unauthorized" }, { status: 401 });
    }
    return Response.json({ error: "not found" }, { status: 404 });
  },
};
