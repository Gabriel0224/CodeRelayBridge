# Source changes and installer availability

Published installers: Windows, Ubuntu / Mint and macOS **0.6.75**. The v0.6.75 tag preserves the accepted native build sources, including the unified extension row. Later releases upload only changed platforms; each download cell keeps its actual published version.

## 0.6.76 Reasoning summaries follow the selected palette

Use the current palette for the reasoning summary background, border and heading, with the normal readable text color in both light and dark modes. Quota and error notices keep their semantic colors.

## 0.6.75 Local web shortcut in the extension row

Place Local web alongside Terminal, Files and Subagents using the same button styling. Keep the controls on one row, with horizontal scrolling in narrow panes or at large font sizes. The shortcut continues to open the shared preview.

## 0.6.74 Desktop project identity and shared web/file preview tabs

- Prefer the Desktop App's explicit chat memberships and project ID mapping over shared-folder guesses. Honor projectless chats; ambiguous legacy roots stay unclassified instead of borrowing the first project.
- Open loopback links using their message or file's computer. Cover hydrated links, external attachments and dynamic preview links, including modifier clicks and scripted popups.
- Place local web pages alongside files and Office documents in one preview tab strip, with optional desktop split view. Keep each tab's forwarding session isolated and release it on close; independent windows remain an explicit action.
- This is a CRB forwarding view, not an exported instance of the App's native browser. Existing sandbox, authentication and bounded transfer rules remain in force.

## 0.6.73 composer controls stay on one line

Keep jump-to-latest, model selection, fast mode and full access on one line when the jump button appears. Long model names truncate first; narrow panes or large fonts scroll only the control group horizontally, preserving full switch labels and separate click targets. Queue controls and expanded model settings retain their existing layout. This style-only fix does not change model, permission, theme or font settings. The 0.6.73 Linux deployment build includes it; published downloads remain unchanged until a separate Release.

## 0.6.74 Office editing engine

- Connect a self-hosted ONLYOFFICE Docs service from the native server settings; edit DOCX, XLSX and PPTX in the panel. Older Office and OpenDocument formats remain view-only.
- Isolate the remote editor from the panel origin, authenticate engine callbacks, and save bounded binary chunks back to the selected client with digest conflict checks and recoverable transactions.
- Keep failed edits available for download and retry; report a save only after a matching client receipt. See [document engine setup](DOCUMENT_EDITING.md).
- Resolve native conversation attachments through their existing asset index; content-addressed DeepSeek copies remain read-only so their native hashes stay valid.
- The 0.6.74 native builds include this feature; the download tables identify verified published packages.

## 0.6.70 updater compatibility upgrade

- Both roles and editions discover their platform-specific newest signed package from the release catalog on GitHub and Gitee, including a pinned or older GitHub Latest badge, independently versioned roles, and paginated catalogs.
- This one-time updater change affects all three platforms; subsequent releases keep untouched platforms at their actual existing versions.
- 保留签名、仓库、平台、角色、包哈希及大小校验，未完成查询不冒称无更新。

## Independent platform releases (included in 0.6.70)

- Collect and sign only explicitly selected changed platforms, while validating both roles and all editions on those platforms. Leave unchanged platforms' versions and published installers intact.
- Resolve GitHub updates from earlier signed Releases when the newest Release only contains another platform; preserve repository, signature, package and version checks. Catalog reads are bounded; already-current packages do not download signed manifests or installer data.
- Generate introduction download cells from verified published packages, independently for every platform and role. Retain older Releases that still host any current platform download.
- 按平台发布并如实显示实际版本；0.6.70 已包含更新器兼容升级，后续仅上传有实际变化的平台。

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


## 0.6.72 Shared document-engine setup

Windows SSH document operations stream the bounded helper and request through standard input. Ordinary file operations retain a short command, including when document editing is enabled.
The stdin length marker is quoted as a literal native-program argument so PowerShell cannot remove it through argument splatting. This preserves document access through the Windows SSH fallback when the original model App is closed.

Integrated managed ONLYOFFICE setup into native server first-run and its document page, with a server-only Windows installer choice and a narrow root-owned helper in Linux DEBs. Private owner/JWT registration, retryable setup, pinned/verified runtime downloads, status/start/stop/remove, active-document guards and advanced external-engine configuration are included. A network-disabled engine is reached through a bounded socket channel and opaque same-URL browser gateway; no additional public port is published. Original documents and failed save recovery copies are preserved. The 0.6.72 Windows and Linux deployment build includes these changes; published downloads remain unchanged until a separate Release.


## 0.6.72 Local web previews

Added message-host-bound loopback web links and manual web preview controls. On-demand authenticated client wires forward HTTP, SSE and WebSockets without publishing application ports or adding page bodies to chat history. Preview documents use an opaque sandbox; target cookies and fallback storage are session-local. Explicit close cancels active streams; separate windows have their own leases. Literal loopback validation, client identity separation, reconnect handling and normal TLS verification are preserved. Published downloads remain unchanged. See LOCAL_WEB_PREVIEW.md for compatibility boundaries.
