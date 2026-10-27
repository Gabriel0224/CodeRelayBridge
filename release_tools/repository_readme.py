"""Render a host-specific introduction; runtime sources stay on shared main."""
import re

def latest_published_packages(records, edition):
    """Select each platform/role independently from verified publication receipts."""
    if edition not in ('standard',):
        raise ValueError('Unknown package edition')
    selected = {}
    for row in records:
        if row.get('edition') != edition:
            continue
        if row.get('published') is not True or row.get('verified') is not True:
            raise ValueError('Download tables require verified, published packages')
        version, platform, role = (row['version'], row['platform'], row['role'])
        extensions = {'Windows-x64': 'Windows-x64-Setup.exe', 'Linux-x64': 'Linux-x64.deb', 'macOS-arm64': 'macOS27-arm64.dmg'}
        if not isinstance(version, str) or not re.fullmatch('[0-9]+\\.[0-9]+\\.[0-9]+', version) or platform not in extensions or (role not in ('client', 'server')) or (row.get('tag') != 'v' + version):
            raise ValueError('Invalid published package identity')
        expected = f'Relay{role.capitalize()}-{version}-{edition.capitalize()}-{extensions[platform]}'
        if row['file'] != expected:
            raise ValueError('Published installer filename does not match its version/platform/role')
        key = (platform, role)
        number = tuple(map(int, version.split('.')))
        previous = selected.get(key)
        if previous is None or number > tuple(map(int, previous['version'].split('.'))):
            selected[key] = dict(row)
    expected = {(platform, role) for platform in ('Windows-x64', 'Linux-x64', 'macOS-arm64') for role in ('client', 'server')}
    if set(selected) != expected:
        raise ValueError('Missing verified downloads for a platform/role')
    return selected

def superseded_release_tags(records, edition):
    """A Release can be removed only when no platform/role still needs it."""
    selected = latest_published_packages(records, edition)
    required = {row['tag'] for row in selected.values()}
    known = {row['tag'] for row in records if row.get('edition') == edition}
    return sorted(known - required, key=lambda tag: tuple(map(int, tag[1:].split('.'))))

def update_downloads(text, provider, repository, records):
    """Update only the six download cells, keeping each actual published version."""
    edition = 'standard'
    selected = latest_published_packages(records, edition)
    host = 'https://github.com/Gabriel0224/' if provider == 'github' else 'https://gitee.com/gabriel0224/'
    platforms = {'Windows x64': 'Windows-x64', 'Linux x64（Ubuntu / Mint）': 'Linux-x64', 'Linux x64 (Ubuntu / Mint)': 'Linux-x64', 'macOS 27+ Apple Silicon': 'macOS-arm64'}
    labels = {'Windows-x64': 'Setup.exe', 'Linux-x64': 'DEB', 'macOS-arm64': 'DMG'}
    seen = set()

    def replace(match):
        label = match[1]
        platform = platforms[label]
        if platform in seen:
            raise ValueError('Duplicate platform row in the download table')
        seen.add(platform)
        cells = []
        for role in ('client', 'server'):
            row = selected[platform, role]
            url = host + repository + '/releases/download/' + row['tag'] + '/' + row['file']
            cells.append(f"[v{row['version']} · {labels[platform]}]({url})")
        return '| ' + label + ' | ' + ' | '.join(cells) + ' |'
    pattern = '^\\| (' + '|'.join((re.escape(label) for label in platforms)) + ') \\|[^\\n]*$'
    result = re.sub(pattern, replace, text, flags=re.M)
    if len(seen) != 3:
        raise ValueError('Missing platform row in the download table')
    return result

def render(text, provider, repository):
    if provider not in ('github', 'gitee') or not re.fullmatch('[A-Za-z0-9_.-]{1,100}', repository):
        raise ValueError('Unknown repository presentation')
    text = re.sub(' · \\[Gitee(?: 发行版| releases)?\\]\\(https://gitee\\.com/[^)]+\\)', '', text)
    text = re.sub('\\n\\[(?:Gitee 仓库|Gitee repository|备用源码仓库|Alternative source repository)\\]\\([^\\n]+\\)\\s*$', '\n', text)
    if provider == 'github':
        label = 'Gitee repository' if text.startswith('# CodeRelayBridge') and '[English]' not in text[:180] else 'Gitee 仓库'
        return text.rstrip() + f'\n\n[{label}](https://gitee.com/gabriel0224/{repository})\n'
    text = re.sub('https://github\\.com/Gabriel0224/(CodeRelayBridge[^/)\\s]*)', 'https://gitee.com/gabriel0224/\\1', text)
    text = text.replace('GitHub 发行版', '发行版').replace('GitHub releases', 'Releases')
    text = text.replace('网页左下角“反馈”可打开本仓库的 GitHub Bug 表单，需要登录 GitHub。', '网页左下角“反馈”可打开外部问题反馈表单，需要登录对应平台。')
    text = text.replace("Select Feedback in the panel's lower-left corner to open this repository's GitHub bug form. GitHub sign-in is required.", "Select Feedback in the panel's lower-left corner to open the external issue form. Sign in to that service to submit a report.")
    text = re.sub('\\n## (?:同时使用 Tailscale 与 v2rayN|Using Tailscale with v2rayN)\\n.*?(?=\\n## )', '', text, flags=re.S)
    text = text.replace('Codex 和 DeepSeek Harness 桌面应用', '已支持的桌面应用')
    text = text.replace('Codex and DeepSeek Harness desktop apps', 'supported desktop apps')
    text = text.replace('Codex／DeepSeek Harness', 'AI').replace('Codex / DeepSeek Harness', 'AI')
    text = text.replace('Codex／DeepSeek', 'AI').replace('Codex', 'AI app').replace('DeepSeek Harness', 'AI app')
    english = '[English]' not in text[:180]
    label = 'Alternative source repository' if english else '备用源码仓库'
    return text.rstrip() + f'\n\n[{label}](https://github.com/Gabriel0224/{repository})\n'

def _with_network_guidance(guide, original, english):
    match = re.search('^## 7\\. .*?(?=^## 9\\. )', original, re.M | re.S)
    if match is None or not re.search('^## 8\\. ', match[0], re.M):
        raise ValueError('The source guide is missing private-network setup steps')
    network = re.sub('^### 7\\.1 .*?(?=^## 8\\. )', '', match[0], flags=re.M | re.S)
    network = network.replace('Codex', 'AI app' if english else '原桌面应用')
    guide = re.sub('^## ([789])\\. ', lambda m: f'## {int(m[1]) + 2}. ', guide, flags=re.M)
    marker = '## 9. '
    if guide.count(marker) != 1:
        raise ValueError('The destination guide has an unexpected structure')
    return guide.replace(marker, network + marker, 1)

def render_guide(text, provider, english=False):
    """A provider-local, product-focused guide; never alter runtime identities."""
    if provider == 'github':
        return text
    if provider != 'gitee':
        raise ValueError('Unknown documentation provider')
    if english:
        return _with_network_guidance("# CodeRelayBridge user guide\n\n[中文](USER_GUIDE.zh-CN.md) · [Home](../README.en.md)\n\n## 1. Choose the roles\n\nInstall Server on one always-online computer, and Client on each computer whose supported desktop application you want to access. Open the panel in a phone, tablet or computer browser. Server does not include a model account or replace the original application; install and sign in to that application separately. Available actions depend on its version and platform.\n\n## 2. Check the network\n\nUse a trusted network where these devices can reach each other. Guest Wi-Fi or device isolation may prevent access. Across locations, join the relevant devices to the same Tailscale or ZeroTier private network first; see sections 7 and 8. CRB does not provide a public server, shared account or network service. Browser and clients connect to Server, normally on TCP 8799; do not expose desktop debug ports or a model API. Do not disable firewalls or certificate verification.\n\n## 3. Install\n\nUse this repository's Releases page, linked from Home. Match the system, role and edition. Check the supplied SHA256SUMS before running an installer.\n\n- Windows x64: run Setup.exe. Start from the Start menu. Uninstall.exe beside the main program opens the uninstaller; Settings can also uninstall it. Keep all EXE and DLL files together.\n- Ubuntu / Mint x64: install the DEB with the system package installer, or `sudo apt install ./package.deb`. System dependencies are declared in the package. Do not start the desktop UI as root.\n- Apple Silicon: open the DMG and copy the application into Applications. These packages require macOS 27 or later and do not support Intel Macs. Approve only a verified application using the normal system procedure; do not disable system protection globally.\n\nPython, Qt and packaged runtime dependencies are included. Operating-system graphics libraries may still be needed. Standard and integrated editions have separate packages; optional account-management features require their separate application and repository permission.\n\n## 4. Configure Server\n\n1. Open Server and choose Settings & setup.\n2. Enter this computer's reachable network IP and listening port (normally 8799).\n3. The panel URL is generated from the IP and port. Verify it; edit only if a separately configured address requires it. Replace any placeholder with your own address. A listening wildcard is not a browser address; loopback addresses only work on that same computer.\n4. Create your own panel login with a unique password of at least eight characters. No default address or password is supplied.\n5. Create the server configuration, then Start service. Review any system request for the program's own port or dependencies before approving.\n6. Open the panel URL locally and log in, then repeat from another device. Only the second check demonstrates network access.\n\n## 5. Pair Client\n\n1. Install and sign in to the supported original desktop application on the target computer.\n2. Open Client and select Pair / add app.\n3. Enter a recognizable computer name, the verified panel URL and the panel login created on Server.\n4. Select the applications to connect. Normally leave the advanced backend address equal to the panel URL.\n5. Check the server, then Pair & connect. The pairing window closes on success.\n6. Check both server and desktop-application connection states. Start the original application or reconnect an existing bridge if necessary; do not repeatedly pair a device that is already paired.\n\nThe panel password is not an operating-system, remote-login or model API password. After pairing, Client uses an issued device credential. Pairing allows access to that computer's conversations, files and terminals; pair only with a server you trust. Never publish credentials, configuration files or private logs.\n\n## 6. Use the panel\n\nChoose an application, computer, project and conversation. Workspace combines links to conversations from different sources. Files and terminals operate on the selected target computer, not the phone. The model, Fast mode and access controls are shown only when supported; changing settings does not send a message.\n\nQuestions stay above the input area. Use Previous / Next and the question index; paging preserves drafts and does not answer. Submit the group when complete. Closing or temporarily skipping the panel is not an answer and does not stop the task. Native synchronization requires a supported original application and its local channel. Progress should continue while awaiting answers.\n\nQueued-message controls appear on the left of the settings row when a queue exists. Expand to edit, steer or delete. A paused queue requires its own Continue action. Reading history should not jump to new output; use Jump to latest when needed. Reload refreshes the page resources, not the server or original application; save drafts and file edits first.\n\n## 7. Language, theme and font\n\nChinese system/browser UI languages default to Chinese; others default to English. A manual choice is saved locally. Client and panel provide language/theme controls at the lower left; Server provides language in settings. The Font button opens font size and Reset. Conversation content, file names and terminal output are not translated.\n\n## 8. Disconnect and unpair\n\nClose & disconnect exits Client and its bridges, not the original application. If minimize-to-tray is enabled, closing the window keeps the bridge running; use the explicit exit action to disconnect. Pausing a bridge is not closing the original application. Unpair revokes that application's device access on Server. Local-only disconnect is an emergency option when Server cannot be reached; complete remote revocation when connectivity returns. Stopping Server disconnects panels and their terminals, not model tasks.\n\n## 9. Updates and troubleshooting\n\nCheck CRB updates in Client or Server. Automatic channel selection compares the same signed version with bounded traffic; a manual channel can also be selected. Confirm or cancel before installation. Integrated packages may require repository read permission stored in the system credential vault. Update only from verified packages; pairing and settings are retained. Quit Relay before manual replacement; the original desktop application need not restart.\n\nIf access fails, verify the actual IP, port, service state, device isolation and proxy exceptions. Direct trusted-network requests to the real server address. Do not assume a successful ping means the service port works. Check connection diagnostics before pairing again. Share only redacted error details through the feedback entry. For any externally reachable deployment, configure HTTPS and access control separately and assess file/terminal exposure.\n\nProject-owned code is for noncommercial use under the repository license. Third-party licenses remain unchanged.\n", text, english)
    return _with_network_guidance('# CodeRelayBridge 使用指南\n\n[English](USER_GUIDE.en.md) · [返回首页](../README.md)\n\n## 1. 选择角色\n\n一台保持联网的电脑安装服务端，每台需要接入的电脑安装客户端；手机、平板和电脑通过浏览器打开面板。服务端不提供模型账号，也不代替原桌面应用；请另外安装并登录受支持的原应用。可用功能取决于原应用的版本和平台。\n\n## 2. 确认网络\n\n使用设备间可互通的可信网络。访客无线网络或设备隔离可能阻止访问；跨地点时先让相关设备加入同一个 Tailscale 或 ZeroTier 私有网络，具体步骤见第 7、8 节。CRB 不提供公共服务器、共享账号或网络服务。浏览器和客户端连接服务端，默认使用 TCP 8799；不要暴露桌面调试端口或模型接口。不要关闭防火墙或证书校验。\n\n## 3. 安装\n\n从首页链接的本仓库发行版下载，核对系统、角色、版本类型和 SHA256SUMS。\n\n- Windows x64：运行 Setup.exe，从开始菜单启动。主程序旁的 Uninstall.exe 可打开卸载器，也可在系统设置卸载；不要单独移动 EXE 或 DLL。\n- Ubuntu / Mint x64：用系统安装器打开 DEB，或执行 `sudo apt install ./安装包文件名.deb`；包内声明系统依赖。不要以管理员身份运行桌面界面。\n- Apple Silicon：打开 DMG，将应用复制到应用目录。当前包要求 macOS 27 或以上，不支持 Intel Mac。确认来源后仅按系统流程批准这一个应用，不要全局关闭系统防护。\n\n完整包附带 Python、Qt 和打包运行依赖，操作系统图形库仍可能需要单独补齐。普通版和集成版使用不同安装包；可选账号管理能力需要另外安装对应应用，并取得相应仓库访问权限。\n\n## 4. 设置服务端\n\n1. 打开服务端，进入“连接与首次设置”。\n2. 填写本机可访问的网络 IP 和监听端口，通常保留 8799。\n3. 面板地址依据 IP 和端口自动生成，请核对；只有另行配置了访问地址才手工修改。占位符要替换为自己的地址。监听所有网卡的通配地址不能直接用于浏览器，本机回环地址仅供同一电脑访问。\n4. 自行创建网页登录账号及独立密码，密码至少 8 位。新安装不提供默认地址或密码。\n5. 点击“创建服务器配置”，再“启动服务”。若出现系统授权，请确认只涉及本程序的端口与依赖后批准。\n6. 先在服务端本机登录面板，再用另一台设备访问并登录；第二步成功才证明网络访问成立。\n\n## 5. 配对客户端\n\n1. 在目标电脑安装并登录受支持的原桌面应用。\n2. 打开客户端，选择“配对 / 添加应用”。\n3. 填写容易区分的电脑名称、已验证的面板地址，以及刚在服务端创建的网页登录账号和密码。\n4. 选择希望接入的应用；通常保持“高级：后台服务器地址”和面板地址一致。\n5. 检查服务器，通过后“配对并接入”；成功后配对窗口自动关闭。\n6. 分别核对服务器和桌面应用状态；需要时启动原应用或重新连接已有桥接，不要对已配对的设备反复配对。\n\n网页登录密码不是系统密码、远程登录密码或模型密钥。配对后客户端使用服务端签发的设备凭据。配对意味着允许访问该电脑的对话、文件和终端，只连接可信服务端，不要公开凭据、配置文件或原始日志。\n\n## 6. 使用面板\n\n选择应用、电脑、项目和对话。工作区汇集不同来源的对话链接；文件和终端作用于选中的目标电脑，而非手机本地。模型、加速和访问权限只在具备能力时显示，调整设置不发送消息。\n\n待回答的问题固定在输入框上方，通过题号和“上一题 / 下一题”切换；翻页保留草稿且不提交答案，填写同组问题后再提交。关闭或暂时跳过只是收起界面，不代表回答，也不停止任务。原生联动需要受支持的原应用及本机通道。等待回答不应停止进展同步。\n\n有队列时，“待发送”及其状态在设置行左侧显示，展开后可编辑、引导或删除；暂停的队列使用独立“继续”按钮。阅读历史时不应自动跳转，用“跳至最新”回到新内容。“重载”重新读取网页资源，不重启服务或原应用；先保存输入草稿和文件修改。\n\n## 7. 语言、主题和字体\n\n系统或浏览器首选语言为中文时默认中文，其他语言默认英文，手动选择单独保存。客户端及网页左下角有语言与主题入口，服务端在设置中选择语言。“字体”按钮打开字号条和重置。聊天正文、问题原文、文件名和终端输出不会翻译。\n\n## 8. 关闭与解除配对\n\n“关闭并断开”退出客户端及其桥接，不关闭原应用。启用最小化到托盘后，关窗口仍保持接入，彻底退出请用明确的退出操作。暂停桥接不等于关闭原应用。解除某应用配对会请求服务端撤销授权；“仅断开本机”是服务器不可达时的应急选项，网络恢复后应补做撤销。停止服务端会断开网页和面板终端，不停止原应用的模型任务。\n\n## 9. 更新与排障\n\n客户端和服务端提供“检查 CRB 更新”。自动通道对同一已签名版本限量测速，也可手动选择通道；有新版本时先确认或取消，再安装。集成版可能需要仓库读取权限，可保存在系统密钥库中。仅使用来源、签名及校验正确的安装包，升级保留配对和设置。手动替换前退出 Relay，无需重启原桌面应用。\n\n访问失败时依次核对实际 IP、端口、服务是否启动、设备隔离和代理例外；可信网络中的面板地址应直接访问。能连通主机不等于服务端口可用，先看连接诊断，不要反复配对。反馈前遮盖密码、地址和个人内容。需要外部访问时，另外配置 HTTPS 与访问控制，并评估文件和终端权限。\n\n项目自有代码仅限非商业用途，具体以仓库许可为准；第三方许可保持不变。\n', text, english)
