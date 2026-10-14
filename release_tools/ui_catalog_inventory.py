"""Read-only inventory of UI source copy for translation review (no user data)."""
import ast
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from crb.i18n.ui_i18n import tr
CHINESE = re.compile('[\\u3400-\\u9fff]')

class HtmlCopy(HTMLParser):

    def __init__(self):
        super().__init__()
        self.strings = []

    def handle_starttag(self, tag, attrs):
        self.strings += [value for key, value in attrs if key in ('title', 'aria-label', 'placeholder') and value]

    def handle_data(self, value):
        if value.strip():
            self.strings.append(value.strip())

def strings(name):
    raw = (ROOT / name).read_text(encoding='utf-8')
    if name.endswith('.html'):
        parser = HtmlCopy()
        parser.feed(raw)
        return set(parser.strings)
    if name.endswith('.py'):
        tree = ast.parse(raw)
        parents = {id(child): node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        values = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr):
                index = 0
                value = ''
                for part in node.values:
                    if isinstance(part, ast.Constant):
                        value += str(part.value)
                    else:
                        value += '{' + str(index) + '}'
                        index += 1
                values.add(value)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and (not isinstance(parents.get(id(node)), ast.JoinedStr)):
                values.add(node.value)
        return values
    js = "\nconst fs=require('fs'),acorn=require('acorn'),s=fs.readFileSync(process.argv[1],'utf8');\nconst tree=acorn.parse(s,{ecmaVersion:'latest'}),values=[];\nfunction walk(n,parent){if(!n||typeof n!=='object')return;\nif(n.type==='Literal'&&typeof n.value==='string')values.push(n.value);\nif(n.type==='TemplateLiteral')values.push(n.quasis.map((q,i)=>q.value.cooked+(i<n.expressions.length?'{'+i+'}':'')).join(''));\nfor(const[k,v]of Object.entries(n))if(v&&typeof v==='object')Array.isArray(v)?v.forEach(x=>walk(x,n)):walk(v,n);}\nwalk(tree);process.stdout.write(JSON.stringify(values));\n"
    values = json.loads(subprocess.check_output(['node', '-e', js, str(ROOT / name)], cwd=ROOT))
    result = set()
    for value in values:
        if '<' in value and '>' in value:
            parser = HtmlCopy()
            parser.feed(value)
            result.update(parser.strings)
        else:
            result.add(value)
    return result
if __name__ == '__main__':
    names = sys.argv[1:] or ['web/index.html', 'web/app.js', 'web/vendor/native-ui.js']
    values = sorted({s.strip() for name in names for s in strings(name) if CHINESE.search(s) and CHINESE.search(tr(s, 'en'))})
    print('Untranslated source candidates:', len(values))
    for index, value in enumerate(values):
        print(index, json.dumps(value, ensure_ascii=False))
