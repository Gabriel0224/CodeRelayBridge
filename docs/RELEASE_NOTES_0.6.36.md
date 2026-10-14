# CodeRelayBridge 0.6.36

## 更新内容

- 修复工作区持久化数据读取，保留已有对话链接和排列顺序。
- 子智能体对话移入文件／终端侧窗的独立标签，支持按当前对话或本机全部查看。
- 连接连续失败五次后停止数据桥接，支持从网页、托盘或重新启动原 App 后恢复。
- 文件浏览使用客户端直连，优化 Windows 后台 SSH 和 PowerShell 启动。

## Changes

- Fixed persistent workspace loading while retaining existing chat links and ordering.
- Moved subagent chats to a dedicated tab beside files and terminals, with current-chat and computer-wide views.
- Stopped data bridges after five consecutive connection failures, with recovery from the web panel, tray, or a new desktop app launch.
- Enabled direct client file access and improved background SSH and PowerShell launches on Windows.
