# Development and builds / 开发与构建

CRB uses Python and PySide6 for its native applications, and JavaScript, HTML, and CSS for the browser panel.

## Environment / 环境

Use Python 3.12 or later on the target operating system. Create a virtual environment and install the declared dependencies:

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r desktop_client/requirements.txt
```

Linux / macOS:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r desktop_client/requirements.txt
```

Activate the environment or use its Python executable in the commands below. Linux also needs the graphical system libraries required by Qt. The supported OS minimums and runtime versions are recorded in each package's `DEPENDENCIES.json`.

## Run / 运行

```sh
python -m desktop_client.run_client
python -m desktop_server.run_server
```

Run the applications in a graphical user session. Install and sign in to the original AI desktop apps separately. Configure connections through the UI; see the [user guide](docs/USER_GUIDE.en.md).

## Source layout / 源码目录

| Directory | Purpose |
| --- | --- |
| `crb/` | Application bridges, server, files, terminals, platform support and updates |
| `desktop_client/` | Native client |
| `desktop_server/` | Native server |
| `web/` | Browser panel |
| `release_tools/` | Build and packaging tools |

## Native packages / 原生安装包

Build on the target platform:

```sh
python -m desktop_client.native_build client --edition standard --core-source .
python -m desktop_client.native_build server --edition standard
python release_tools/package_release.py local --output releases
```

For a platform-only release, collect an output directory containing only that platform's verified installers and compatibility archives. Select the platform explicitly, for example `python release_tools/package_release.py collect --output releases --platform Windows-x64`. Repeat `--platform` for several changed platforms; omitting it retains the full-matrix check. Both roles and all editions for each selected platform remain required. Preserve unchanged platforms' versions and download links; update the introduction only from verified published packages.

仅发布有变化的平台时，通过 `--platform Windows-x64`、`--platform Linux-x64` 或 `--platform macOS-arm64` 显式选择本次范围，可重复传入。每个选中平台仍须完整核验客户端、服务端及对应版本类型，未选中的平台不重新打包、不虚增版本、不重复上传。介绍页按各平台实际已发布版本更新，Gitee 清理不能删除仍承载其他平台最新包的 Release。

Windows clients require the x64 `WebView2Loader.dll` from Microsoft's WebView2 SDK. Place it in the source root and retain Microsoft's redistribution notices. [WebView2 distribution](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution)

Native applications are produced under `desktop_client/dist`. Distribute the entire application directory or macOS `.app`, including its libraries. Packaging runs the application's isolated UI, dependency and lifecycle checks before creating the archive.

## Contributions / 贡献

Keep connection credentials, private keys, personal data and build outputs out of commits. Before submitting changes, run the local checks:

```sh
python tools/privacy_guard.py --staged
gitleaks git --staged --redact=100 --no-banner --config .gitleaks.toml
```

Project and third-party licensing terms are in [LICENSING.md](LICENSING.md).
