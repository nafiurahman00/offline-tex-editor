"""Tests for the website generator (site/build.py) and for links that must agree across README, pyproject and the
site config."""
import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('site_build', REPO / 'site' / 'build.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)
CONFIG = json.loads((REPO / 'site' / 'config.json').read_text(encoding='utf-8'))


class Markdown(unittest.TestCase):
    def html(self, md):
        return ''.join(b[1] for b in build.render(md))

    def test_inline(self):
        out = self.html('Some *em*, **bold**, `a<b|c` and [a link](https://x.org/?a=1&b=2).')
        self.assertEqual(out, '<p>Some <em>em</em>, <strong>bold</strong>, <code>a&lt;b|c</code> and '
                              '<a href="https://x.org/?a=1&amp;b=2">a link</a>.</p>')

    def test_blocks(self):
        md = ('# Title\n\nFirst line\nsecond line.\n\n- one\n  continued\n- two\n\n1. first\n2. second\n\n'
              '```bash\necho "<hi>"\n```\n\n| A | B |\n|---|--:|\n| `x|y` | 2 |\n\n**Group**\n\n'
              '![Alt "q"](img.png)\n')
        out = self.html(md)
        for part in ('<h1 id="title">Title</h1>', '<p>First line second line.</p>',
                     '<ul><li>one continued</li><li>two</li></ul>', '<ol><li>first</li><li>second</li></ol>',
                     '<pre><code class="language-bash">echo "&lt;hi&gt;"</code></pre>',
                     '<th>A</th><th class="right">B</th>', '<td><code>x|y</code></td><td class="right">2</td>',
                     '<h3 id="group">Group</h3>', '<img src="img.png" alt="Alt &quot;q&quot;">'):
            self.assertIn(part, out)

    def test_slugs_match_github(self):
        self.assertEqual(build.slug('Main document and engine'), 'main-document-and-engine')
        self.assertEqual(build.slug('Help, suggestions & bugs'), 'help-suggestions--bugs')


class Site(unittest.TestCase):
    def test_build(self):
        with tempfile.TemporaryDirectory() as d:
            out = build.build(Path(d), CONFIG)
            page = (out / 'index.html').read_text(encoding='utf-8')
            readme = (REPO / 'README.md').read_text(encoding='utf-8')
            for h2 in re.findall(r'^## (.+)$', readme, re.M):
                if h2 not in CONFIG['skip_sections']:
                    self.assertIn(f'href="#{build.slug(h2)}"', page, h2)
            self.assertIn(f'pip install {CONFIG["pypi"]}', page)
            self.assertIn('TeX is a trademark of the American Mathematical Society.', page)
            self.assertIn(f'href="https://pypi.org/project/{CONFIG["pypi"]}/"', page)
            self.assertIn(f'https://pypi.org/project/{CONFIG["pypi"]}/', (out / 'README.md').read_text(encoding='utf-8'))
            self.assertIn(f'action="https://github.com/{CONFIG["public_repo"]}/issues/new"', page)
            self.assertIn('src="assets/editor.jpg"', page)   # the README's absolute image link, made local
            # every generated file has its placeholders filled in (the README itself may contain "$", e.g. $pdf_mode)
            names = set(re.findall(r'\$([a-z_]+)', (REPO / 'site' / 'template.html').read_text(encoding='utf-8')))
            for src in (REPO / 'site' / 'public').rglob('*'):
                if src.is_file():
                    names |= set(re.findall(r'\$([a-z_]+)', src.read_text(encoding='utf-8')))
            leftover = re.compile(r'\$(%s)\b' % '|'.join(sorted(names)))
            for f in out.rglob('*'):
                if f.suffix in ('.html', '.md', '.yml'):
                    self.assertIsNone(leftover.search(f.read_text(encoding='utf-8')), f)
            # the suggestion box fills in fields the issue forms have
            forms = {f.name: f.read_text(encoding='utf-8') for f in (out / '.github' / 'ISSUE_TEMPLATE').glob('*.yml')}
            for template in re.findall(r'name="template" value="([^"]+)"', page):
                self.assertIn(template, forms)
                self.assertIn('id: details', forms[template])
            self.assertIn('id: system', forms['bug_report.yml'])
            self.assertIn('id: version', forms['bug_report.yml'])
            for asset in ('style.css', 'site.js', 'favicon.svg', 'editor.jpg'):
                self.assertTrue((out / 'assets' / asset).is_file(), asset)

    def test_links_agree(self):
        readme = (REPO / 'README.md').read_text(encoding='utf-8')
        pyproject = (REPO / 'pyproject.toml').read_text(encoding='utf-8')
        self.assertIn(f'pip install {CONFIG["pypi"]}', readme)
        self.assertIn(f'name = "{CONFIG["pypi"]}"', pyproject)
        for text in (readme, pyproject):
            self.assertIn(CONFIG['site_url'], text)
            self.assertIn(f'github.com/{CONFIG["public_repo"]}', text)


if __name__ == '__main__':
    unittest.main()
