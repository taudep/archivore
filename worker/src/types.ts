import type { D1Migration } from "@cloudflare/vitest-pool-workers";

// The real bindings for this Worker, as configured in wrangler.toml. This is
// declared under a non-"Env" name so it can be referenced from within the
// `declare global { namespace Cloudflare { interface Env ... } }` block below
// without the `interface Env extends Env` self-reference TypeScript rejects
// (this mirrors the `__BaseEnv_Env` indirection `wrangler types` itself
// generates for the same reason).
interface WorkerBindings {
  DB: D1Database;
  QUEUE_API_TOKEN: string;
}

export interface Env extends WorkerBindings {}

declare global {
  namespace Cloudflare {
    // Merge the real, deploy-time bindings into the global `Cloudflare.Env`
    // interface that `cloudflare:test`'s `env` export (and other ambient
    // Workers types) are typed against. Without this, `Cloudflare.Env` is
    // effectively empty and every `env.DB` / `env.QUEUE_API_TOKEN` access in
    // tests fails to type-check, even though the binding is real at runtime.
    interface Env extends WorkerBindings {}

    // TEST_MIGRATIONS is a Miniflare-only binding injected by
    // vitest.config.ts's `miniflare.bindings` (see readD1Migrations()); it is
    // not part of the real deployed Env from wrangler.toml, so it's added
    // here rather than to WorkerBindings/the local Env export.
    interface Env {
      TEST_MIGRATIONS: D1Migration[];
    }
  }
}

export interface ClaimRequestItem {
  item_id: string;
  source: string;
  comments_url: string;
  article_url: string | null;
}

export interface ClaimResultItem {
  item_id: string;
  claimed: boolean;
  status: string;
  retries: number;
}

export interface CompleteRequestItem {
  item_id: string;
  status: string;
  title: string | null;
  is_selfpost: boolean | null;
  filename: string | null;
  last_error: string | null;
}
