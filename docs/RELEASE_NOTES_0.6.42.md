# CodeRelayBridge v0.6.42

## 中文

- 修复 Windows 普通权限启动或重启服务时，系统授权结果文件无法回读、导致启动中断的问题。
- 改进授权结果校验与错误提示；结果不完整时提示重新检测，不自动重复授权。
- 顶部“刷新对话”改为整页“重载”，重新获取网页及界面资源，同时保留登录、工作区与本地记录。
- 网页初始化失败时提供独立重载入口；有未发送内容或正在编辑的文件时先提示保存。
- 普通版与 Cockpit 集成版同步更新，提供 Windows x64、Linux x64、macOS 27+ Apple Silicon 的完整客户端与服务端安装包。

Windows 请完整解压 ZIP 后运行程序，并保留同目录的 `_internal` 文件夹；无需另装 Python。重载不会重发消息或重启原 AI 桌面应用。

## English

- Fixed service startup and restart failures on Windows when the original non-admin process could not read the elevated helper's result file.
- Improved authorization result validation and error messages. Incomplete results prompt a fresh check instead of automatically requesting elevation again.
- Replaced the toolbar's Refresh chat action with a full-page Reload that fetches fresh interface resources while preserving sign-in, workspaces, and local records.
- Added an independent recovery action for page initialization failures, with a warning before discarding unsent content or file edits.
- Updated both Standard and Cockpit editions, with complete native client and server packages for Windows x64, Linux x64, and macOS 27+ Apple Silicon.

On Windows, extract the entire ZIP before running the app and keep its `_internal` directory alongside the executable. A separate Python installation is not required. Reload does not resend messages or restart the original AI desktop apps.
