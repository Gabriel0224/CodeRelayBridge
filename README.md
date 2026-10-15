# CodeRelayBridge（CRB）

[English](README.en.md) · [下载](https://github.com/Gabriel0224/CodeRelayBridge/releases/latest) · [使用指南](docs/USER_GUIDE.zh-CN.md)

在一个网页中使用多台电脑上的 Codex 和 DeepSeek Harness 桌面应用。通过电脑、平板或手机管理项目、继续对话、查看执行过程，并访问对应电脑的文件和终端。

## 功能

- 同步原桌面应用的对话、执行状态、提问与审批。
- 跨应用、跨电脑的工作区，集中查看常用对话。
- 文件浏览、预览、编辑和对话附件。
- 多标签终端与文件预览，支持不同设备访问。
- 消息接收状态、明确重试和任务完成提示。
- 独立托盘客户端，可启动应用、管理连接与解除配对。
- 中英文界面、主题切换和字体调整。
- 签名更新清单、安装包校验与更新回滚。

各桌面应用支持的功能不同，面板会显示对应的可用操作。

## 下载与要求

[下载最新版本](https://github.com/Gabriel0224/CodeRelayBridge/releases/latest)

| 平台 | 客户端 | 服务端 |
| --- | --- | --- |
| Windows x64 | ZIP | ZIP |
| Linux x64（Ubuntu / Mint） | TAR.GZ | TAR.GZ |
| macOS 27+ Apple Silicon | DMG | DMG |

客户端和服务端是独立程序。完整安装包包含 Python、Qt 和运行依赖；Linux 可能还需要系统图形库。原 Codex／DeepSeek Harness 桌面应用需另外安装并登录。

此仓库提供普通版源码和安装包。普通版不依赖 Cockpit；Cockpit 集成版通过单独的私有仓库提供。

## 快速开始

1. 在一台电脑上安装服务端，在需要接入的电脑上安装客户端。
2. 在服务端设置监听地址、访问地址及网页登录账号，启动服务。
3. 用浏览器打开访问地址，确认能够登录。
4. 在客户端选择“配对 / 添加应用”，填写服务端地址和网页登录账号，选择应用后接入。
5. 在网页选择应用、电脑、项目或工作区，打开对应对话。

同一台电脑可以同时安装客户端和服务端。跨地点使用时，可通过 Tailscale 或 ZeroTier 建立私有网络；手机也需加入相应网络。公网访问需单独配置 HTTPS 和访问控制。

详细步骤：[中文使用指南](docs/USER_GUIDE.zh-CN.md) · [English user guide](docs/USER_GUIDE.en.md)

## 同时使用 Tailscale 与 v2rayN

开启 v2rayN 后面板连不上或明显变慢时，先区分系统代理和 TUN：系统代理需为服务端实际 IP／MagicDNS 名称设置例外；TUN 还应排除 Tailscale 网段，并检查 Windows 后台服务 `tailscaled.exe` 的直连规则。仅添加托盘或命令行进程不等于排除后台服务，也不要把整个浏览器或 Codex 强制直连。

具体填写位置、IPv4／IPv6 网段、DNS 与验证步骤见[共存配置说明](docs/USER_GUIDE.zh-CN.md#71-同时使用-tailscale-与-v2rayn)。

## 更新与安全

在客户端或服务端中选择“检查 CRB 更新”。确认安装后，程序会校验签名和安装包，保留配对与设置，并重新启动 Relay。更新期间连接和网页终端会短暂中断，不会重启原桌面 AI 应用。

配对允许访问目标电脑的文件、终端和应用。请只连接可信服务端，公网使用 HTTPS，不要公开连接凭据或私钥。[安全说明](SECURITY.md)

## 开发与许可

[开发与构建](DEVELOPMENT.md) · [更新说明](UPDATES.md)

项目自有代码采用 [PolyForm Noncommercial 1.0.0](LICENSE)，限非商业用途。第三方组件保留各自许可，详见 [许可范围](LICENSING.md) 和 [第三方声明](THIRD_PARTY_NOTICES.md)。

作者：Gabriel
