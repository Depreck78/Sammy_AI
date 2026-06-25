# Security Policy

## Reporting a Vulnerability

Please do not open a public issue for sensitive security reports.

Report vulnerabilities privately through GitHub Security Advisories for this repository. Include:

- A concise description of the issue
- Steps to reproduce
- Impact and affected versions or commits, if known
- Any suggested fix or mitigation

## Sensitive Data

Sammy may store local OAuth credentials, encrypted tool credentials, uploaded files, logs, and SQLite data under `~/.sammy`. Those files must not be committed or shared publicly.

## Local App Exposure

Sammy is a local single-user app. You can add a local login password from Settings > General; Sammy stores a password hash and uses an HttpOnly session cookie after login. The default host is `127.0.0.1`; keep it local unless you intentionally need same-Wi-Fi access.

Running `sammy lan` or setting `SAMMY_HOST` to a network-reachable address exposes the app to devices that can reach your Mac. Without a login password, a network client may use the UI/API, upload files, view local chat data, change settings, and prompt enabled tools to act with configured credentials. Only use LAN mode on trusted private networks, and stop Sammy when finished.

The File System plugin is read-only by default and limited to the repository root unless a user configures other allowed directories. File writes require the explicit `Allow file writes` plugin setting.
