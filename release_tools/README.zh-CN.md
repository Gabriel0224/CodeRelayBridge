# CodeRelayBridge（CRB）安装与更新

客户端连接本机的桌面 AI 应用，服务端提供网页面板与设备管理。同一台电脑可同时安装两者。原 Codex／DeepSeek Harness 应用需另外安装并登录。

## 安装

- Windows：解压完整 ZIP，运行其中的 `RelayClient.exe` 或 `RelayServer.exe`。
- Linux：解压完整 TAR.GZ，运行“启动客户端.sh”或“启动服务端.sh”。系统缺少图形库时，按界面的“网络与依赖”提示处理。
- macOS：打开 DMG，将对应应用拖入 Applications。Apple Silicon 安装包需要 macOS 27 或以上。

完整程序包含 Python、Qt 和运行依赖，请保留同目录的库文件。普通版不需要 Cockpit；集成版的 Cockpit 功能需要目标电脑安装 Cockpit Tools。

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
