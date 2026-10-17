# CodeRelayBridge（CRB）安装与更新

客户端连接本机的桌面 AI 应用，服务端提供网页面板与设备管理。同一台电脑可同时安装两者。原 Codex／DeepSeek Harness 应用需另外安装并登录。

## 安装

- Windows：运行对应客户端或服务端的 `*-Setup.exe`，按安装向导完成安装。程序按当前用户安装，不要求整个应用长期使用管理员权限。升级、卸载前按提示退出 Relay；原 Codex／DeepSeek 不必退出。
- Ubuntu／Linux Mint：安装对应 `.deb`，推荐使用 `sudo apt install --reinstall ./安装包文件名.deb` 自动处理声明的系统依赖，然后从应用菜单打开。程序文件由包管理器安装到 `/opt/coderelaybridge/`，个人配置仍保存在用户目录。安装时不以 root 启动桌面应用，不自动配置防火墙或登录账号。
- macOS：打开 DMG，将对应应用拖入 Applications。Apple Silicon 安装包需要 macOS 27 或以上。

完整程序包含 Python、Qt 和运行依赖，请保留同目录的库文件。普通版不需要 Cockpit；集成版的 Cockpit 功能需要目标电脑安装 Cockpit Tools。

Windows 安装版可双击主程序同级的 `Uninstall.exe`，也可从系统“已安装的应用”卸载；Ubuntu 可使用 `sudo apt remove coderelaybridge-client` 或 `sudo apt remove coderelaybridge-server`。卸载保留个人配置、配对与历史；若要撤销远端授权，先在客户端解除配对。自动更新兼容用 ZIP／TAR.GZ 不是手动安装入口。

## 首次连接

1. 在服务端设置监听地址、访问地址和网页登录账号，创建配置并启动。
2. 用另一台设备的浏览器打开访问地址，确认能够登录。
3. 在客户端选择“配对 / 添加应用”，填写相同访问地址和网页登录账号，选择应用后接入。
4. 在网页选择应用、电脑、项目或工作区，打开对应对话。

跨地点访问可使用 Tailscale 或 ZeroTier 私有网络。公网访问需单独配置 HTTPS 和访问控制。

## 更新

在客户端或服务端中选择“检查 CRB 更新”。程序校验签名和安装包，确认后替换完整 Relay 程序，保留配对与设置，不重启原 AI 应用。更新期间网页和终端会短暂断开。

手动更新时先关闭 Relay，再替换完整应用目录，保留上层配置与数据。不要混用不同版本的 EXE、动态库或 `_internal`。

## 安全与指南

配对允许访问目标电脑的文件、终端和应用，只连接可信服务端，不公开连接凭据或私钥。

完整步骤见随包 `docs/USER_GUIDE.zh-CN.md` 和 `docs/USER_GUIDE.en.md`。

项目自有代码采用 PolyForm Noncommercial 1.0.0，限非商业用途。第三方组件保留各自许可，详见随包 LICENSE、LICENSING.md 和 THIRD_PARTY_NOTICES.md。
