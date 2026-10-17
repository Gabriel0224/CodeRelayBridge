# CodeRelayBridge (CRB) user guide

[简体中文](USER_GUIDE.zh-CN.md) · [Home](../README.en.md)

This guide covers first-time setup using complete native packages. No prior Python installation, SSH configuration or hand-written connection file is required. Native clients, native servers, and the web panel support Chinese and English.

## Language

On first launch, a Chinese system UI language selects Chinese; all other languages select English. Native apps use the OS's preferred UI language, not its number/date region. The browser uses its preferred language list. An explicitly saved choice takes priority on later launches.

- Client: use Language in the lower-left corner.
- Server: open Settings & setup and select Language.
- Web panel: use the language list beside the theme controls in the lower-left corner.

Only 中文 and English are available. Choices are saved locally for each installation/browser. Switching does not reconnect devices or resend messages. Chat text, custom question options, file/project names, drafts, logs, and terminal output stay unchanged. Native Codex terminology is used for corresponding features; CRB-specific features have their own translations.

| Chinese label used in this guide | English UI label |
| --- | --- |
| 连接与首次设置 | Settings & setup |
| 配对 / 添加应用 | Pair / add app |
| 配对并接入 | Pair & connect |
| 解除配对 | Unpair |
| 检查 CRB 更新 | Check for CRB updates |

## 1. Understand the three roles

| Role | Where it runs | Purpose |
| --- | --- | --- |
| CRB server | An available Windows, Linux or Mac computer | Hosts the web panel, login, device enrollment and message relay |
| CRB client | Every computer whose desktop Apps you want to access | Bridges that computer's supported desktop AI Apps |
| Browser | Phone, tablet or computer | Connects to the server and operates enrolled conversations |

The server is not a model provider. Install and configure Codex or DeepSeek Harness, including its account/API setup, separately on each client computer. Original Claude desktop Code sessions are not yet integrated; do not treat them as supported.

One computer may run both server and client. Installing only the server does not enroll its local AI Apps. A phone needs a browser, plus the virtual-network App when accessing across networks.

## 2. Prerequisite: a reachable local or virtual LAN

Choose one option; installing both VPN products is unnecessary:

- **Same trusted physical LAN:** the server, clients and browser devices can reach one another. Guest Wi-Fi or device isolation can prevent this even on the same router.
- **Different locations/mobile data:** Tailscale is the recommended starting point; ZeroTier is an alternative. Join the devices to the same virtual private network first.

CRB does not bundle a VPN, cloud tunnel, public server or shared account. Tailscale/ZeroTier provides connectivity, not the CRB service or its authentication. [Tailscale connection requirements](https://tailscale.com/docs/how-to/connect-to-devices)

Clients normally initiate outbound connections to the server. Do not expose client SSH, model APIs or debug ports. Browsers also connect to the server. A basic installation needs the server's **TCP 8799** reachable; use your chosen port everywhere if you change it.

## 3. Download and install

Choose your operating system and role at [GitHub Releases](https://github.com/Gabriel0224/CodeRelayBridge/releases). Standard packages are public; Cockpit integrated packages are not distributed in this public repository. Client and server packages are different applications.

- **Windows x64:** run the matching `*-Setup.exe` and complete the per-user installation. Open the client or server from the Start menu. To uninstall, open `Uninstall.exe` beside the main executable, or use Windows Installed apps. Quit the corresponding Relay and stop its service before upgrading or uninstalling; Codex and DeepSeek may stay open. Do not move individual EXE or DLL files.
- **Ubuntu / Mint (Linux x64):** download the matching `.deb`, run `sudo apt install --reinstall ./PACKAGE.deb` to resolve declared system dependencies, then open it from the desktop application menu. The package manager owns `/opt/coderelaybridge/`; personal settings and pairing stay in your user directory. Never run the desktop UI with sudo. Quit a previous portable Relay before migration; use the server UI's existing service takeover option for an old configured service.
- **macOS Apple Silicon:** open the DMG and copy `Relay Server.app` / `Relay Client.app` to your applications directory. Current packages require **macOS 27 or later** and do not support Intel Macs. Check the selected Release's requirements.

Python, Qt and bundled runtime dependencies are included. OS libraries and graphics drivers are still required. On Linux, use “网络与依赖” (Network and dependencies) to inspect and repair supported missing system libraries with local authorization. Arbitrarily old systems are not guaranteed compatible.

If a security product warns or a checksum differs, investigate the exact package and official Release first. Do not disable security software or whitelist the whole site. Approve an unsigned application through the OS's per-app process only after verifying its origin; never disable Gatekeeper globally.

## 4. Configure the first server

### 4.1 Find the address

On the server computer, view the active network adapter's IPv4 in network settings. Alternatives: `ipconfig` on Windows, `ip -4 addr` on Linux, or network details in macOS System Settings.

Replace every `SERVER_IP` placeholder below with **your own server's actual address**. For Tailscale/ZeroTier, use that server's virtual IPv4 instead; see sections 7–8. Placeholders are not working addresses.

### 4.2 Configure using the UI

1. Launch `RelayServer`; open **“连接与首次设置”** (Connection and first setup).
2. **“监听 IP”** (Listen IP): enter the server's LAN IPv4, or its virtual-network IPv4. Connect the network before starting the server.
3. **“监听端口”** (Port): normally keep `8799`.
4. **“网页访问地址”** (Web URL): enter `http://SERVER_IP:8799`, replacing the placeholder. Use this HTTP URL only inside a trusted LAN or encrypted private virtual network.
5. Create your own **“网页登录账号 / 密码”** (Web login username/password). Minimum password length is 8; prefer a separate random long password. New installations have no preset account, password or server address.
6. Click **“创建服务器配置”** (Create server configuration).
7. Keep **“启动时自动配置系统端口与依赖”** enabled and click **“启动服务”** (Start service). Review and approve requested local firewall/dependency authorization.
8. Open your URL on the server and verify login.
9. **Open that same URL and log in from another computer/phone.** Only this confirms remote LAN access.

`127.0.0.1` means the device opening the URL; a loopback-only server is unreachable from other devices. `0.0.0.0` means listen on all IPv4 adapters, not a browser destination. Binding the single intended adapter IP is the safer beginner setup.

Automatic repair only handles this application's rules. It cannot override organization policies, third-party firewalls, Wi-Fi isolation, router settings or cloud security groups. An address change/disconnected VPN may prevent startup when bound to the old IP. Consider a DHCP reservation or recheck listen settings; never open all ports to everyone for troubleshooting.

## 5. Enroll each client using the UI

1. Install and sign in to supported Codex / DeepSeek Harness desktop Apps on this computer. App/platform versions affect bridge capabilities.
2. Install and launch the Standard `RelayClient`.
3. Open **“配对 / 添加应用”** (Pair / Add applications).
4. Set **“电脑名称”** (Computer name), e.g. “Work computer”, for the browser list.
5. Set **“网页面板地址”** (Panel URL) to the server URL verified above.
6. Enter the **web login** username and password you created on that server.
7. Select installed applications to enroll; Codex and DeepSeek may both be selected.
8. Leave **“高级：后台服务器地址”** (Advanced: backend server URL) unchanged in a basic deployment. It normally matches the panel URL; only an existing split reverse-proxy setup may require otherwise.
9. Click **“检查服务器”** (Check server), then **“配对并接入”** (Pair and connect), and wait for server/desktop connection status.
10. If connected to the server but waiting for the desktop App, launch that App and check diagnostics. Do not repeatedly enroll. **“启动 App”** launches the App; **“重新连接”** reconnects its bridge.

**Do not enter a Windows/Microsoft, SSH, sudo password or DeepSeek API Key here.** The web password is used during enrollment and is not retained by the client; subsequent sync uses the issued device credential. Never share that credential or connection configuration in an Issue.

Pairing authorizes the panel to access this computer's conversations, files and terminal. Enroll only trusted servers. Some desktop operations need an active signed-in graphical session. CRB does not automatically install missing original Apps.

## 6. Browser access, quitting and unpairing

Pending questions stay above the composer. Use Previous/Next and the question counter to move between them, then submit the completed group. Paging does not send answers and keeps your selections and custom text. Execution updates continue while a question is pending; answers accepted in the original app also dismiss the corresponding panel questions.

Open the server URL, sign in with its web credentials, then select application, computer, project and conversation. A workspace groups conversations across sources. Files and terminals operate on the selected computer, not on your phone.

- **“关闭并断开”** (Close and disconnect) stops client-owned bridges and disconnects; it does not delete the original AI Apps or their history.
- **“关闭窗口时最小化到托盘（保持接入）”** keeps enrollment active when closing the window. Use the explicit disconnect/quit action for full exit. A missing tray cannot be used to maintain a hidden, inaccessible client.
- Pausing a bridge neither closes the original App nor revokes device authorization.
- Unpair the selected application in pairing management to stop its bridge and request server-side revocation; the web password is not needed again.
- **“仅断开本机”** (Disconnect locally only) is an emergency option when the server is unreachable. It does not revoke server authorization; finish revocation later.
- Server **“停止服务” / “重启服务”** disconnects the panel and its terminals, not original model tasks. Closing its management UI is different from explicitly stopping its service.

## 7. Recommended: Tailscale across networks

A “tailnet” is your private virtual network. This simple deployment needs no router port-forwarding, exit node, subnet router or Funnel. [Official quickstart](https://tailscale.com/docs/how-to/quickstart)

1. Install the matching App from [Tailscale downloads](https://tailscale.com/download) on the server, every remote client computer and the phone/tablet using mobile data.
2. Sign in and join the **same tailnet**. Your own devices can use one identity. Invited users must join your network rather than an unrelated newly created one.
3. Confirm devices are online and any required device approval is complete. Custom access policies must allow clients/browser devices to reach the CRB server on TCP 8799.
4. Copy the **server's Tailscale IPv4** from its device entry. Do not invent a `100.x` address; this is not an Internet-public address.
5. For a new CRB server, use that IPv4 as Listen IP and `http://SERVER_TAILSCALE_IP:8799` as Web URL, replacing the placeholder. Follow section 4. For an existing server, back up configuration and schedule the brief listen-setting change; do not recreate it over existing pairing records.
6. Pair clients to this same URL; open it in the browser too.
7. Switch a phone from Wi-Fi to mobile data, **keep Tailscale connected**, and verify login plus a target conversation.

Installing Tailscale only on the server does not connect an ordinary phone browser automatically. Without a separately configured subnet router/other access arrangement, each participating remote device needs connectivity to the tailnet. A successful ping does not prove that CRB is listening or its TCP port is allowed. [Service/connectivity prerequisites](https://tailscale.com/docs/how-to/connect-to-devices)

MagicDNS can identify a server. If enrollment rejects an HTTP hostname URL, use its real Tailscale IPv4 as in this guide, or first configure trusted HTTPS. Never disable certificate verification. The private-LAN setup does not need public exposure or Funnel.

### 7.1 Using Tailscale with v2rayN

These steps target **Windows + v2rayN + private Tailscale access**. Menu names may vary. With TUN off, follow **A → C**; if you need TUN, follow **A → B → C**.

Before starting, back up v2rayN configuration. Confirm Tailscale is online and this setup is not using an exit node. Record the server's actual Tailscale IP, port (default `8799`), and optional full MagicDNS name. These private-address rules do not apply to a separate public HTTPS endpoint.

#### A. Configure proxy exceptions (check even without TUN)

**Turning TUN off does not guarantee unaffected access.** Start with the panel's proxy settings:

1. Open v2rayN **Settings → Parameter settings → Proxy settings → Exceptions**.
2. Keep existing entries. Append the server's **actual Tailscale IP**, comma-separated; append the actual full MagicDNS hostname if used.
3. Enter only the IP/hostname, not `http://`, a port, path, or CIDR range. Retain existing loopback exceptions such as `localhost` and `127.0.0.1`.
4. Save and reapply your original system proxy mode. With PAC or a browser proxy extension, set the same destinations to DIRECT in that configuration too; OS exceptions alone may not apply.
5. **TUN off**: keep it off and go to C. Tailscale process rules are normally unnecessary. **TUN on**: continue to B.

#### B. Exclude TUN interception (only with TUN enabled)

1. Open **TUN settings → Route exclusion addresses**.
2. Keep existing entries and append this value. If using Tailscale subnet routes, also append the actual advertised destination subnets:

   ```text
   100.64.0.0/10,fd7a:115c:a1e0::/48
   ```

3. Save, reload v2rayN's TUN/core, and verify using C. **If login works and the bridge is online, stop here; no additional rules are needed.**
4. If problems remain, open **Routing settings** and add two **separate** rules to the currently used rule set:

   | Rule | Field | Value | Outbound |
   | --- | --- | --- | --- |
   | Tailscale destinations | IP | The two CIDR ranges above, entered separately as required by the editor | `direct` |
   | Tailscale service | Process name | `tailscaled.exe` | `direct` |

5. Move both rules before the catch-all proxy rule, confirm they are enabled and the rule set is selected, then save and reload the core. Do not combine IP and process fields in one rule.
6. Repeat C and check v2rayN logs for direct-rule matches. If the core cannot match processes, a saved rule does not prove success; disable TUN to recover access, then consult that core's documentation.

#### C. Verify the result

1. Confirm Tailscale remains online. For path B, also confirm TUN is enabled again.
2. Open the original panel URL in a browser. Verify login, a target conversation, and the target computer's bridge status.
3. If access still fails, run these PowerShell checks, replacing `SERVER_TAILSCALE_IP` and the port with your values:

```powershell
tailscale status
tailscale ping SERVER_TAILSCALE_IP
Test-NetConnection SERVER_TAILSCALE_IP -Port 8799
curl.exe --noproxy "*" --max-time 10 http://SERVER_TAILSCALE_IP:8799/
```

Success means the real TCP port is reachable, browser login works, and the bridge recovers, not merely that ping succeeds. `--noproxy` bypasses HTTP proxying, not TUN. HTTP 401/login redirects show reachability only; do not use `-k` to bypass HTTPS certificate validation.

<details>
<summary>If problems remain: DNS, process identities, and mobile limits</summary>

- **IP works, hostname fails**: check that system/Tailscale DNS handles MagicDNS rather than remote proxy DNS or FakeIP. Domain direct rules do not replace correct DNS routing.
- **Tailscale itself goes offline**: Windows networking uses `tailscaled.exe`, not the CLI `tailscale.exe` or GUI `tailscale-ipn.exe`. Linux usually uses `tailscaled`; macOS GUI builds use a network extension, not the Windows name.
- **Different rule purposes**: TUN address exclusions, core IP direct rules, and service process rules do not replace one another. Do not force all `100.*` addresses, private networks, browser traffic, or Codex traffic to direct.
- **Exit nodes/mobile**: this is not a design for two full tunnels. Normal iPhone/iPad/Android VPN modes generally allow only one active VPN; desktop process rules cannot solve that. Do not disable firewalls for troubleshooting.

Official references: [System proxy/PAC](https://github.com/2dust/v2rayN/wiki/Description-of-system-proxy-routing) · [v2rayN fields, TUN, and rule order](https://github.com/2dust/v2rayN/wiki/Description-of-some-ui) · [Custom routing format](https://github.com/2dust/v2rayN/wiki/Description-of-custom-routing-rules) · [Daemon identities](https://tailscale.com/docs/reference/tailscaled) · [MagicDNS](https://tailscale.com/docs/features/magicdns) · [VPN coexistence limits](https://tailscale.com/docs/reference/faq/other-vpns)

</details>

## 8. Alternative: ZeroTier across networks

A ZeroTier Network ID identifies the network to join; **it is neither a CRB URL nor a web password**. Use a private network and authorize devices individually. [Official network setup](https://docs.zerotier.com/start/)

1. Sign up at [ZeroTier Central](https://central.zerotier.com/), create a private network, and copy its 16-character Network ID.
2. Install ZeroTier from [the official download site](https://www.zerotier.com/download/) on server/client computers and phones/tablets.
3. Join the same Network ID from each device's App.
4. In Central's member list, compare each Node ID against your actual device, then **Authorize** that device. Never authorize unfamiliar nodes.
5. Verify a Managed IPv4 was assigned to the server and other devices. Allow managed IPs/routes; avoid overlapping the virtual subnet with existing physical LANs.
6. Set the new CRB server's Listen IP to its Managed IPv4 and Web URL to `http://SERVER_ZEROTIER_IP:8799`, replacing the placeholder. Start as in section 4 and enroll clients as in section 5.
7. Check the OS firewall and ZeroTier flow rules allow server TCP 8799. Test the page from another device.
8. Switch the phone to mobile data and repeat while its ZeroTier VPN remains connected.

CRB cannot fix unauthorized members, missing Managed IPv4 addresses, the wrong Network ID or restrictive virtual-network rules. [Official network/member management](https://docs.zerotier.com/networks/)

## 9. Updates, backups and troubleshooting

### Updating

Click **“检查 CRB 更新”** (Check CRB updates) in the client/server UI. Verify platform, role and edition before confirming installation. The updater checks a signed manifest, size and SHA-256. Relay connections and panel terminals disconnect briefly; original model App tasks should not be stopped.

Older installations without this button need a first manual full-package upgrade. Preserve user data rather than re-enrolling or deleting configuration. Cockpit updates require read access to the private repository; Standard updates do not require GitHub authorization.

### Backups

Back up server user data/pairing configuration and client connection configuration in your own encrypted storage, never in Git. Panel data is separate from original desktop chat storage; back up the original App before uninstalling it.

### Diagnostic order

| Symptom | Check first |
| --- | --- |
| Works locally, not on another device | Loopback-only listening, IP/port, firewall, guest Wi-Fi isolation |
| Fails remotely/on mobile data | Same online Tailscale/ZeroTier network, device authorization and access rules |
| 401/login failure | Your server's web credentials, not SSH credentials; additional upstream login layer |
| HTTPS error | System time, trusted certificate, matching hostname; never bypass validation |
| Computer online, no desktop conversation | Original App installed/signed in/running, bridge diagnostics and supported version |
| Linux UI will not start | Graphical session, completed DEB installation, supported OS and declared dependencies, Network and dependencies |
| Offline after quitting the client | Expected; keep the client in tray and computer awake for continued access |

On Windows, run `Test-NetConnection SERVER_IP -Port 8799` after replacing the placeholder. Elsewhere a browser or `curl -I http://SERVER_IP:8799/` can test an HTTP response. A 401/login redirect generally proves reachability, not successful authentication. Redact credentials, personal paths and real conversations before reporting issues.

## 10. If you really need an Internet-public website

This is a separate deployment, not the default LAN solution. Configure trusted HTTPS, authentication, access restrictions, reverse proxy and any cloud security group intentionally. Public-publishing features such as Tailscale Funnel also need a permissions/exposure assessment. A VPN is not a public website.

CRB exposes file editing, terminal execution and target-App management. A login page alone is not a reason to expose it carelessly. Do not port-forward original model APIs, SSH or local debug interfaces to the Internet.
