export interface Env {
  DB: D1Database;
  QUEUE_API_TOKEN: string;
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
