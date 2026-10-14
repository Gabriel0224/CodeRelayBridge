# Updates / 更新

Select **Check for CRB updates / 检查 CRB 更新** in the native client or server. The updater selects the package matching your operating system, program role and edition.

Standard packages are available from [CodeRelayBridge](https://github.com/Gabriel0224/CodeRelayBridge/releases/latest). Cockpit packages are available from the private [CodeRelayBridge-Cockpit](https://github.com/Gabriel0224/CodeRelayBridge-Cockpit/releases/latest) repository.

## Install an update / 安装更新

1. Check for updates and review the available version.
2. Confirm the download and installation.
3. The updater checks the signed manifest, package size, SHA-256 and native program identity.
4. Relay closes its own connections, replaces the complete application package and restarts.

Pairing and settings are retained. Panel connections and terminals briefly disconnect; the original Codex and DeepSeek desktop apps are not restarted. If installation fails, the updater retains or restores the previous program.

中文：更新只替换 Relay 程序，保留账号、配对与设置。更新期间网页连接和终端会短暂断开，原 AI 桌面应用不会重启。安装失败时保留或恢复旧程序。

## Private releases / 私有版本

Cockpit updates require a GitHub token with **Contents: read** permission for the private repository. Optional token storage uses Windows Credential Manager, macOS Keychain or Linux Secret Service. Tokens are sent only to the GitHub API, not to download redirects. If the credential store is unavailable, the program does not save a plaintext fallback.

中文：集成版需要私有仓库读取权限。可选择将授权保存到系统密钥库；无法使用密钥库时，不以明文文件替代。

## Manual installation / 手动安装

Download the complete package for your platform and edition. Close Relay before replacing its application directory. Keep the surrounding configuration and data directories. Do not mix executable files or libraries from different versions.

Each release provides `SHA256SUMS.txt` and the signed `crb-update-v2.json` manifest. `crb-update.json` is supplied for compatible older client updaters. Applications without a working updater can be upgraded manually.
