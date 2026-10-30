# Local web previews

This source adds interactive previews of web services running on a paired computer's loopback interface. It is not included in the currently published 0.6.70 installers. Upgrade both the server and the target client when deploying a new build. No document engine or extra browser package is required.

## Open a page

Click an HTTP/HTTPS `localhost`, `127.0.0.1` or `[::1]` link in a conversation or Markdown file preview. The link keeps the computer belonging to that message or file, including workspace messages, even when another chat is selected. URLs shown in code blocks receive a preview button. For a manual address, select **Local web / 本地网页** in the extensions panel or preview toolbar, choose the computer, and enter a full URL or a port such as `3000`.

Local pages share the existing preview tab strip with files and Office documents. On desktop, **Split view / 并排查看** keeps chat beside the preview; **Full view / 集中查看** restores the larger preview. Switching tabs or view modes keeps existing sessions alive. Local links requesting a new window open a preview tab by default; use **Separate window** explicitly to open a forwarded page outside the panel. Static HTML files remain script-free; their local targets are available through the preview buttons below the file.

The selected client's loopback service is forwarded through an authenticated, on-demand client connection. The target can keep listening only on loopback. The computer or phone viewing CRB does not connect to its own localhost, and there is no need to publish each application port. The target computer must remain connected and the web service must already be running; CRB does not start model tasks or launch arbitrary web commands.

The preview supports interactive HTTP requests, SSE streams, WebSockets, relative assets, JavaScript modules and common Workers. **Refresh / reconnect** reuses an active preview and its cookies, storage and asset cache. **Separate window** creates a separate preview session, so it can remain open after closing the embedded preview. Closing the embedded preview immediately releases its forwarding streams without stopping the target application. An independent window's abandoned session is collected within about 25 seconds after its last view disconnects. Refresh/navigation receives this short grace period. Sessions have an eight-hour absolute limit, with at most 32 active previews per server.

## Session routing

Each preview is bound to one paired client identity, scheme and loopback port. Two computers using `127.0.0.1:3000` remain separate. Client connections use the existing device credentials and normal TLS certificate verification. A temporary preview URL is a session capability; keep it private. HTTPS panels use HTTPS/WSS preview URLs, including for HTTP-only services on the target computer. If a reverse proxy fronts CRB, it must forward WebSocket upgrades on both `/agent/local-preview` and `/local-preview/` routes. Validate this through the actual proxy during deployment; direct isolated TLS tests do not establish a production proxy configuration.

## Reverse proxies with a separate login gateway

A working main panel does not establish that embedded previews work. A proxy-wide `X-Frame-Options: DENY` blocks the preview document, missing WebSocket upgrade headers disconnect its view lease, and a second login-cookie check can reject its scripts and data requests. The opaque sandbox intentionally does not give preview scripts the panel's origin. Its subrequests cannot rely on a `SameSite=Strict` or `Lax` panel cookie, and the adapter omits panel credentials from fetch requests.

Keep the normal panel, login and `/api/local-preview/open` routes behind their existing authentication. For the two forwarding routes, delegate authentication to CRB: `/local-preview/<48 lowercase hex characters>/...` requires the unpredictable, expiring preview capability issued by the authenticated API; `/agent/local-preview` requires the paired-device Bearer credential and the outstanding channel key. An unknown or closed preview returns 410, while an unauthenticated device connection returns 401. Never inject a privileged edge token on either route.

The following example belongs in an existing HTTPS proxy configuration. Put the `map` in the HTTP context and the two locations in the server context, adjust the upstream to the actual CRB service, and retain the deployment's existing request/connection limits. Ensure a cookie-authentication rule inherited from a parent context does not override these two backend authentication mechanisms. The other routes keep their existing login and frame protections.

```nginx
# HTTP context
map $http_upgrade $crb_preview_connection {
    default upgrade;
    '' close;
}

# HTTPS server context
location ~ "^/local-preview/[0-9a-f]{48}(?:/|$)" {
    proxy_pass http://127.0.0.1:8799;
    proxy_http_version 1.1;
    proxy_set_header Host $http_host;
    proxy_set_header Origin $http_origin;
    proxy_set_header Cookie "";
    proxy_set_header X-Codex-Edge-Token "";
    proxy_set_header Authorization $http_authorization;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $crb_preview_connection;
    proxy_buffering off;
    proxy_request_buffering off;
    proxy_connect_timeout 5s;
    proxy_read_timeout 650s;
    proxy_send_timeout 120s;
    client_max_body_size 16m;
    proxy_hide_header X-Frame-Options;
    # Override inherited add_header rules here without a DENY frame header.
    # Preserve CRB's CSP sandbox and retain these HTTPS response protections.
    add_header Strict-Transport-Security "max-age=31536000" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "no-referrer" always;
    add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;
}

location = /agent/local-preview {
    proxy_pass http://127.0.0.1:8799;
    proxy_http_version 1.1;
    proxy_set_header Host $http_host;
    proxy_set_header Origin $http_origin;
    proxy_set_header Cookie "";
    proxy_set_header X-Codex-Edge-Token "";
    proxy_set_header Authorization $http_authorization;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $crb_preview_connection;
    proxy_buffering off;
    proxy_request_buffering off;
    proxy_connect_timeout 5s;
    proxy_read_timeout 650s;
    proxy_send_timeout 120s;
    client_max_body_size 16m;
}
```

After `nginx -t` and a graceful reload, test by clicking a loopback link through the actual authenticated public HTTPS entry. Check that the embedded page, its live data, the separate window and the WebSocket lease work, then close it and verify that its URL expires. Also verify that anonymous panel/API access, unknown capabilities and invalid device credentials remain rejected. A phone-sized desktop browser check does not replace testing the user's actual phone/browser when its behavior differs.

## Isolation and traffic

All preview documents receive an opaque sandbox, including direct visits in a separate window. They cannot read the CRB DOM, login cookies or local storage. CRB login cookies, edge headers and device credentials are not forwarded to the target application. Target cookies and fallback storage stay in that preview session's memory and are cleared on close. Revoking the client credential invalidates its preview sessions and closes their streams. HttpOnly target cookies are not exposed to preview JavaScript. Existing application CSP/frame headers are adapted for this sandboxed rendering; the target's original server configuration is not changed. Applications retain their own authentication checks.

There is no port scan, idle background tunnel or screenshot polling. Opening a preview queues a local client-control request, then page data travels over a bounded multiplexed stream rather than entering chat command history. Normal assets can use private browser caching; HTML, JSON and event streams use no-store. View/channel liveness uses small WebSocket control frames. An active page can generate its own traffic at its own update rate. Requests and transformed text resources have a 16 MiB limit.

## Compatibility boundaries

This is a web forwarding adapter, not a full remote browser. A path prefix and opaque origin are used so an additional public hostname or port is unnecessary. Hard-coded single-page router assumptions, Service Workers, persistent browser profiles, arbitrary cross-port dependencies, external authentication redirects and some browser origin checks can need application-specific base-URL configuration. Storage fallback is session-local, not the target computer's regular browser profile. The separate-window option retains the same isolation rules. Unsupported or unavailable targets show an error instead of silently switching computers or relaxing TLS verification.

Only explicitly selected loopback targets are accepted. Private LAN addresses, arbitrary DNS names, `0.0.0.0`, file URLs and alternate numeric IP spellings are rejected. Use the actual loopback URL for a server that prints its listen address as `0.0.0.0`. This feature does not expose unrestricted network forwarding or the original model application's debug interfaces by default.
