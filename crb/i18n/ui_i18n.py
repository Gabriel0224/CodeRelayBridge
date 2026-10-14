"""Shared UI-only translations. Never pass conversation/file/terminal data here.

Chinese source labels are stable identifiers; persisted values and wire protocols
stay language-independent. English terms follow the installed Codex UI where the
feature exists (New chat, Archive chat, Full access, Reasoning effort, Settings).
"""
from functools import lru_cache
import re
from crb.i18n.ui_translations import MESSAGES, TEMPLATES
LANGUAGES = ('zh', 'en')
_language = 'zh'
_messages = dict(MESSAGES)
_templates = list(TEMPLATES)

def default_language(system_locale):
    return 'zh' if re.match('^zh(?:$|[-_])', str(system_locale or ''), re.I) else 'en'

def selected_language(saved, system_locale):
    return saved if saved in LANGUAGES else default_language(system_locale)

def language():
    return _language

def set_language(value):
    global _language
    if value not in LANGUAGES:
        raise ValueError('Unsupported UI language')
    _language = value

def register(messages, templates=()):
    """Optional UI modules register their own catalog, absent from Standard."""
    _messages.update(messages)
    current = dict(_templates)
    current.update(templates)
    _templates[:] = list(current.items())
    _compiled.cache_clear()

@lru_cache(maxsize=1)
def _compiled():
    rows = []
    for source, target in _templates:
        parts = re.split('(\\{\\d+\\})', source)
        groups = []
        pattern = ''
        for part in parts:
            if re.fullmatch('\\{\\d+\\}', part):
                number = int(part[1:-1])
                if number in groups:
                    pattern += f'(?P=v{number})'
                else:
                    groups.append(number)
                    pattern += f'(?P<v{number}>.*?)'
            else:
                pattern += re.escape(part)
        rows.append((re.compile('^' + pattern + '$', re.S), target))
    return rows

def translate(value, lang=None):
    """Translate explicit UI copy only; interpolated values are left untouched."""
    if not isinstance(value, str) or (lang or _language) == 'zh':
        return value
    if value in _messages:
        return _messages[value]
    stripped = value.strip()
    if stripped in _messages:
        return value[:len(value) - len(value.lstrip())] + _messages[stripped] + value[len(value.rstrip()):]
    for pattern, target in _compiled():
        match = pattern.fullmatch(stripped)
        if match:
            result = re.sub('\\{(\\d+)\\}', lambda m: match.group('v' + m[1]), target)
            return value[:len(value) - len(value.lstrip())] + result + value[len(value.rstrip()):]
    parts = re.split('(\\n| · |：|；)', value)
    if len(parts) > 1:
        return ''.join((': ' if p == '：' else '; ' if p == '；' else translate(p, lang) if p not in ('\n', ' · ') else p for p in parts))
    return value
tr = translate
