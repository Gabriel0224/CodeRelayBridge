# CodeRelayBridge 0.6.45

## 中文

- Windows 客户端、服务端改为标准 Setup.exe 安装器，提供开始菜单、主程序同级 `Uninstall.exe` 卸载入口和安装日志；按当前用户安装，保留配对、配置与历史。卸载入口仅依赖 Windows 系统 API，不运行 PowerShell 或脚本。
- Ubuntu／Linux Mint 改为原生 amd64 DEB，声明系统依赖；系统程序文件与个人数据分离，支持包管理器安装、重装和移除。
- 安装器会阻止覆盖仍在运行的对应 Relay 程序，不关闭原 AI App；卸载不等于撤销配对授权。
- 在线更新支持新安装格式；系统 DEB 通过包管理器更新并准备回滚包，不直接覆盖系统目录。保留旧版更新器使用的签名兼容资源。
- 普通版、Cockpit 集成版采用相同公共实现；包含 0.6.44 的配对重试与地址同步修正。

## English

- Windows clients and servers now use a standard Setup.exe installer with Start menu entries, an `Uninstall.exe` beside the main executable, and installation logs. The uninstall launcher uses only Windows system APIs, with no PowerShell or script dependency. Per-user settings, pairing, and history are preserved.
- Ubuntu and Linux Mint now use native amd64 DEB packages with declared system dependencies and separate application and user-data directories.
- Installers refuse to replace a running Relay application. Original AI apps are not closed. Uninstalling does not revoke device pairing.
- Online updates support the new formats. System DEB installations update through the package manager with a prepared rollback package, not by overwriting system files. Signed compatibility assets remain available for older updaters.
- Both editions share the implementation and retain the pairing-retry and address-sync fixes from 0.6.44.
