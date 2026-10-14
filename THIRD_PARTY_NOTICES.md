# Third-party components / 第三方组件

CRB's project license does not replace these upstream licenses. Keep their actual license texts when redistributing source or native packages. The package `LICENSES` directory includes distribution notices; pinned build dependencies are listed in `desktop_client/requirements.txt`. Linux credential-store dependencies are covered by the [supplemental SecretStorage/Jeepney notices](THIRD_PARTY_LICENSES.txt), also provided with the Release.

| Component | Upstream / notice | Role |
| --- | --- | --- |
| Python | https://docs.python.org/3/license.html | Bundled native interpreter |
| PySide6 / Shiboken / Qt | https://doc.qt.io/qtforpython-6/licenses.html | Native UI; upstream LGPL/GPL/commercial choices apply |
| PyInstaller | https://pyinstaller.org/en/stable/license.html | Native packaging; bootloader exception |
| psutil | https://github.com/giampaolo/psutil | Process ownership and lifecycle checks |
| PyYAML | https://github.com/yaml/pyyaml | Supported desktop configuration parsing |
| websocket-client | https://github.com/websocket-client/websocket-client | Desktop connection transport |
| pywin32 / pywinpty | https://github.com/mhammond/pywin32 ; https://github.com/andfoy/pywinpty | Windows system/terminal bindings |
| cryptography / cffi | https://github.com/pyca/cryptography ; https://cffi.readthedocs.io/ | Signed update verification |
| keyring and its platform dependencies | https://github.com/jaraco/keyring | System credential storage where applicable |
| markdown-it | [bundled license](web/vendor/markdown-it.LICENSE) | Markdown renderer |
| KaTeX | [bundled license](web/vendor/katex/LICENSE) | Mathematical rendering and fonts |
| xterm.js / FitAddon | [xterm license](web/vendor/xterm.LICENSE), [addon license](web/vendor/xterm-addon-fit.LICENSE) | Browser terminal |
| Playwright | https://github.com/microsoft/playwright | Development/browser regression dependency, not an end-user requirement |
| Microsoft WebView2 SDK/runtime | https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution | Original-App UI linkage on Windows; preserve SDK redistribution terms |

This is an index, not a replacement for the exact installed versions' license texts. Include notices for transitive dependencies when redistributing packages. Original Codex, DeepSeek Harness, Tailscale and ZeroTier applications/services are separate installations and are not relicensed by CRB.

以上为许可索引，不是对第三方组件的重新授权。完整安装包必须保留实际依赖版本的许可文本；打包器、Qt 库、WebView2 等的再分发要求分别适用。原 AI App 与虚拟局域网 App 由使用者另外安装。
