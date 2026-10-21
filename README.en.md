# CodeRelayBridge (CRB)

[简体中文](README.md) · [Downloads](#downloads-and-requirements) · [User guide](docs/USER_GUIDE.en.md)

Use Codex and DeepSeek Harness desktop apps across multiple computers from one browser. Manage projects, continue chats, view execution progress, and access files and terminals from a computer, tablet, or phone.

## Preview

![CodeRelayBridge empty interface](docs/images/crb-empty-desktop.png)

An empty-state example with no real accounts, devices, or conversations loaded.

## Features

- Desktop chat, execution status, questions, and approval synchronization.
- A shared workspace for conversations across computers and apps.
- File browsing, previews, editing, and chat attachments.
- Tabbed terminals and file previews accessible across devices.
- Message delivery status, explicit retries, and completion notifications.
- A standalone tray client for launching apps, managing connections, and unpairing.
- Chinese and English interfaces, themes, and adjustable text size.
- Signed update manifests, package verification, and rollback.

Available actions depend on the capabilities of each desktop app.

## Downloads and requirements

[GitHub releases](https://github.com/Gabriel0224/CodeRelayBridge/releases)

Choose the latest installer for your platform and role. Platforms can be released independently; client and server versions are listed separately below.

| Platform | Client | Server |
| --- | --- | --- |
| Windows x64 | [v0.6.65 · Setup.exe](https://github.com/Gabriel0224/CodeRelayBridge/releases/download/v0.6.65/RelayClient-0.6.65-Standard-Windows-x64-Setup.exe) | [v0.6.65 · Setup.exe](https://github.com/Gabriel0224/CodeRelayBridge/releases/download/v0.6.65/RelayServer-0.6.65-Standard-Windows-x64-Setup.exe) |
| Linux x64 (Ubuntu / Mint) | [v0.6.65 · DEB](https://github.com/Gabriel0224/CodeRelayBridge/releases/download/v0.6.65/RelayClient-0.6.65-Standard-Linux-x64.deb) | [v0.6.65 · DEB](https://github.com/Gabriel0224/CodeRelayBridge/releases/download/v0.6.65/RelayServer-0.6.65-Standard-Linux-x64.deb) |
| macOS 27+ Apple Silicon | [v0.6.65 · DMG](https://github.com/Gabriel0224/CodeRelayBridge/releases/download/v0.6.65/RelayClient-0.6.65-Standard-macOS27-arm64.dmg) | [v0.6.65 · DMG](https://github.com/Gabriel0224/CodeRelayBridge/releases/download/v0.6.65/RelayServer-0.6.65-Standard-macOS27-arm64.dmg) |

The client and server are separate applications. Complete packages include Python, Qt, and runtime dependencies; Linux may also require system graphical libraries. Install and sign in to the original Codex or DeepSeek Harness desktop app separately.

This repository provides Standard source and packages. Standard does not require Cockpit; the Cockpit edition is distributed through a separate private repository.

## Quick start

1. Install the server on one computer and a client on each computer you want to connect.
2. Configure the server's listen address, access URL, and web login, then start it.
3. Open the URL in a browser and confirm that you can sign in.
4. Select “Pair / add app” in the client, enter the server URL and web credentials, select the app, and connect.
5. Choose an app, computer, project, or workspace in the panel and open a chat.

A computer can run both applications. For access across locations, use a private network such as Tailscale or ZeroTier, including the phone when needed. Public Internet access requires separate HTTPS and access-control configuration.

Full instructions: [English user guide](docs/USER_GUIDE.en.md) · [中文使用指南](docs/USER_GUIDE.zh-CN.md)

## Using Tailscale with v2rayN

Check v2rayN's TUN switch, then follow the matching steps:

- **TUN off**: add the panel's actual Tailscale IP/MagicDNS name to proxy exceptions → save and reapply → test browser login. Process rules are usually unnecessary.
- **TUN on**: set those exceptions → exclude Tailscale address ranges → reload TUN and test. Add direct rules such as `tailscaled.exe` only if problems remain.

**Turning TUN off does not guarantee unaffected access.** PAC and browser-specific proxies need exceptions in their own configurations. Do not force the entire browser or Codex app to direct.

[Step-by-step setup: fields, values, and verification](docs/USER_GUIDE.en.md#71-using-tailscale-with-v2rayn)

## Updates and security

Select Feedback in the panel's lower-left corner to open this repository's GitHub bug form. GitHub sign-in is required. Only the CRB version is prefilled; logs, chats, and device details are not uploaded automatically. Redact your report before submitting.

Select “Check for CRB updates” in the client or server. After confirmation, the updater verifies the manifest and package, preserves pairing and settings, and restarts Relay. Connections and panel terminals briefly disconnect; the original AI desktop apps are not restarted.

Pairing grants access to the target computer's files, terminal, and applications. Connect only to a trusted server, use HTTPS for public access, and keep credentials and private keys confidential. [Security](SECURITY.md)

## Development and license

[Development and builds](DEVELOPMENT.md) · [Updates](UPDATES.md)

Original project code is available under [PolyForm Noncommercial 1.0.0](LICENSE) for noncommercial use. Third-party components retain their own licenses. See [licensing](LICENSING.md) and [third-party notices](THIRD_PARTY_NOTICES.md).

Author: Gabriel

[Gitee repository](https://gitee.com/gabriel0224/CodeRelayBridge)
