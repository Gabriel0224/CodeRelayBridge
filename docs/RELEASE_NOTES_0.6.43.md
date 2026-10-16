# CodeRelayBridge v0.6.43

## 中文

- 修复重复登录时偶发的 HTTP 501：未使用的 POST 请求体不再影响复用连接上的下一条请求。
- 登录提交增加防重复处理，旧错误不会覆盖新一次登录结果。
- 服务端设置自动根据监听 IP 和端口生成网页地址，仍可手动修改为域名或反代地址。
- 已有本地登录配置可以明确保存账号、密码和地址；密码留空时保留原值。运行中的服务会在确认后重启应用设置，并验证新密码，不再只报告文件已保存。
- 客户端解除配对后，网页通过现有事件连接及时移除设备，避免等待对话读取；迟到的旧目录响应不会恢复已移除的设备。
- 客户端配对成功后自动关闭配对窗口，结果保留在主界面。配对失败时窗口仍可编辑重试。

普通版与 Cockpit 集成版共用这些修正。保存服务端配置不会停止原 AI 桌面应用；服务重启会短暂断开网页和面板终端。使用外部登录入口的既有配置不会被本地密码设置覆盖。

## English

- Fixed intermittent HTTP 501 errors during repeated sign-in: unused POST bodies can no longer corrupt the next request on a reused connection.
- Prevented duplicate sign-in submissions and stale error messages.
- The server now generates its web address from the listen IP and port. You can still enter a domain or reverse-proxy address.
- Existing local sign-in settings can now be saved explicitly. Leave the password blank to keep it. A running service restarts after confirmation and verifies a new password, rather than reporting only that the file was saved.
- Unpairing removes the device promptly through the existing event stream, without waiting for a chat read. Late catalog responses cannot restore a removed device.
- Successful pairing closes the setup dialog automatically and keeps its result in the main window. Failed attempts remain editable.

These fixes are shared by Standard and Cockpit editions. Saving server settings does not stop the original AI desktop apps; a relay restart briefly disconnects web sessions and panel terminals. Existing external sign-in configurations are not overwritten by local password settings.
