# Updates / 更新

Select **Check for CRB updates / 检查 CRB 更新** in the native client or server. The updater selects the package matching your operating system, program role and edition.

Standard packages are available from [CodeRelayBridge](https://github.com/Gabriel0224/CodeRelayBridge/releases). Cockpit packages are available from the private [CodeRelayBridge-Cockpit](https://github.com/Gabriel0224/CodeRelayBridge-Cockpit/releases) repository.

Platforms are released independently. The introduction page lists the actual latest published client and server installers for each platform; another platform's newer version does not change those versions. Only changed platforms are uploaded. Keep any older Release that still provides a platform's latest verified package.

中文：各系统独立发布，只上传有变化的平台。介绍页分别标注各系统客户端、服务端实际已发布的最新版本及下载链接；未变化的平台保留原版本。其他平台仍在使用的最新安装包及其 Release 必须保留。

The 0.6.70 updater reads the release catalog and selects the newest signed package for its own platform, role and edition, independently of GitHub’s global Latest badge. Catalog reads are limited to five pages of twenty releases; incomplete discovery reports an error rather than claiming there is no update.

中文：0.6.70 起，更新器直接读取发布列表，按本机系统、角色和版本类型选择最新的签名安装包，不再依赖 GitHub 的全局 Latest 标记。最多读取五页、每页二十条；超过上限明确提示查询未完成。

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
