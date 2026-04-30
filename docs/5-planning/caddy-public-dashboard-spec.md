# Public Dashboard Access — Caddy Reverse Proxy Spec

**Author:** QSE (Claude Code)
**Date:** 2026-04-12
**Status:** Draft for Director Review
**Goal:** Make the Streamlit dashboard accessible via HTTPS on a public domain, so the project owner, claude.ai conversations, and guests can view it without SSH tunnels.

---

## 1. What We're Building

A Caddy reverse proxy container that sits in front of the existing Streamlit dashboard, providing:
- **HTTPS** with automatic Let's Encrypt certificates
- **Password protection** via HTTP Basic Auth (Caddy-level, not bypassable)
- **Public URL** like `https://dashboard.yourdomain.com`

```
Internet                    EC2 (t3.medium)
                   ┌─────────────────────────────────┐
  browser ──────── │  caddy :443 (HTTPS + auth)      │
  (HTTPS)          │       │                          │
                   │       ▼                          │
                   │  dashboard :8501 (Streamlit)     │
                   │       │                          │
                   │       ▼                          │
                   │  db :5432 (PostgreSQL)            │
                   │                                   │
                   │  v2-kraken (trading bot)          │
                   └─────────────────────────────────┘
```

**What doesn't change:** Streamlit code, dashboard container, database, bot. Only the networking layer changes.

---

## 2. Prerequisites (User Action Required)

### 2.1 Domain Name

Buy a domain or use an existing one. A subdomain works fine.

**Recommended registrars:** Cloudflare (~$10/yr for .com), Namecheap, or any registrar.

**Example:** If you own `yourdomain.com`, create a subdomain `dash.yourdomain.com`.

### 2.2 Elastic IP

The current public IP (`44.238.14.228`) is a dynamic IP — it will change if the EC2 instance reboots. An Elastic IP is a static IP that persists.

**Steps (AWS Console):**
1. Go to EC2 > Elastic IPs > Allocate Elastic IP address
2. Associate it with your EC2 instance
3. Cost: Free while associated with a running instance

### 2.3 DNS A Record

Point your domain to the Elastic IP:

```
Type: A
Name: dash (or whatever subdomain)
Value: <your-elastic-ip>
TTL: 300
```

### 2.4 EC2 Security Group

Open port 443 (HTTPS) inbound. Optionally open port 80 (HTTP) for the automatic HTTP→HTTPS redirect.

**Steps (AWS Console):**
1. Go to EC2 > Security Groups > select your instance's security group
2. Edit inbound rules
3. Add: Type `HTTPS`, Port `443`, Source `0.0.0.0/0` (anywhere)
4. Add: Type `HTTP`, Port `80`, Source `0.0.0.0/0` (for redirect)
5. Save

**Keep port 22 (SSH) restricted** to your IP or a known range.

---

## 3. Implementation Plan

### 3.1 New Files (3)

| File | Purpose |
|------|---------|
| `docker/Caddyfile` | Caddy configuration (reverse proxy + auth + HTTPS) |
| `docker/Dockerfile.caddy` | Minimal — just copies Caddyfile into official Caddy image |
| `docker-compose.aws.yml` (modify) | Add caddy service, change dashboard port binding |

### 3.2 Caddyfile

```
{$DASHBOARD_DOMAIN:localhost} {
    basicauth * {
        {$DASHBOARD_USER:admin} {$DASHBOARD_PASSWORD_HASH}
    }

    reverse_proxy dashboard:8501

    encode gzip

    header {
        X-Content-Type-Options "nosniff"
        X-Frame-Options "DENY"
        Referrer-Policy "strict-origin-when-cross-origin"
    }

    log {
        output stdout
        format json
        level INFO
    }
}
```

**Key design decisions:**
- Domain, username, and password hash are injected via environment variables (from `.env`), not hardcoded
- `basicauth` at the Caddy level — every request requires credentials before reaching Streamlit
- `reverse_proxy dashboard:8501` — routes to the Streamlit container by Docker service name
- Security headers prevent clickjacking and MIME sniffing
- Gzip compression for faster page loads over the internet

### 3.3 Dockerfile.caddy

```dockerfile
FROM caddy:2-alpine
COPY Caddyfile /etc/caddy/Caddyfile
```

Two lines. The official Caddy image handles everything else (Let's Encrypt, OCSP stapling, HTTP/2).

### 3.4 docker-compose.aws.yml Changes

```yaml
  # MODIFY dashboard: remove host port binding (Caddy handles external access)
  dashboard:
    ports: []  # was "127.0.0.1:8501:8501" — now internal only
    # Everything else unchanged

  # ADD caddy service:
  caddy:
    build:
      context: ./docker
      dockerfile: Dockerfile.caddy
    container_name: caddy
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "2"
    environment:
      DASHBOARD_DOMAIN: "${DASHBOARD_DOMAIN}"
      DASHBOARD_USER: "${DASHBOARD_USER:-admin}"
      DASHBOARD_PASSWORD_HASH: "${DASHBOARD_PASSWORD_HASH}"
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - caddy_data:/data        # TLS certificates persist here
      - caddy_config:/config    # Caddy runtime config
    depends_on:
      - dashboard
    deploy:
      resources:
        limits:
          memory: 64M
    restart: unless-stopped

# ADD to volumes:
volumes:
  pg_data:
    external: true
    name: bottrader-aws_pg_data
  caddy_data:      # NEW — persists Let's Encrypt certs
  caddy_config:    # NEW — Caddy auto-config
```

### 3.5 .env Additions

Three new variables in `/opt/bot/.env`:

```bash
# Dashboard public access
DASHBOARD_DOMAIN=dash.yourdomain.com
DASHBOARD_USER=admin
DASHBOARD_PASSWORD_HASH=$2a$14$... (bcrypt hash from caddy hash-password)
```

**Generating the password hash:**
```bash
docker run --rm caddy:2-alpine caddy hash-password --plaintext 'your-password-here'
```

This outputs a bcrypt hash like `$2a$14$abc123...`. Paste the full hash into `.env`.

---

## 4. Resource Impact

| Resource | Current | With Caddy | Headroom |
|----------|---------|------------|----------|
| RAM | 259 MB | ~270 MB (+~11 MB) | ~3.5 GB |
| Disk | 15 GB | ~15.05 GB (+50 MB) | 4.75 GB |
| Containers | 3 | 4 | Fine |
| Open ports | 22 (SSH), 5432 (localhost) | + 80, 443 | Fine |

Caddy is extremely lightweight — Alpine-based image (~40 MB), ~11 MB RAM at runtime.

---

## 5. SSH Tunnel Access Preserved

The dashboard port binding changes from `127.0.0.1:8501:8501` (host-accessible) to no host port (Docker-internal only). Caddy handles all external access.

**If you still want SSH tunnel as a fallback** (recommended), keep the port binding:

```yaml
  dashboard:
    ports:
      - "127.0.0.1:8501:8501"  # SSH tunnel fallback
```

This is safe — the port is bound to localhost only, so it's not internet-accessible even with security group changes. The SSH tunnel command stays the same:
```bash
ssh -L 8501:localhost:8501 bottrader-aws -N
```

**Recommendation:** Keep the SSH tunnel port. It's a zero-cost safety net if Caddy or DNS has issues.

---

## 6. Security Considerations

| Concern | Mitigation |
|---------|------------|
| Dashboard exposed to internet | HTTP Basic Auth required for every request (Caddy-enforced) |
| Password in .env file | .env is gitignored; password stored as bcrypt hash, not plaintext |
| TLS certificates | Caddy auto-provisions and renews via Let's Encrypt (no manual cert management) |
| Database not exposed | PostgreSQL port remains `127.0.0.1:5432` — unchanged, not internet-reachable |
| Bot container not exposed | v2-kraken has no port bindings — unchanged, not internet-reachable |
| Brute force attacks | Caddy rate-limits by default; bcrypt hashing is slow by design |
| Dashboard is read-only | Currently view-only (no config editing). Future config editor will need additional safeguards |

### Future: Config Editor Security Note

When the Config Editor page is built (Session 3+), it will write YAML and restart containers. At that point, consider:
- A separate "admin" password for write operations
- Confirmation dialogs before destructive actions
- Audit logging of config changes

This is out of scope for this spec but noted for planning.

---

## 7. Implementation Steps

### Pre-implementation (User)
- [ ] Buy/choose domain name
- [ ] Allocate Elastic IP in AWS Console and associate with EC2
- [ ] Create DNS A record pointing domain to Elastic IP
- [ ] Update EC2 security group: open ports 80 and 443
- [ ] Wait for DNS propagation (5-30 minutes)

### Implementation (QSE)
1. Generate password hash via `caddy hash-password`
2. Add `DASHBOARD_DOMAIN`, `DASHBOARD_USER`, `DASHBOARD_PASSWORD_HASH` to `.env` on EC2
3. Create `docker/Caddyfile`
4. Create `docker/Dockerfile.caddy`
5. Modify `docker-compose.aws.yml` — add caddy service + volumes
6. Commit and push
7. Deploy: `docker compose -f docker-compose.aws.yml up -d --build caddy`
8. Verify: `curl -I https://dash.yourdomain.com` returns 401 (auth required)
9. Verify: browser access with credentials shows the dashboard

### Post-deploy verification
- [ ] HTTPS certificate issued (green lock in browser)
- [ ] Basic auth prompt appears before any dashboard content
- [ ] All dashboard pages load correctly through the proxy
- [ ] SSH tunnel still works as fallback
- [ ] Bot and database containers unaffected

---

## 8. Rollback Plan

If anything goes wrong:

```bash
# Stop caddy, restore dashboard direct access
ssh bottrader-aws "cd /opt/bot && docker compose -f docker-compose.aws.yml stop caddy"
```

The dashboard continues working via SSH tunnel regardless of Caddy's state. Caddy is purely additive — removing it returns to the pre-change state.

---

## 9. Open Questions for Director

1. **Domain choice:** Do you already own a domain, or do you need to purchase one? Any preference on registrar?

2. **Password sharing:** How do you want to share the dashboard password with guests? A single shared password, or individual accounts? (Caddy basic auth supports multiple users.)

3. **SSH tunnel retention:** Keep the SSH tunnel port as a fallback (recommended), or remove it to force all access through Caddy?
