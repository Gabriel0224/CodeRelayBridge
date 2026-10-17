"""Render a host-specific introduction; runtime sources stay on shared main."""
import re

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
    english = '[English]' not in text[:180]
    label = 'Alternative source repository' if english else '备用源码仓库'
    return text.rstrip() + f'\n\n[{label}](https://github.com/Gabriel0224/{repository})\n'
