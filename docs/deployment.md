# Deployment

How the live demo is hosted, how to reproduce the deployment, and how to check and debug it. Everything runs
on free tiers.

**Live:** website https://frontend-flame-seven-10.vercel.app · API https://backend-alpha-puce-86.vercel.app

## Architecture

```
Browser ──► Vercel project "frontend" (Next.js, root dir frontend/)
               │  NEXT_PUBLIC_API_URL
               ▼
            Vercel project "backend" (FastAPI + LangGraph, root dir backend/, entrypoint app.py)
               │                     │                     │
               ▼                     ▼                     ▼
         MotherDuck md:tpcds   Neon Postgres          Groq (+ Cerebras, optional)
         (warehouse,           (LangGraph checkpoints: (LLMs)
          read-only token)      paused clarifications)
```

Ollama is **local-development only**. Production never sets `OLLAMA_BASE_URL`, and `localhost` on Vercel
would point at Vercel's own machine anyway.

## Accounts (all free)

| Service | Used for | What you need from it |
|---|---|---|
| GitHub | Source; Vercel deploys from `main` | the repo |
| Vercel | Hosts both projects | sign in with GitHub |
| MotherDuck | Hosts the warehouse | a **read/write** token for the upload, a **read scaling** (read-only) token for the app |
| Neon | Stores conversation state | the **pooled** connection string (host contains `-pooler`) |
| Groq | LLMs (primary) | API key; allow `openai/gpt-oss-120b` and `openai/gpt-oss-20b` at **both** the organisation and the project level in Groq's settings |
| Cerebras | LLMs (backup, optional) | API key |

## Checklist

### 1. Data and state (from your machine)
- [ ] `backend/.env` has `MOTHERDUCK_TOKEN` (read/write) and `DATABASE_URL` (Neon, pooled). Type values rather
      than pasting, or check for stray whitespace.
- [ ] `cd backend && uv run sqlagent upload-motherduck`. This copies `data/tpcds_sf1.duckdb` to `md:tpcds`.
      Add `--replace` to overwrite.
- [ ] The Neon tables are created automatically on the backend's first start (`checkpoints`,
      `checkpoint_writes`, …).

### 2. MotherDuck read-only token
- [ ] MotherDuck → Settings → Access Tokens → create a **read scaling** token. This is the one the deployed app
      uses. The read/write token can modify data (verified); never put it on Vercel.

### 3. Backend project on Vercel
- [ ] Add New → Project → import the repo → **Root Directory `backend`**.
- [ ] Environment variables. **Type** the values (a pasted tab once broke the deploy; challenges log #18):

  | Name | Value |
  |---|---|
  | `GROQ_API_KEY` | Groq key |
  | `MOTHERDUCK_TOKEN` | the **read scaling** token |
  | `WAREHOUSE_URL` | `md:tpcds?saas_mode=true` |
  | `DATABASE_URL` | Neon pooled connection string |
  | `CORS_ORIGINS` | `["https://<frontend-domain>"]`; use `["http://localhost:3000"]` until the frontend exists |
  | `CEREBRAS_API_KEY` | *(optional, step 6)* |

  Never set `OLLAMA_BASE_URL` here.
- [ ] Deploy. `https://<backend-domain>/api/health` must return `"status":"ok"`.

### 4. Frontend project on Vercel
- [ ] Add New → Project → the same repo → **Root Directory `frontend`**.
- [ ] Environment variable `NEXT_PUBLIC_API_URL` = `https://<backend-domain>`, with no trailing slash. It's
      baked in at build time, so redeploy the frontend if it changes.
- [ ] Deploy and note the domain.

### 5. Connect them
- [ ] Backend → Settings → Environment Variables → `CORS_ORIGINS` = `["https://<frontend-domain>"]` → Redeploy.
- [ ] Open the website and ask "What were the top 3 product categories by revenue in 2002?".

### 6. Backup LLM provider: Cerebras (optional, recommended)

Without it, if Groq is down or its free daily quota (about 200k tokens/day for gpt-oss-120b, roughly 25–35
questions) runs out, visitors see "AI providers are busy". The code already supports Cerebras. It just needs a
key.
- [ ] Create a free account at https://cloud.cerebras.ai and create an **API key**.
- [ ] Optional local check: add `CEREBRAS_API_KEY=...` to `backend/.env`, then
      `uv run sqlagent ask "How many customers do we have?"`.
- [ ] Vercel backend → Environment Variables → add `CEREBRAS_API_KEY` → Redeploy.
- [ ] `/api/health` now lists `cerebras:gpt-oss-120b` after the Groq models. If Cerebras has renamed the model,
      set `SQL_MODELS` / `ROUTER_MODELS` explicitly (format in `backend/.env.example`).

### 7. Repository hygiene
- [ ] GitHub → Settings → General → enable **Automatically delete head branches**.

## Verifying a deployment

```bash
curl https://<backend-domain>/api/health          # {"status":"ok","models":{...}}
curl -X OPTIONS https://<backend-domain>/api/chat \
  -H "Origin: https://<frontend-domain>" -H "Access-Control-Request-Method: POST" -i   # 200 + allow-origin
```

Pull requests get **preview deployments** automatically. They're behind Vercel login, so open them in a browser
where you're signed in to Vercel.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `/api/health` shows `"status":"error"` with a `stage` | Startup failed. The message (with secrets redacted) says where | `warehouse`: check `WAREHOUSE_URL` / `MOTHERDUCK_TOKEN`. `checkpointer`: check `DATABASE_URL`. `catalog`: deployment files missing |
| HTTP 500 `FUNCTION_INVOCATION_FAILED` | A crash before the app could report anything | Vercel → Logs → open the red "lifespan" traceback and read the **last** line. Ignore `vc_handler` import errors, which are a side effect |
| Website loads but every question fails | `CORS_ORIGINS` doesn't contain the exact frontend origin | Fix the value (exact `https://` origin, no trailing slash) and redeploy the backend |
| "AI providers are busy" | Groq rate limit or daily quota | Wait, or add Cerebras (step 6) |
| Groq 403 `model_permission_blocked_*` | The model isn't allowed in Groq's org or project settings | Allow it at both levels |
| MotherDuck "DuckDB version not supported" | DuckDB was upgraded past what MotherDuck supports | Keep the `duckdb>=1.5.6,<1.6` pin (challenges log #17) |

Every deployment problem hit so far is written up in [challenges-and-fixes.md](challenges-and-fixes.md),
#17–19.
