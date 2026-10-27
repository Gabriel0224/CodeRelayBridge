# Source changes and installer availability

Published installers: Windows **0.6.69**; Ubuntu / Mint and macOS **0.6.68**. The matching release tags preserve the original accepted build sources. The independent-release tooling and GitHub history-discovery code below are not included in those original installers.

## Independent platform releases (not packaged)

- Collect and sign only explicitly selected changed platforms, while validating both roles and all editions on those platforms. Leave unchanged platforms' versions and published installers intact.
- Resolve GitHub updates from earlier signed Releases when the newest Release only contains another platform; preserve repository, signature, package and version checks. History reads are bounded and are skipped on the normal matching/up-to-date path.
- Generate introduction download cells from verified published packages, independently for every platform and role. Retain older Releases that still host any current platform download.
- 按平台发布并如实显示实际版本；本节是本地源码和发布工具变更，现有安装包、线上部署及正式 Release 尚未更新。

## 0.6.69 Windows conversation attachment repair

- Open existing Codex conversations through the original App's native navigation tool before using the Windows URI handler. Unknown native outcomes are never replayed through a second launcher; unavailable or explicitly unsupported tools retain the URI fallback.
- Report Windows login-session activation failures with a recovery message. Refuse GUI startup in session 0, including the independent-start flag, while retaining headless workers and diagnostics. Session 0 startup writes its diagnostic and exits instead of waiting on an invisible message box.
- 修正 Windows 对话接入的登录会话错误；本节修复以新补丁 0.6.69 构建，保留原 App、配对和历史。

## 0.6.68 deployment build

- Panel addresses restore the selected computer, conversation, workspace and settings category. Direct links, refresh and browser navigation preserve the intended target and page drafts.
- Conversation completion notifications float below the header without moving the conversation. The close button stays at the right edge, including on narrow screens and with long titles.
- 页面路由和悬浮完成通知保留原配色、配对、工作区和历史，现已随上面标明的各平台版本正式发布。

The earlier features below are included in the published installers listed above. This publication does not change installed deployments; choose each platform and role from the introduction download table.

- Codex user messages can be edited from earlier turns, including loaded older history. Editing replaces that message and the following conversation content through the original app. Attachments and current thread settings are retained; changes to files on disk are not undone. Running tasks must finish before editing. Older apps that reject historical editing require a reviewed local adapter; unsupported app versions report an error without automatically retrying an edit.
- A native history replacement invalidates expanded history and opaque pagination cursors in the panel. The edited text is retained if resending cannot be confirmed.
- New Codex chats have independent Fast mode and Full access switches. The selected model must support the speed tier. Settings are applied before the first model turn. If setup fails after creation, the panel keeps the accepted chat identity and the original draft instead of creating another chat.
- Native project registration, sidebar sections and forks, client/server close preferences, and Windows original-app channel discovery are also synchronized in this source update.

## 较早功能变更记录

下列较早功能已包含在上面标明的正式安装包中。本次发布不重新部署；请按介绍页的系统与角色选择实际版本。

- 可编辑较早轮次的 Codex 用户消息，包括已加载的旧历史。通过原 App 替换该消息和后续对话，保留附件及当前对话设置；不会撤销磁盘文件改动。运行中的任务需先结束。旧 App 若拒绝历史编辑，则需要已核验的本机适配；不支持的版本会报错，不自动重试编辑。
- 原生历史替换会清除面板的旧分页、展开内容及游标。无法确认重新发送时保留编辑内容。
- 新建 Codex 对话增加独立的“闪电加速”和“完全访问”开关，模型需支持所选速度；设置在首轮执行前生效。创建后若设置失败，保留已创建的对话标识和原草稿，避免重复创建。
- 此次源码同步也包括原生项目登记、分区与分支、客户端／服务端关闭偏好和 Windows 原 App 通道发现修复。
