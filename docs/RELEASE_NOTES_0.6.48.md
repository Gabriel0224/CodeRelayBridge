# CodeRelayBridge 0.6.48

- macOS: named and branded native app identities in system settings, with an Accessibility permission button and current permission status. Permission is checked locally, with no automatic prompts or additional network polling.
- Model settings: Standard, Fast and Ultrafast options follow the selected Codex model's native capabilities. Faster modes are opt-in and show an increased-usage notice. Cancel leaves the original settings unchanged.
- macOS: reuse the original App-owned tool bridge independently of a CRB pairing's data directory; initialize the private native bridge when no registration exists, without restarting the original App or replaying a chat operation.

These shared changes apply to both editions. Original chats, pairing and model accounts are preserved.
