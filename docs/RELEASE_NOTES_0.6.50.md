# CodeRelayBridge 0.6.50

## 中文

- 客户端与服务端更新窗口同时支持 GitHub、Gitee；可自动择优或手动选择，选择会保存。
- 自动模式先选最新的已验签版本，再对同版本通道限量测速；每通道最多读取 256 KiB，不在后台反复测速。
- 检查后明确显示“已是最新版”或“已有新版本”，确认前不安装；支持取消检查和下载。
- 下载显示百分比和已下载大小，校验及准备阶段单独提示。网络失败可切换同版本镜像；签名、哈希或包身份错误不会被回退掩盖。
- 私有更新的 GitHub 与 Gitee 授权分别保存在系统密钥库，不内置共享凭据，不向下载重定向转发令牌。

## English

- Client and server updates support GitHub and Gitee, with saved automatic or manual channel selection.
- Automatic selection prefers the newest signed version, then tests matching mirrors with at most 256 KiB read per channel. No background speed polling.
- The update window explicitly shows whether you are up to date or an update is available. Installation requires confirmation; checks and downloads can be cancelled.
- Downloads show percentage and transferred size, followed by separate verification and preparation stages. Network failures may fall back to an identical signed package; signature, hash, or identity errors stop the update.
- Private GitHub and Gitee credentials use separate system-keychain entries. No shared credentials are bundled or forwarded to download redirects.
