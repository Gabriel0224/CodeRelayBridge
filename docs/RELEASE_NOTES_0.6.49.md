# CodeRelayBridge 0.6.49

- Questions stay in a single panel above the composer, with a question counter and Previous/Next navigation. Paging keeps selections and custom answers without sending them; the original native answer protocol remains unchanged.
- Pending answers no longer block updates to the execution timeline. Lost pointer releases have a bounded interaction guard rather than indefinitely delaying updates.
- Native answers dismiss resolved questions, while remaining question drafts and editor focus survive unrelated progress updates.

These shared changes apply to both editions. No additional network polling or automatic answer submission is introduced.
