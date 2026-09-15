# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | :white_check_mark: Current |

Only the latest release receives security updates. Older versions should be upgraded.

## Reporting a Vulnerability

**Please do not open a public issue for security vulnerabilities.**

Instead, report them privately:

1. **Email:** [bart@schelstraete.org](mailto:bart@schelstraete.org)
2. **Subject line:** `[SECURITY] shelfcloud-decky — <brief description>`

You should receive an acknowledgement within 48 hours. A fix or mitigation will be communicated to you before any public disclosure.

## Scope

The following components are in scope:

| Component | Description |
|-----------|-------------|
| `main.py` | Plugin backend — handles API tokens, network requests, file I/O |
| `src/`    | Frontend — renders UI inside Steam's client |
| `bin/shelfcloud-launch.py` | Launcher — opens a browser with a URL and profile |
| `py_modules/` | Browser detection and CDP interaction |
| `packaging/` | Install scripts that run with `sudo` |

## Security Model

### What Shelfcloud stores

- **API token** — stored in `~/homebrew/settings/Shelfcloud/settings.json`. Used to authenticate with the gateway. File permissions are managed by the install script.
- **Synced state** — which games have been added as Steam shortcuts. No credentials, no personal data beyond game IDs and names.
- **Icon files** — artwork images on disk, referenced by Steam. No executable content.

### What Shelfcloud does NOT store or transmit

- Provider passwords or session cookies (these stay in the browser's own profile)
- Steam credentials (sign-in is handled by Steam's own OAuth flow)
- Browsing history or activity beyond which game was launched and when

### Network communication

- All communication with the gateway uses HTTPS
- API tokens are sent in `Authorization: Bearer` headers, never in URLs or query strings
- Artwork fetches from third-party CDNs carry no authentication headers
- The plugin never phones home to any service besides the configured gateway

### Launch security

- The launcher receives URLs from the gateway, and does **not** trust them. Every
  URL — whether the gateway returned it just now or it was baked into the Steam
  shortcut at sync time — is checked **on the device** against an allow-list of
  provider hosts before a browser opens: `play.geforcenow.com`,
  `cloud.boosteroid.com`, `www.xbox.com` and `luna.amazon.com`. Anything else is
  refused and logged, and the game does not launch.
- **https only.** A launch carries provider session cookies; over http they are
  readable by anything on the path, which can also rewrite the page.
- Amazon Luna is pinned to the **full host** `luna.amazon.com`, not to
  `amazon.com`. The allow-list matches subdomains, so the broader form would
  admit every host Amazon serves — retail pages, seller-controlled content,
  arbitrary storage subdomains — into a fullscreen browser with no address bar.
- URLs containing a backslash, tab, carriage return or newline are refused before
  parsing. Python and Chromium disagree about where the hostname ends in those,
  and the browser is the one that opens the page.
- Credentials in the URL authority (`https://cloud.boosteroid.com@evil.example/`)
  are refused: they name one host while reading as another.
- Browser profiles are isolated per-browser (Chrome and Edge get separate profiles)
- Launch commands are shell-quoted to prevent injection via special characters in URLs
- The install script validates browser app IDs against a known allowlist before installing anything

This check exists because the gateway is a server, and servers get compromised.
A launch URL it returns is opened in a kiosk-mode browser holding live provider
sessions, with no address bar and no tab strip to show where it went — a
phishing page in that window is indistinguishable from the real sign-in.

## Best Practices for Users

- **Keep Decky and the plugin updated** — security fixes ship as new versions
- **Don't share your settings.json** — it contains your API token
- **Use the offical install method** — manual installs that skip `install-to-deck.sh` may leave file permissions wrong
- **Review launch logs** if a game behaves unexpectedly: `~/homebrew/logs/shelfcloud-launch.log`
