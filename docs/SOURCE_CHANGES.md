# Source changes awaiting an installer release

These changes are in source only. Existing release installers and online deployments are unchanged. Update both Relay roles together when deploying these capabilities.

- Codex user messages can be edited from earlier turns, including loaded older history. Editing replaces that message and the following conversation content through the original app. Attachments and current thread settings are retained; changes to files on disk are not undone. Running tasks must finish before editing. Older apps that reject historical editing require a reviewed local adapter; unsupported app versions report an error without automatically retrying an edit.
- A native history replacement invalidates expanded history and opaque pagination cursors in the panel. The edited text is retained if resending cannot be confirmed.
- New Codex chats have independent Fast mode and Full access switches. The selected model must support the speed tier. Settings are applied before the first model turn. If setup fails after creation, the panel keeps the accepted chat identity and the original draft instead of creating another chat.
- Native project registration, sidebar sections and forks, client/server close preferences, and Windows original-app channel discovery are also synchronized in this source update.

## 尚未发布安装包的源码变更

现有 Release 安装包和在线部署没有更新。后续部署时需同步更新 Relay 客户端和服务端。

- 可编辑较早轮次的 Codex 用户消息，包括已加载的旧历史。通过原 App 替换该消息和后续对话，保留附件及当前对话设置；不会撤销磁盘文件改动。运行中的任务需先结束。旧 App 若拒绝历史编辑，则需要已核验的本机适配；不支持的版本会报错，不自动重试编辑。
- 原生历史替换会清除面板的旧分页、展开内容及游标。无法确认重新发送时保留编辑内容。
- 新建 Codex 对话增加独立的“闪电加速”和“完全访问”开关，模型需支持所选速度；设置在首轮执行前生效。创建后若设置失败，保留已创建的对话标识和原草稿，避免重复创建。
- 此次源码同步也包括原生项目登记、分区与分支、客户端／服务端关闭偏好和 Windows 原 App 通道发现修复。
