"""Build-owned feature identity, never inferred from installed optional provider/config."""
import json
from pathlib import Path
import sys
LABELS = {'standard': '普通版'}

def edition():
    if not getattr(sys, 'frozen', False) or not hasattr(sys, '_MEIPASS'):
        return 'standard'
    value = json.loads((Path(sys._MEIPASS) / 'relay-edition.json').read_text(encoding='utf-8'))
    if value.get('edition') != 'standard':
        raise RuntimeError('安装包类型无效，请完整解压普通版安装包')
    return 'standard'

def edition_label():
    return LABELS[edition()]
