# Security / 安全

Pairing grants the server access to the target computer's files, terminals and supported application controls. Use only a server you trust, create a strong web password and restrict network access. Internet-facing deployments should use HTTPS and appropriate access controls.

Do not publish credentials, pairing configuration, private keys, personal conversations or unredacted diagnostic logs. When reporting an issue, include the program version, operating system, a description and sanitized error details.

Updates verify an Ed25519-signed manifest and the package's SHA-256, size, platform and edition. Third-party credentials are not included in installation packages. Private update tokens may be stored in the operating system's credential store.

If security software raises an alert, check the exact file or response and its source before allowing it. Do not disable protection or add a broad exception as a first response.

中文：配对后，服务端可访问目标电脑的文件、终端和应用控制。只连接可信服务端，使用独立强密码并限制访问范围。报告问题时去除账号、凭据、私人对话和个人路径；公网部署使用 HTTPS。
