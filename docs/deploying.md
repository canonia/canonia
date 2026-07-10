# Building & deploying the site — READ BEFORE YOU EXPOSE IT

`canonia build` generates a **self-contained** static site — one HTML page per
concept (rendered body, references, backlinks, provenance, redirect/deprecation
banners), a domain index, and client-side search:

```bash
canonia build                    # -> <canon>/site/
canonia build --out ./public     # custom output dir
open <canon>/site/index.html     # works from file:// — no server needed
```

It has **zero external requests** (inline CSS/JS, no CDN) and is theme-aware.

## ⚠ The site has NO access control

This is the single most important thing on this page. The generated site is plain
static HTML with **no authentication of any kind**. The open core deliberately ships
**without access control**, so **whoever can reach the URL can read every
concept** — including anything sensitive in your canon. **You** are responsible for
restricting access at the network / edge layer.

Treat the canon site as a **private service**. (If your canon holds infra
conventions like "private services behind a VPN" or "one ingress at the edge",
this is exactly where you apply them.)

### Option A — Tailnet only (simplest, strongest for solo use)

Serve it bound to loopback and expose it over Tailscale. Only your tailnet devices
can see it — no public DNS, no open port.

```bash
cd <canon>/site
python3 -m http.server 8000 --bind 127.0.0.1   # --bind 127.0.0.1 is MANDATORY
tailscale serve --bg 8000                        # reachable only on your tailnet
```

`python3 -m http.server` **binds `0.0.0.0` (all interfaces, public) by default** —
the `--bind 127.0.0.1` is what keeps it private. Verify with `ss -tlnp | grep 8000`
(you want `127.0.0.1:8000`, never `0.0.0.0:8000`).

### Option B — Cloudflare Access (browser access from anywhere, no VPN)

Front it with your reverse proxy on a hostname, then put **Cloudflare Access (Zero
Trust)** ahead of it with an allow-policy of just your email/identity. Cloudflare
authenticates every request before it reaches the origin. Best combined with a
**Cloudflare Tunnel** so the origin isn't directly reachable from the internet at
all.

### Option C — Reverse-proxy auth middleware

If a proxy already fronts the box (e.g. Traefik), attach an auth middleware to the
router — `basicAuth` (quick) or `forwardAuth` / SSO (proper). Without the middleware,
**the router must not exist** for this service.

## Do NOT

- Run `python -m http.server` **without** `--bind 127.0.0.1` (it's public otherwise).
- Add a public DNS record + a proxy route with **no** auth middleware.
- Commit or publish the generated `site/` directory. `canonia init` gitignores
  `site/` and `.canonia/`; keep it that way, and keep the **canon repo private**.
- Assume "nobody knows the URL" is protection. It isn't.

## Verify it's private

From a device that is **not** on your tailnet and **not** authenticated, the URL must
**refuse the connection or present an auth challenge — never render a page.** Check
this every time you change how it's served.

## Why an auth-capable edge

The static site is designed to sit behind an **auth-capable edge** — that is the
intended access model for the open core, not a stopgap. The edge owns
authentication and can carry per-identity policy (scoping **LLM identities too**,
not just humans). The edge is your control. Use it.
