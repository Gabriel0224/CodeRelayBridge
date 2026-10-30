# Office document editing

This source integrates setup and management of a shared **ONLYOFFICE Docs** service on the CRB server, with an advanced option for an existing service. It is not included in the published 0.6.70 installers. Deploy a newly built server and upgrade target clients before enabling document saves.

| Format | Panel behavior |
| --- | --- |
| DOCX / XLSX / PPTX | Edit and save in the same format, up to 32 MiB |
| DOC / XLS / PPT / ODT / ODS / ODP / RTF | View with the document engine; download or convert separately for editing |
| Text / Markdown / CSV / HTML | Existing text editor and previews |
| PDF / images / audio / video | Existing previews |

## Install the shared engine (recommended)

Open **Relay Server → Document editing / 文档编辑 → Install and enable / 安装并启用**. New server setup also offers a recommended checkbox; the Windows server installer offers the same choice. CRB downloads a pinned ONLYOFFICE Docs Community 10.0.0 component and generates its private JWT secret. Only the server needs the engine. All connected Windows, Linux and Mac clients share it through the existing panel URL, including an HTTPS panel; no extra public port, hostname or certificate is required.

The first download needs internet access and several GB of free disk space. The runtime needs at least 4 GB available memory. Linux DEB installations include a root-owned narrow management helper; native authorization may install the distribution's `docker.io` package and enable its service. This does not grant the desktop user membership in the Docker group. Windows and Mac reuse local Docker Desktop; if absent, CRB downloads the official pinned 4.97.0 installer and verifies SHA-256. Complete the official installation, license and first-launch screens. If the system requires a restart or initialization takes longer, click **Install and enable** again afterward. Installation is retryable; an interrupted download or denied authorization does not enable an unusable editor or replace an existing manual configuration. CRB does not automatically accept third-party terms or reboot the computer.

The owned engine has networking disabled, four explicitly named data volumes and no published ports or host-directory mounts. A bounded stdin/stdout channel carries browser requests and signed document callbacks. Gateway pages receive an opaque sandbox, including direct visits; panel login cookies and edge credentials are not forwarded to the engine. This adapter is pinned to the tested engine interface. Windows/Mac runtime installer flows require native platform acceptance before release; Linux isolation tests alone do not establish their native installation behavior.

The document page provides status, start, stop and remove actions. Stop/removal and server stop/restart are refused while document sessions remain active. Close editors and confirm saves first. Removal deletes only the registered engine and its four validated volumes; a component image fetched by CRB is removed only when unused. Docker itself and unrelated containers/images are retained. Original user documents and failed recovery copies are retained. CRB server upgrades retain engine registration and volumes. A Linux service-package uninstall must first stop active editors and remove the owned engine through the document page; package removal is guarded while its document agent exists.

The Community engine uses the desktop editor interface; this does not add the vendor's separately licensed mobile editor. See [ONLYOFFICE Community](https://github.com/ONLYOFFICE/DocumentServer), [Docker installation](https://docs.docker.com/desktop/) and [Docker Desktop release notes](https://docs.docker.com/desktop/release-notes/).

## Connect an existing engine (advanced)

Select **Advanced: connect an existing engine / 高级：连接已有的独立文档引擎** in the document page.


1. Install a self-hosted ONLYOFFICE Docs service using the [official Docker installation guide](https://helpcenter.onlyoffice.com/docs/installation/docs-community-install-docker.aspx). Integration was tested with Docs Community **10.0.0**, image `onlyoffice/documentserver:10.0.0`, manifest digest `sha256:ca17889f10b11141cc6441836aebdd2835cf3e1b49681b2fd6e155c94b7850b3`. The engine is an independent service with its own license and resource requirements; CRB does not redistribute its source or binaries. See the [upstream project and license](https://github.com/ONLYOFFICE/DocumentServer).
2. Configure JWT authentication with a random secret of at least 32 characters. Keep it on the server; use the same value in ONLYOFFICE's `JWT_SECRET` and CRB. Do not use example/test secrets. See [official JWT configuration](https://helpcenter.onlyoffice.com/docs/installation/docs-configure-jwt.aspx).
3. In the native **Relay Server → 文档编辑 / Document editing** page, enable the connector and enter the engine's browser URL, optional internal service URL, optional panel callback URL, and shared JWT secret. An empty secret keeps the saved value. Settings are private to the server profile and take effect without restarting CRB.
4. Check the engine connection, then open a synthetic DOCX from the panel and save an edit. The browser must reach the engine browser URL; CRB must reach the internal service URL; the engine must reach CRB's callback URL. Use a separate hostname or port for the engine. Use HTTPS for both browser-facing services when the panel uses HTTPS. A successful health check alone does not verify the callback path.

ONLYOFFICE's request filtering may reject private-network addresses. For a private installation, configure the document service's network access deliberately for your own CRB endpoint using the upstream instructions. CRB does not change firewall rules, TLS trust, network routes or request-filter settings automatically. Keep the engine and panel behind your existing access controls. An unrelated public/cloud editor is not enabled by default.

## Editing and recovery

Open a remote Office file from the file list, a file citation or a conversation attachment. Native attachments resolve their already registered path through the app attachment index without transferring document bytes to the browser. DeepSeek content-addressed attachment copies open read only to preserve their digest; save them to a regular folder to edit. Attachments without a local file path must be downloaded to a regular folder first. Modern formats open in editing mode when the target client advertises document-save support. Older clients remain read only and should be upgraded. Existing SSH file access can also use the binary save helper.

**Save to computer / 保存到目标机** asks the engine to assemble the current document. CRB downloads the result, checks its type and size, sends bounded binary chunks to the selected computer, checks the original file's SHA-256 again and replaces it. Only a confirmed client digest produces “Saved to computer.” The editor's own internal autosave does not by itself confirm a save to the target computer.

Closing a changed document tab first waits for synchronization to the engine, saves it to the computer and asks the editor to check for uncommitted input. A failed or unconfirmed save keeps the tab open. A verified recovery copy can also be retained with **Keep copy and close / 保留副本并关闭**; reopening that file offers recovery. Hiding the preview dialog keeps its document tabs alive. Leaving the page with unconfirmed changes prompts the browser's normal warning. The final signed engine callback also saves the document after the editor disconnects.

If another program changes the original, CRB refuses to overwrite it. The complete edited copy remains in the server's private `document-editing` recovery directory and can be downloaded with **Download edited copy / 下载编辑副本**. A transfer failure can be retried with the same transaction. Resolve external changes using the downloaded copy; retries never silently switch the expected original hash or force an overwrite. Protect and back up this recovery directory like other private server data.

Confirmed closed sessions release their source and edited payloads. Short-lived receipts support unknown-result recovery and are collected later; failed edited copies are recovery data and are not automatically deleted as caches. Client partial transfers older than seven days are collected on the next transfer, while the server retains the complete failed edited copy. Engine settings cannot change while document sessions remain active.

Remote editor JavaScript runs in an opaque browser wrapper, isolated from the panel DOM and login cookies. Document fetches use narrowly scoped capabilities; callbacks also require the shared JWT signature. The shared secret is never returned by the panel API.

Official protocol references: [configuration](https://api.onlyoffice.com/docs/docs-api/usage-api/config/document/), [callbacks](https://api.onlyoffice.com/docs/docs-api/usage-api/callback-handler/), [saving](https://api.onlyoffice.com/docs/docs-api/more-information/faq/saving/).
