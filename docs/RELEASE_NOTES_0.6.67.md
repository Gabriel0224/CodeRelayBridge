# CodeRelayBridge v0.6.67

## 本地部署更新 / Deployment update

- 所有未选中对话（含工作区）增加随主题变化的浅色外轮廓，保留原背景、字体和选中样式。
- 修正原位更新及失败回滚的文件完整性；回滚失败时保留备份并停止启动。
- 修正分支会话正式 ID 的确认、创建会话阶段的持久回执和重复请求识别。
- 修正设备离线状态通知、镜像读取异常和 IPC 断开后的状态清理。
- 补齐当前受核验 N305 原 App 的问题面板及队列恢复契约，保留未知版本保护。

Adds a theme-aware light outline to every unselected chat, including workspace chats. Fixes exact in-place update/rollback, confirmed fork identities, durable creation receipts, request idempotency, offline notifications, mirror-read errors and IPC cleanup. Extends the reviewed N305 native question/queue contract while preserving unknown-version checks.

此版本按用户要求用于本地全量部署与源码同步，尚未发布 Release。现有下载链接继续指向已正式发布的版本。安装位置、配对、设置与历史保留；原模型 App 不自动重启。

This version is prepared for fleet deployment and source synchronization; no Release is published. Existing download links remain pinned to the latest published version. Installation locations and user state are preserved; original model Apps are not restarted.
