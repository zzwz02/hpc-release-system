# web/ — React/Vite/TypeScript frontend

Phase 3 frontend rewrite.  Talks to the frozen Phase-2 FastAPI backend.

---

## Development

### Prerequisites

- Node.js ≥ 18
- Backend running: `python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1`

### Proxy caveat (this machine)

This host sets `http_proxy`/`https_proxy` env vars that hijack localhost traffic
and return `503 Proxy-Connection: close`.  Before starting the dev server, clear
the proxy for localhost:

```sh
export NO_PROXY=localhost,127.0.0.1
export no_proxy=localhost,127.0.0.1
```

Vite's `server.proxy` is already configured to forward `/api` →
`http://127.0.0.1:8000`, but those env vars must be unset (or overridden via
`no_proxy`) so Node's http client does not route the proxy request through the
system proxy.

### Start dev server

```sh
cd web
NO_PROXY=localhost,127.0.0.1 npm run dev
# App available at http://localhost:5173
```

---

## Build

```sh
cd web
npm run build
```

Output lands in `web/web_dist/` (configured via `vite.config.ts` `build.outDir`).

To verify the build is clean (TypeScript + lint + tests):

```sh
npm run build   # tsc strict + vite bundle
npm run lint    # eslint
npx vitest run  # unit tests
```

---

## Phase-4 cutover — FastAPI StaticFiles

In Phase 4 the app will be deployed as a **single process**: FastAPI will serve
the compiled React app via `StaticFiles` (already stubbed in `app/main.py`):

```python
# app/main.py (Phase-4 activation)
app.mount("/", StaticFiles(directory="web/web_dist", html=True), name="static")
```

Until then (`rewrite/fastapi-react` branch), run the Vite dev server separately
and rely on its `/api` proxy to the backend.  The legacy `index.html` continues
to work unchanged.

Steps for Phase-4 cutover:
1. `cd web && npm run build` → produces `web/web_dist/`
2. Remove the `StaticFiles` guard in `app/main.py` (or ensure `web_dist/` exists)
3. Boot a single uvicorn process; both the API and the React app are served from it

---

## Architecture notes

- **R2 refresh model**: data moves only on explicit RefreshBar click, nav mount,
  or post-mutation `invalidateQueries`.  No background polling anywhere except the
  QA AI-analysis job (1 s interval, cancelled on unmount and release change).
- **Auth**: cookie-based (`hpc_session` HttpOnly).  All fetches use
  `credentials: 'include'`.  A 401 response clears the user and shows the login page.
- **Timezone**: stored times are naive Beijing strings.  The frontend displays them
  as-is with zero offset — no `+8` math.
- **Markdown**: `src/components/Markdown.tsx` is the **sole** `dangerouslySetInnerHTML`
  sink for Markdown content.  All other components must use `<Markdown>` rather
  than setting innerHTML directly.
