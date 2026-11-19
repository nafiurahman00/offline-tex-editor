#!/usr/bin/env python3
"""Build the public website from README.md. Standard library only.

    python3 site/build.py                  # writes site/_build/
    python3 site/build.py --out ../offline-tex-editor.github.io

The output is everything the website's repository needs: index.html with the README, a contact section and a
suggestion box, its assets, and that repository's README, SECURITY.md and issue settings (which send people to the
source repository). The suggestion box opens a pre-filled issue in the source repository, using the issue forms in
.github/ISSUE_TEMPLATE/. Settings (site address, repositories, contact email) are in site/config.json.
"""
import argparse
import hashlib
import html
import json
import re
import shutil
from pathlib import Path
from string import Template

HERE = Path(__file__).resolve().parent
REPO = HERE.parent


# ---------- a small Markdown renderer, for what README.md uses ----------
# Headings, paragraphs, **bold**, *italic*, `code`, links, images, fenced code blocks, lists and tables.

def slug(text):
    """GitHub's heading anchors: lower case, punctuation removed, spaces as hyphens."""
    text = re.sub(r'[`*]', '', text).strip().lower()
    return re.sub(r'[^\w\- ]', '', text).replace(' ', '-')


def attr(s):
    return s.replace('"', '&quot;')


def inline(text):
    codes = []

    def keep(m):
        codes.append('<code>' + html.escape(m.group(1), quote=False) + '</code>')
        return f'\x00{len(codes) - 1}\x00'

    text = re.sub(r'`([^`]+)`', keep, text)
    text = html.escape(text, quote=False)
    text = re.sub(r'!\[([^\]]*)\]\(([^)\s]+)\)', lambda m: f'<img src="{attr(m.group(2))}" alt="{attr(m.group(1))}">', text)
    text = re.sub(r'\[([^\]]+)\]\(([^)\s]+)\)', lambda m: f'<a href="{attr(m.group(2))}">{m.group(1)}</a>', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'(?<![\w*])\*(?![\s*])(.+?)(?<![\s*])\*(?![\w*])', r'<em>\1</em>', text)
    return re.sub('\x00(\\d+)\x00', lambda m: codes[int(m.group(1))], text)


ITEM = re.compile(r'(\s*)([-*]|\d+\.)\s+(.*)$')
FENCE = re.compile(r'\s*```\s*([\w+-]*)\s*$')
HEADING = re.compile(r'(#{1,6})\s+(.*?)\s*#*\s*$')
RULE_ROW = re.compile(r'^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$')   # |---|:--:|--:|


def split_row(row):
    """Cells of a table row; a | inside `code` does not split."""
    row = row.strip()
    row = row[1:] if row.startswith('|') else row
    row = row[:-1] if row.endswith('|') and not row.endswith('\\|') else row
    cells, cur, code = [], '', False
    for c in row:
        if c == '`':
            code = not code
        if c == '|' and not code:
            cells.append(cur.strip())
            cur = ''
        else:
            cur += c
    return cells + [cur.strip()]


def starts_block(lines, i):
    line = lines[i]
    return bool(FENCE.match(line) or HEADING.match(line) or ITEM.match(line) or
                (line.lstrip().startswith('|') and i + 1 < len(lines) and RULE_ROW.match(lines[i + 1])))


def render(md):
    """Markdown -> list of (kind, html, extra) blocks; kind is 'h1'…'h6', 'p', 'img', 'pre', 'ul', 'ol' or 'table'."""
    lines = md.replace('\r\n', '\n').split('\n')
    blocks, i = [], 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        m = FENCE.match(line)
        if m:
            code, i = [], i + 1
            while i < len(lines) and not FENCE.match(lines[i]):
                code.append(lines[i])
                i += 1
            i += 1
            lang = f' class="language-{m.group(1)}"' if m.group(1) else ''
            blocks.append(('pre', f'<pre><code{lang}>{html.escape(chr(10).join(code), quote=False)}</code></pre>', None))
            continue
        m = HEADING.match(line)
        if m:
            n, text = len(m.group(1)), m.group(2)
            blocks.append((f'h{n}', f'<h{n} id="{slug(text)}">{inline(text)}</h{n}>', text))
            i += 1
            continue
        if line.lstrip().startswith('|') and i + 1 < len(lines) and RULE_ROW.match(lines[i + 1]):
            head = split_row(line)
            align = ['right' if c.strip().endswith(':') and not c.strip().startswith(':') else
                     'center' if c.strip().startswith(':') and c.strip().endswith(':') else ''
                     for c in split_row(lines[i + 1])]
            i += 2
            rows = []
            while i < len(lines) and lines[i].lstrip().startswith('|'):
                rows.append(split_row(lines[i]))
                i += 1
            def cell(tag, j, c):
                cls = f' class="{align[j]}"' if j < len(align) and align[j] else ''
                return f'<{tag}{cls}>{inline(c)}</{tag}>'
            thead = '<tr>' + ''.join(cell('th', j, c) for j, c in enumerate(head)) + '</tr>'
            tbody = ''.join('<tr>' + ''.join(cell('td', j, c) for j, c in enumerate(r)) + '</tr>' for r in rows)
            blocks.append(('table', f'<div class="table"><table><thead>{thead}</thead><tbody>{tbody}</tbody></table></div>', None))
            continue
        m = ITEM.match(line)
        if m:
            ordered = m.group(2)[0].isdigit()
            first = int(m.group(2)[:-1]) if ordered else 1
            items = []
            while i < len(lines):
                m = ITEM.match(lines[i])
                if not m or m.group(2)[0].isdigit() != ordered:
                    break
                text, i = [m.group(3)], i + 1
                while i < len(lines) and lines[i].strip() and lines[i][:1] in (' ', '\t') and not ITEM.match(lines[i]):
                    text.append(lines[i].strip())
                    i += 1
                items.append('<li>' + inline(' '.join(text)) + '</li>')
            tag = 'ol' if ordered else 'ul'
            start = f' start="{first}"' if ordered and first != 1 else ''
            blocks.append((tag, f'<{tag}{start}>' + ''.join(items) + f'</{tag}>', None))
            continue
        para = [line.strip()]
        i += 1
        while i < len(lines) and lines[i].strip() and not starts_block(lines, i):
            para.append(lines[i].strip())
            i += 1
        text = ' '.join(para)
        m = re.fullmatch(r'!\[([^\]]*)\]\(([^)\s]+)\)', text)
        if m:
            blocks.append(('img', inline(text), (m.group(1), m.group(2))))
        elif re.fullmatch(r'\*\*[^*]+\*\*', text):   # a paragraph that is all bold works as a subheading
            blocks.append(('h3', f'<h3 id="{slug(text)}">{html.escape(text[2:-2], quote=False)}</h3>', text[2:-2]))
        else:
            blocks.append(('p', f'<p>{inline(text)}</p>', None))
    return blocks


# ---------- the site ----------

def version():
    text = (REPO / 'offline_tex_editor' / '__init__.py').read_text(encoding='utf-8')
    return re.search(r"__version__ = '([^']+)'", text).group(1)


def page(config):
    """index.html: the README (title, lead, screenshot, sections), then contact and the suggestion box."""
    blocks = render((REPO / 'README.md').read_text(encoding='utf-8'))
    title = next(b for b in blocks if b[0] == 'h1')[2]
    rest = blocks[[b[0] for b in blocks].index('h1') + 1:]
    lead, figure, dropping = [], '', False
    while rest and rest[0][0] in ('p', 'img'):
        kind, body, extra = rest.pop(0)
        if kind == 'img' and not figure:
            alt, src = extra
            if src.startswith(config['site_url']):    # served from this site: link it relatively
                src = src[len(config['site_url']):]
            figure = (f'<figure class="shot"><img src="{attr(src)}" alt="{attr(alt)}" width="1558" height="784">'
                      f'<figcaption>Figure 1: {html.escape(alt, quote=False)}</figcaption></figure>')
        else:
            lead.append(body)
    # Sections the site replaces with its own (the README's pointer to the suggestion box, say).
    skip, kept = set(config.get('skip_sections', [])), []
    for b in rest:
        if b[0] == 'h2':
            dropping = b[2] in skip
        if not dropping:
            kept.append(b)
    toc = [(b[2], slug(b[2])) for b in kept if b[0] == 'h2'] + [('Contact', 'contact'), ('Suggestion box', 'suggest')]
    # Links from the README back to this site become links within the page.
    content = '\n'.join(b[1] for b in kept).replace(f'href="{config["site_url"]}#', 'href="#')
    email = config.get('email', '').strip()
    # Asset URLs carry a hash of their content, so browsers fetch new versions instead of cached ones.
    stamp = lambda name: hashlib.sha256((HERE / 'assets' / name).read_bytes()).hexdigest()[:10]
    values = dict(config, title=html.escape(title), version=version(), lead='\n'.join(lead), figure=figure,
                  css_v=stamp('style.css'), js_v=stamp('site.js'),
                  content=content,
                  toc='\n'.join(f'<li><a href="#{s}">{html.escape(t)}</a></li>' for t, s in toc),
                  email_contact=(f'<p>You can also write to <a href="mailto:{attr(email)}">{html.escape(email)}</a>.</p>'
                                 if email else ''))
    return Template((HERE / 'template.html').read_text(encoding='utf-8')).substitute(values)


def build(out, config):
    out.mkdir(parents=True, exist_ok=True)
    (out / 'index.html').write_text(page(config), encoding='utf-8')
    if (out / 'assets').exists():
        shutil.rmtree(out / 'assets')
    shutil.copytree(HERE / 'assets', out / 'assets')
    (out / '.nojekyll').write_text('', encoding='utf-8')   # serve the files as they are
    # The website repository's own files (site/public/), with the addresses from config.json filled in.
    values = dict(config, version=version())
    for src in (HERE / 'public').rglob('*'):
        if src.is_file():
            dest = out / src.relative_to(HERE / 'public')
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(Template(src.read_text(encoding='utf-8')).substitute(values), encoding='utf-8')
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', type=Path, default=HERE / '_build', help='output folder (default: site/_build)')
    a = ap.parse_args()
    config = json.loads((HERE / 'config.json').read_text(encoding='utf-8'))
    print(f'Built {build(a.out, config)}')


if __name__ == '__main__':
    main()
