# CodeRelayBridge 0.6.47

## 中文

- 修正服务端状态刷新可能打断“启动服务”等按钮点击的问题。后台读取不再禁用操作按钮，也不会丢弃已点击的服务操作。
- 忽略操作前开始读取的过期状态和错误，防止它们提前解锁正在执行的操作或覆盖新状态。
- 保留单次后台读取合并、服务操作串行执行，以及原有配置、配对和卸载行为。

## English

- Fixed server status refreshes interrupting clicks on Start service and other controls. Background reads no longer disable action buttons or discard service operations.
- Ignore stale status results and errors from reads started before an operation, so they cannot unlock active controls or overwrite newer state.
- Preserve coalesced background reads, serialized service operations, and existing configuration, pairing, and uninstall behavior.
