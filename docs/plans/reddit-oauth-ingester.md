# Plan: authenticated Reddit ingester (OAuth)

## Problem

`clients/reddit.py` scrapes old.reddit.com HTML for post metadata. Reddit has since
locked this down for unauthenticated requests:

- `old.reddit.com/r/.../comments/{id}/` now returns a generic "Welcome to Reddit"
  login/signup interstitial (315KB modern React shell) instead of the post.
- `www.reddit.com/r/.../comments/{id}/.json` returns `403`.

Neither path works unauthenticated anymore. Result: 19 saved articles (as of
2026-08-07) contain bogus "Welcome to Reddit" content instead of real posts,
including from allowed subreddits like `r/LocalLLM` — this is not related to
the `reddit_subreddits` allowlist filter (already shipped), it's a separate
fetch-layer bug.

## Access model

A free Reddit account is sufficient — no paid plan needed for this volume:

- Register a **script**-type app at `reddit.com/prefs/apps` (personal-use apps
  run by the account owner, not a public-facing product).
- Free tier: ~100 queries/minute per app. Archivore fetches a handful of posts
  per run, nowhere near that ceiling.
- Paid/commercial tier only matters at the scale of apps making millions of
  calls/day (the 2023 Apollo/RIF pricing controversy) — not relevant here.

## Auth flow

Two paths depending on whether 2FA is enabled on the Reddit account:

**No 2FA** — use the OAuth "password" grant directly: POST `client_id`,
`client_secret`, `grant_type=password`, username, password to
`https://www.reddit.com/api/v1/access_token`. No browser/redirect involved;
the redirect URI required at app-registration time is an unused placeholder
(e.g. `http://localhost:8080`).

**2FA enabled (likely, and recommended)** — password grant can't handle the
2FA challenge, so a one-time "authorization_code" flow is needed instead:

1. Build an authorize URL: `https://www.reddit.com/api/v1/authorize?client_id=...&response_type=code&redirect_uri=http://localhost:8080&duration=permanent&scope=read&state=...`
2. Open it in a browser, log in, click "allow".
3. Reddit redirects to `redirect_uri` with `?code=XXXX` in the query string.
   Nothing needs to be listening there — the code is visible in the browser's
   address bar even if the page fails to load; copy it out manually.
4. Exchange the code once for a **permanent refresh token** (this is why
   `duration=permanent` matters in step 1) via a POST to the same
   `access_token` endpoint with `grant_type=authorization_code`.
5. From then on, use the refresh token to mint short-lived access tokens.
   No more browser interaction ever needed. The redirect URI is never touched
   again after this one-time step.

## Implementation

### 1. One-off setup helper

A small script (or `archivore reddit-auth` CLI command) that:

- Prints the authorize URL to open in a browser.
- Prompts the user to paste the redirected URL (or just the `code` param).
- Exchanges the code for a refresh token.
- Prints the refresh token for the user to paste into their config manually —
  never auto-write it, same handling as `smtp_password` (credential, config
  file should be `chmod 600`).

### 2. Config additions (`archivore/config.py`)

```python
reddit_client_id: str | None = None
reddit_client_secret: str | None = None
reddit_refresh_token: str | None = None
reddit_user_agent: str = "archivore:v0.2 (personal use script)"
```

Reddit requires a distinctive User-Agent
(`<platform>:<app-id>:<version> (by /u/<username>)`) — generic/default
User-Agents get rate-limited harder.

### 3. `clients/reddit.py` rewrite

- Replace HTML scraping with authenticated calls to `oauth.reddit.com` (the
  authenticated API host, distinct from the blocked public `www.reddit.com`
  host).
- Cache the short-lived access token in-process (same pattern as
  `clients/http.py`'s `@lru_cache` session, but with expiry tracking since
  access tokens last ~1 hour) — mint a new one via the refresh token grant
  when expired.
- Fetch `GET https://oauth.reddit.com/r/{subreddit}/comments/{post_id}` with
  `Authorization: Bearer <token>` and the custom User-Agent.
- Parse the JSON listing directly: `title`, `is_self`, `selftext`, `url` are
  all present in the post's `data` — no more HTML scraping/regex needed for
  title extraction or link-post detection.
- Keep returning `ResolvedItem` with the same shape callers already expect —
  this is a fetch-layer swap, not an interface change.

### 4. Defensive guard (do this regardless of the above)

Detect the login-gate response pattern (e.g. `<title>Welcome to Reddit</title>`
or similar) in whatever fetch path is active, and treat it as a failure
(`status='failed'`, retryable) rather than silently saving bogus content. This
is cheap insurance against Reddit changing behavior again in the future.

### 5. Cleanup of existing bad data

Once the OAuth path works, find the queue rows behind the 19 known-bad
"Welcome to Reddit" files and reset them (`title = NULL`, clear `filename`)
so `phase1_resolve` picks them up again on the next `archivore run` and
re-fetches real content.

### 6. Tests

- Token caching/refresh logic, mocked (no real network calls in tests).
- The "Welcome to Reddit" gate-page detection guard.

## Open questions for whoever picks this up

- Confirm current Reddit API terms/rate limits before implementing — this
  plan is based on info as of ~2025 and Reddit's policies have shifted more
  than once.
- Decide whether the one-off OAuth setup lives as a throwaway script or a
  permanent `archivore reddit-auth` subcommand (leaning permanent, since the
  refresh token could need re-minting if revoked).
