# CodeRelayBridge 0.6.44

## 中文

- 修复首次配对失败后，在同一客户端窗口重试被误报为“已有另一份客户端”的问题；仅复用文件完全一致的安装，不覆盖其他版本或不完整程序。
- 桌面快捷方式创建暂时失败后，可以重试完成安装。
- 修复通过辅助功能填写面板地址时，后台连接地址未同步的问题；手动设置的独立后台地址仍会保留。
- 普通版与 Cockpit 集成版使用相同修正，保留已有配对、偏好和历史。

## English

- Fixed first-time pairing retries incorrectly reporting another client installation after an unsuccessful attempt. Only an identical, complete installation is reused; other versions and incomplete installations are not overwritten.
- Installation can recover from a temporary desktop shortcut failure.
- Fixed the server address not following the panel address when entered using accessibility tools. Explicit custom server addresses are preserved.
- The fixes apply to both Standard and Cockpit editions, preserving existing pairing, preferences, and history.
