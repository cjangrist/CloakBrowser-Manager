<p align="center">
<img src="https://i.imgur.com/cqkp6fG.png" width="500" alt="CloakBrowser">
</p>

<h3 align="center">Browser Profile Manager for CloakBrowser</h3>

<p align="center">
Create, manage, and launch isolated browser profiles with unique fingerprints.<br>
Free, self-hosted alternative to Multilogin, GoLogin, and AdsPower.
</p>

<p align="center">
<a href="https://github.com/CloakHQ/CloakBrowser"><img src="https://img.shields.io/github/stars/cloakhq/cloakbrowser?label=CloakBrowser" alt="Stars"></a>
<a href="https://hub.docker.com/r/cloakhq/cloakbrowser-manager"><img src="https://img.shields.io/docker/pulls/cloakhq/cloakbrowser-manager?label=docker&logo=docker&logoColor=white" alt="Docker Pulls"></a>
<a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue" alt="License"></a>
</p>

---

<p align="center">
<img src="https://i.imgur.com/twdX81Q.png" width="800" alt="CloakBrowser Manager — Browser View">
<br>
<img src="https://i.imgur.com/XFYn1qY.png" width="800" alt="CloakBrowser Manager — Profile Settings">
</p>

Each profile is an isolated CloakBrowser instance with its own fingerprint, proxy, cookies, and session data. Profiles persist across restarts. Everything runs in one Docker container.

```bash
docker run -p 8080:8080 -v cloakprofiles:/data cloakhq/cloakbrowser-manager
```

Or build from source:

```bash
git clone https://github.com/CloakHQ/CloakBrowser-Manager.git
cd CloakBrowser-Manager
docker compose up --build
```

Open [http://localhost:8080](http://localhost:8080) in your browser. Create a profile. Click Launch. Done.

> **Early alpha** — this project is under active development. Expect bugs. If you find one, please [open an issue](https://github.com/CloakHQ/CloakBrowser-Manager/issues).

## Why Not Just Use a VPN?

A VPN only changes your IP. Incognito only clears cookies. Chrome profiles share the same hardware fingerprint underneath. Platforms use 50+ signals to link your accounts — canvas, WebGL, audio, GPU, fonts, screen size, timezone.

Each CloakBrowser profile generates a completely different device identity. To the website, each profile looks like a different computer.

| Solution | What it changes | Accounts linked? |
|----------|----------------|-----------------|
| VPN | IP address only | Yes — same fingerprint |
| Incognito | Clears cookies | Yes — same fingerprint |
| Chrome profiles | Separate bookmarks/cookies | Yes — same hardware fingerprint |
| **CloakBrowser** | **Everything — full device identity per profile** | **No** |

## Features

- **Profile management** — create, edit, delete browser profiles with unique fingerprints
- **Per-profile settings** — fingerprint seed, proxy, timezone, locale, user agent, screen size, platform
- **One-click launch/stop** — each profile runs as an isolated CloakBrowser instance
- **Session persistence** — cookies, localStorage, and cache survive browser restarts
- **In-browser viewing** — interact with launched browsers via noVNC, directly in the web GUI
- **Playwright/Puppeteer API** — connect to any running profile programmatically via CDP, while still watching it live in the browser
- **Optional authentication** — protect the web UI and API with a single token, or run wide open locally
- **Powered by CloakBrowser** — 32 source-level C++ patches, passes Cloudflare Turnstile, 0.9 reCAPTCHA v3 score

## Stack

- **Backend**: FastAPI (Python)
- **Frontend**: React + Tailwind CSS
- **Browser viewer**: noVNC (WebSocket-based VNC client)
- **Database**: SQLite
- **Browser engine**: [CloakBrowser](https://github.com/CloakHQ/CloakBrowser) (stealth Chromium binary)

## Development

### Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8080
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

### Docker

```bash
docker compose up --build
```

## Requirements

- Docker (20.10+)
- ~2 GB disk (image + binary)
- ~512 MB RAM per running profile

## Updating

Pull the latest image and restart:

```bash
docker pull cloakhq/cloakbrowser-manager
docker stop <container-id>
docker run -p 8080:8080 -v cloakprofiles:/data cloakhq/cloakbrowser-manager
```

Your profiles and session data are stored in the `cloakprofiles` volume and persist across updates.

## Automation API

Every running profile exposes a CDP (Chrome DevTools Protocol) endpoint. Connect Playwright or Puppeteer to automate a profile while watching it live in the browser.

```python
from playwright.async_api import async_playwright

async with async_playwright() as pw:
    browser = await pw.chromium.connect_over_cdp(
        "http://localhost:8080/api/profiles/<profile-id>/cdp"
    )
    page = browser.contexts[0].pages[0]
    await page.goto("https://example.com")
```

```javascript
const { chromium } = require("playwright");

const browser = await chromium.connectOverCDP(
  "http://localhost:8080/api/profiles/<profile-id>/cdp"
);
const page = browser.contexts()[0].pages()[0];
await page.goto("https://example.com");
```

The CDP URL is available in the toolbar (code icon) when a profile is running. The same browser session is accessible both visually through VNC and programmatically through the API.

## Remote Access

The container binds to localhost only. To access from a remote server:

```bash
ssh -L 8080:localhost:8080 your-server
```

Then open `http://localhost:8080`.

## Authentication

By default, there is no authentication (ideal for local use). To protect the
web UI and API when hosting on a network, set the
`CLOAKBROWSER_MANAGER_AUTH_TOKEN` environment variable:

```bash
# Populate CLOAKBROWSER_MANAGER_AUTH_TOKEN through your secret manager first.
docker run -p 8080:8080 -v cloakprofiles:/data -e CLOAKBROWSER_MANAGER_AUTH_TOKEN cloakhq/cloakbrowser-manager
```

Or in `docker-compose.yml`:

```yaml
environment:
  - CLOAKBROWSER_MANAGER_AUTH_TOKEN
```

Inject `CLOAKBROWSER_MANAGER_AUTH_TOKEN` with your secret manager before
running Compose. The legacy `AUTH_TOKEN` name remains supported when the
deployment-specific variable is unset.

When either token variable is set:

- The web UI shows a login page. Enter the token to unlock.
- API consumers pass the token via `Authorization: Bearer <token>` header.
- VNC WebSocket connections are authenticated via the login cookie.
- The `/api/status` endpoint remains unauthenticated (for Docker healthcheck).
- CDP discovery and WebSocket routes under
  `/api/profiles/<canonical-v4-profile-uuid>/cdp` remain unauthenticated so CDP
  clients that cannot add WebSocket upgrade headers can connect directly.

The profile UUID in a CDP URL is a capability credential. Anyone who obtains
that complete URL can control the corresponding running browser. Keep it out of
logs, shell history, screenshots, and shared messages, and expose the Manager
only over HTTPS/WSS. Profile listing, profile status, mutations, VNC, clipboard,
extension catalog, and every other management API remain token-protected.

> **Note**: The auth token is transmitted in cleartext over HTTP. If you expose the Manager to the internet, put it behind a reverse proxy with HTTPS (Caddy, nginx, Traefik).

## License

- **This application** (GUI source code) — MIT. See [LICENSE](LICENSE).
- **CloakBrowser binary** (compiled Chromium) — free to use, no redistribution. See [BINARY-LICENSE.md](BINARY-LICENSE.md).

The GUI application requires the CloakBrowser Chromium binary to function. The binary is automatically downloaded on first launch and is governed by its own license terms. If you fork or redistribute this application, your users must comply with the [CloakBrowser Binary License](BINARY-LICENSE.md).

## Contributing

Contributions are welcome. Please [open an issue](https://github.com/CloakHQ/CloakBrowser-Manager/issues) first to discuss what you'd like to change.

## Links

- **CloakBrowser** — [github.com/CloakHQ/CloakBrowser](https://github.com/CloakHQ/CloakBrowser)
- **Website** — [cloakbrowser.dev](https://cloakbrowser.dev)
- **Bug reports** — [GitHub Issues](https://github.com/CloakHQ/CloakBrowser-Manager/issues)
- **Contact** — cloakhq@pm.me

## Fork-Specific Runtime and Deployment Changes

This public fork is based directly on CloakHQ `main` commit `a85b213` from
May 26, 2026. The fork changes below were consolidated on July 17, 2026. They
are intentionally environment-driven and contain no deployment hostname,
license key, API token, user path, or other operator-specific value.

### Current CloakBrowser and binary handling

- The Python wrapper is pinned to `cloakbrowser[geoip]==0.4.11`, the current
  release verified on July 17, 2026.
- The Docker image does not contain a CloakBrowser Chromium binary. Publicly
  repackaging that binary is prohibited by `BINARY-LICENSE.md`.
- Container startup calls the official wrapper's license validator and
  `ensure_binary()`. With a valid Pro key and automatic updates enabled, this
  downloads and signature-verifies the current Pro binary into the persistent
  `/data/cloakbrowser` cache.
- `CLOAKBROWSER_REQUIRE_LICENSE=true` is fail-closed: startup aborts if the key
  is absent, invalid, or would result in a free-tier binary.
- `/api/status` reports the installed wrapper version, binary version, binary
  tier, platform, configured profile limit, open slots, and extension count.

### Running-profile limit

`MAX_RUNNING_PROFILES` sets a server-side launch limit. The check is performed
under the same asynchronous lock that reserves launches, so simultaneous API
requests and auto-launch profiles cannot race past the limit. `0` or an unset
value means unlimited. The licensed deployment sets this to `3`.

### Environment-provisioned extensions

`CLOAKBROWSER_EXTENSION_IDS` accepts comma- or whitespace-separated 32-character
Chrome Web Store IDs. On every container start, the Manager:

1. Requests the current package from the Chrome Web Store update service.
2. Verifies the CRX2 or CRX3 developer signature and confirms that its signed
   key and ID match the requested extension ID.
3. Preserves the verified developer key in the unpacked manifest so Chrome's
   runtime ID remains identical to the configured Web Store ID.
4. Rejects path traversal, symbolic links, oversized packages, oversized
   expanded archives, and malformed manifests.
5. Unpacks updates atomically under `/data/extensions/<extension-id>` and uses
   an already verified cached version if the update service is temporarily
   unavailable.
6. Publishes the catalog through authenticated `GET /api/extensions`, shows the
   available extensions in the profile form, and defaults new profiles to the
   configured catalog.
7. Passes selected paths through CloakBrowser 0.4.11's native
   `extension_paths` launch option. Profiles cannot select an ID that was not
   provisioned at startup.

Extension settings remain isolated inside each profile. The extension packages
and catalog survive container restarts in the `/data` volume.

### Downloads, VNC, CDP, and authentication

- Downloads use a durable per-profile directory at
  `/data/downloads/<profile-id>`. Chrome preferences and CDP download behavior
  are both configured so website filenames are retained, including when an
  external CDP client attempts to replace the download path.
- The image includes CloakBrowser's recommended Linux, emoji, and extended font
  packages. Operators can mount a legally obtained Windows font set at
  `/data/fonts/windows`; startup registers it with fontconfig and launches the
  browser with `--fingerprint-fonts-dir`. Proprietary Windows fonts are not
  redistributed in the public image.
- The upstream KasmVNC/noVNC browser viewer remains available through the
  authenticated Manager WebSocket route. This fork does not publish raw VNC or
  RDP ports and does not create operating-system users or hardcoded passwords.
- CDP remains proxied through `/api/profiles/<profile-id>/cdp`; Chromium's CDP
  port binds only inside the container.
- API and CDP clients may authenticate with either
  `Authorization: Bearer <manager-token>` or
  `X-Cloak-Auth-Token: <manager-token>`. The second form leaves the
  `Authorization` header available to an outer access proxy. Browser VNC uses
  the secure login cookie.
- `CLOAKBROWSER_MANAGER_AUTH_TOKEN` takes precedence over the legacy
  `AUTH_TOKEN`, allowing multiple Managers to use independent credentials from
  one secret store.
- Internet deployments should keep port 8080 on a private Docker network and
  expose only an authenticated HTTPS reverse proxy or tunnel.

### Container image and required variables

`.github/workflows/docker-publish.yml` runs all backend and frontend tests,
audits frontend dependencies, builds the production frontend, and publishes
provenance attestations plus SBOM-enabled `linux/amd64` and `linux/arm64` images
to `ghcr.io/cjangrist/cloakbrowser-manager`. Tags include `latest` on the default
branch and an immutable full commit SHA.

The Compose file forwards secret names without embedding values. A licensed,
three-profile deployment uses:

```bash
# Populate the documented variables through your secret manager first.
docker compose pull
docker compose up -d
```

Use a secret manager rather than shell history for production values. The
license key and Manager token must never be added to Compose YAML, an image
layer, CI variables visible to pull requests, or Git history.
