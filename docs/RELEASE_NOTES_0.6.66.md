# CodeRelayBridge v0.6.66

## 更新

- 同步历史用户消息编辑与相关状态，新建对话提供“加速”和“完全访问”开关，能力以目标原 App 为准。
- 修正历史分页连续性、执行状态与按需输出同步；原 App 未提供的思考内容不补造。
- 项目创建核对桌面登记；补齐受支持的分区、排序和分支操作。恢复原项目／对话配色，筛选区下方左侧为“分区与排序”、右侧为“新对话”，两按钮同排同样式。
- 分区和设置采用有界客户端内存缓存与按需条件读取，减少重复访问和未变化内容回传；手动刷新与写入仍核对原 App。
- Windows 安装器支持选择安装位置。客户端与服务端管理界面提供后台升级提醒，以及“跳过此版本”“取消”“升级”选项。
- 改进原生问题面板退场与新版 App 适配。部分问题联动需要原 App 本机通道可用；不自动重启原 App。
- 修正服务端管理界面在设备行出现时的状态单元格渲染错误，保留实时语言切换，用户设备名称不翻译。

## 安装与兼容性

Windows x64 使用 Setup.exe，Ubuntu／Mint x64 使用 DEB，Apple Silicon 使用 macOS 27+ DMG。客户端和服务端分别选择对应角色及版本；普通版与集成版源码和安装包分别发布。ZIP／TAR.GZ 仅供旧更新器兼容。

升级前退出对应 Relay 组件；配对、配置和历史保留。原 AI 应用需另外安装登录。可用菜单、历史编辑和原生问题联动取决于原 App 版本及连接状态。

## Changes

This release includes historical user-message editing and supported first-turn controls, continuous history paging, execution-state and on-demand output fixes, native project registration checks, supported sidebar and fork operations, restored project/chat colors, and compact sidebar actions.

Bounded client-side memory caches and conditional reads reduce repeated native reads and unchanged responses. Windows installers support choosing an installation directory. Client and server managers offer background update notices with skip-version, cancel, and upgrade actions.

Native question retirement and current App compatibility are improved where the original App's local channel is available. The server manager now renders populated device-status cells correctly while preserving language changes and user-provided device names. Missing native reasoning content is never fabricated, and original Apps are not automatically restarted.

Use Setup.exe on Windows x64, DEB on Ubuntu/Mint x64, and the macOS 27+ DMG on Apple Silicon. Choose the correct role and edition. ZIP/TAR.GZ files support legacy updaters. Quit the corresponding Relay component before upgrading; pairing, settings, and history are retained. Install and sign in to the original AI applications separately.
