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

- The launcher receives URLs from the gateway. It validates that URLs start with `http://` or `https://` before opening them
- Browser profiles are isolated per-browser (Chrome and Edge get separate profiles)
- Launch commands are shell-quoted to prevent injection via special characters in URLs
- The install script validates browser app IDs against a known allowlist before installing anything

## Best Practices for Users

- **Keep Decky and the plugin updated** — security fixes ship as new versions
- **Don't share your settings.json** — it contains your API token
- **Use the offical install method** — manual installs that skip `install-to-deck.sh` may leave file permissions wrong
- **Review launch logs** if a game behaves unexpectedly: `~/homebrew/logs/shelfcloud-launch.log`
