# CodeRelayBridge 0.6.46

## 中文

- Windows 安装后，主程序同级提供 `Uninstall.exe`，可直接启动正式卸载程序；不依赖 PowerShell、命令脚本或额外运行库。
- 修正首次安装复制不完整的问题：客户端和服务端完整复制、核验附属文件，重复安装或配对重试不再因遗漏同级文件而误判安装损坏。
- 卸载仍保留配对、个人配置和历史；如需撤销服务器授权，请先解除配对。
- 普通版与 Cockpit 集成版使用相同公共实现，保留 Windows Setup、Ubuntu／Mint DEB 与 macOS DMG 安装格式。

## English

- Windows installations include `Uninstall.exe` beside the main executable. It starts the registered uninstaller without PowerShell, command scripts, or an extra runtime.
- Client and server first-run installation now copies and verifies the complete payload, including companion files. Repeated installation and pairing retries no longer misidentify an installation as incomplete because a companion file was omitted.
- Uninstalling preserves pairing, personal settings, and history. Unpair first to revoke server authorization.
- Both editions share the implementation and retain Windows Setup, Ubuntu/Mint DEB, and macOS DMG installation formats.
