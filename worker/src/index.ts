import type { Env } from "./types";

export default {
  async fetch(_request: Request, _env: Env): Promise<Response> {
    return Response.json({ error: "not found" }, { status: 404 });
  },
};
