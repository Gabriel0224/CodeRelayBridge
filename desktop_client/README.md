# Native Relay applications

The client owns its Codex and DeepSeek bridge workers. Exiting the client disconnects those workers; closing an original model App must not close the client. Minimize-to-tray is an explicit preference. Restart and close actions preserve existing connection settings.

The server owns the browser panel. New installations are configured through the UI: choose a listening address and port, enter a trusted browser URL, and create a login account. Clients enter that URL and the account selected by the server administrator to pair. Pairing stores a device credential, not the login password.

Standard and Cockpit editions provide the same core client and server features. The integrated edition additionally provides account and quota management; Standard does not require Cockpit.

Python, Qt and runtime libraries are bundled into complete native packages; original AI desktop apps must be installed separately. Keep the complete application directory when installing.

[Installation and setup](../docs/USER_GUIDE.en.md) · [Development and builds](../DEVELOPMENT.md)
