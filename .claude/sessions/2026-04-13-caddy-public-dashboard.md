# Session: Caddy Public Dashboard Deployment

**Date:** 2026-04-13
**Duration:** ~45 minutes
**Goal:** Make the Streamlit dashboard publicly accessible via HTTPS (P2 backlog item)

---

## Git Summary

**Commits:** 2
- `5f9470a` — feat(infra): Add Caddy reverse proxy for public dashboard access
- `4ab4de5` — docs: Update SYSTEM_CONTEXT with Caddy deployment and bottrader.trade

**Files changed:** 4 (3 added, 1 modified)
- `docker/Caddyfile` — **added** — Caddy config (HTTPS + Basic Auth + reverse proxy to dashboard)
- `docker/Dockerfile.caddy` — **added** — 2-line Dockerfile (copies Caddyfile into caddy:2-alpine)
- `docker-compose.aws.yml` — **modified** — added caddy service + caddy_data/caddy_config volumes
- `docs/SYSTEM_CONTEXT.md` — **modified** — architecture diagram, stack, service count, codebase map, changelog

**Final status:** Clean (all session work committed and pushed)

---

## Key Accomplishments

1. **Domain registered:** `bottrader.trade` via Cloudflare Registrar ($4/yr, renews $5.18/yr)
2. **DNS configured:** A record pointing to Elastic IP `44.238.14.228` (Cloudflare DNS, proxy OFF)
3. **Security group updated:** Ports 80+443 opened on `bottrader-ec2-sg` in us-west-2
4. **Caddy container deployed:** 4th Docker service with auto Let's Encrypt TLS + HTTP Basic Auth
5. **Dashboard live at:** https://bottrader.trade (credentials: denfish / [password in .env as bcrypt hash])
6. **SSH tunnel preserved:** `127.0.0.1:8501` still works as fallback
7. **Documentation updated:** SYSTEM_CONTEXT.md, MEMORY.md, open_work_items.md, ec2_maintenance.md

---

## Problems Encountered & Solutions

### 1. Bcrypt hash `$` signs eaten by Docker Compose
- **Problem:** The bcrypt password hash (`$2a$14$v8sMj9n...`) in `.env` had its `$` signs interpreted as variable references by Docker Compose. The hash arrived corrupted inside the container.
- **Solution:** Escaped all `$` as `$$` in the `.env` file: `$$2a$$14$$v8sMj9n...`
- **Lesson:** Always escape `$` in `.env` values that contain literal dollar signs when using Docker Compose.

### 2. Let's Encrypt ACME challenge timeout
- **Problem:** TLS-ALPN-01 and HTTP-01 challenges both failed with "Timeout during connect (likely firewall problem)" because the security group ports weren't open yet.
- **Solution:** Once ports 80+443 were opened on the correct security group, restarted Caddy to reset its retry backoff. Certificate issued successfully on next attempt.
- **Lesson:** Caddy enters exponential backoff on ACME failures. A simple restart resets the backoff timer.

### 3. Wrong AWS region for security group
- **Problem:** User initially created a security group in us-east-1 instead of us-west-2 (where the EC2 instance lives).
- **Solution:** Deleted the us-east-1 group, added rules to the existing `bottrader-ec2-sg` in us-west-2.
- **No impact** — the wrong-region group was empty and never attached to anything.

---

## Configuration Changes

### EC2 `.env` additions (3 new variables)
```
DASHBOARD_DOMAIN=bottrader.trade
DASHBOARD_USER=denfish
DASHBOARD_PASSWORD_HASH=$$2a$$14$$v8sMj9nVvzJ5uJY.vxQR8.YKcDIa.i8txvoZDNvItGIuZuMdKg40.
```

### docker-compose.aws.yml changes
- Added `caddy` service (ports 80+443, 64MB limit, depends_on dashboard)
- Added `caddy_data` and `caddy_config` named volumes (persist TLS certs)
- Dashboard port binding unchanged (`127.0.0.1:8501:8501` — SSH tunnel fallback)

### AWS Security Group (`bottrader-ec2-sg`)
- Added inbound: HTTP (80) from 0.0.0.0/0
- Added inbound: HTTPS (443) from 0.0.0.0/0

### Cloudflare DNS
- A record: `bottrader.trade` → `44.238.14.228` (proxy OFF / DNS only)

---

## Deployment Steps Taken

1. Generated bcrypt password hash on EC2: `docker run --rm caddy:2-alpine caddy hash-password`
2. Added 3 env vars to `/opt/bot/.env` (with `$$` escaping for hash)
3. Created `docker/Caddyfile` and `docker/Dockerfile.caddy`
4. Modified `docker-compose.aws.yml` with caddy service
5. Committed, pushed to GitHub, pulled on EC2
6. `docker compose -f docker-compose.aws.yml up -d --build caddy`
7. Fixed bcrypt hash escaping, recreated caddy container
8. Restarted caddy after security group ports opened to reset ACME backoff
9. Verified: HTTPS 401 without auth, 200 with correct credentials

---

## What Wasn't Completed

- Nothing — all planned work for this session was completed.

---

## Tips for Future Developers

1. **Password hash escaping:** If you regenerate the Basic Auth password, remember to escape `$` as `$$` in the `.env` file for Docker Compose compatibility.
2. **Caddy cert renewal:** Certs auto-renew. If renewal fails, check security group ports 80+443 are still open, then restart caddy.
3. **Cloudflare proxy must be OFF:** The DNS record must be grey-cloud (DNS only). Orange-cloud (proxied) would intercept TLS and break Caddy's Let's Encrypt flow.
4. **Caddy is additive:** Stopping caddy doesn't affect the dashboard — SSH tunnel still works on 8501.
5. **Config Editor security:** When building the Config Editor page (P4), add additional auth since it will write YAML and restart containers.
