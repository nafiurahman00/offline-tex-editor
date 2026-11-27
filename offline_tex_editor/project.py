"""Reading a LaTeX project's sources: files, root documents, magic comments, the outline, labels, bibliography keys
and user commands, and project-wide search and replace. These functions take the project directory and trust the
paths they are given; the server checks paths before calling them.
"""
import os
import re
import shutil
import time
from pathlib import Path

EDITABLE = {'.tex', '.bib', '.sty', '.cls', '.bst', '.md', '.txt', '.csv', '.py', '.sh', '.tikz', '.bbx', '.cbx',
            '.lbx', '.dtx', '.ins', '.ltx', '.cfg', '.def', '.clo'}
EDITABLE_NAMES = {'.latexmkrc', 'latexmkrc'}
ASSETS = {'.png', '.jpg', '.jpeg', '.gif', '.pdf', '.eps', '.svg', '.webp', '.tif', '.tiff', '.bmp'}
SKIP_DIRS = {'build', '.git', '.venv', 'venv', '__pycache__', 'node_modules', '.editor-trash'}
ENGINES = ('pdflatex', 'xelatex', 'lualatex')
ENGINE_ALIASES = {'pdftex': 'pdflatex', 'latex': 'pdflatex', 'xetex': 'xelatex', 'luatex': 'lualatex',
                  'xelatexmk': 'xelatex', 'lualatexmk': 'lualatex', 'pdflatexmk': 'pdflatex'}
MAX_SCAN = 4 * 1024 * 1024  # skip larger files when scanning or searching


def is_editable(p):
    return p.suffix.lower() in EDITABLE or p.name in EDITABLE_NAMES


def skipped(rel):
    return any(part in SKIP_DIRS for part in Path(rel).parts)


def _walk(root):
    """(folder, its subfolders, its files) like os.walk, never entering build/, .git/, node_modules/ and the like."""
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS)
        yield Path(d), dirs, sorted(files)


def walk(root):
    """(path, POSIX path relative to root) of every project file."""
    for d, _, files in _walk(root):
        for f in files:
            p = d / f
            yield p, p.relative_to(root).as_posix()


def list_dirs(root):
    """Every folder (also empty ones) as POSIX paths relative to root, skipping build/, .git/ and the like."""
    return [(d / x).relative_to(root).as_posix() for d, dirs, _ in _walk(root) for x in dirs]


def list_files(root):
    """(editable sources, assets such as images), as POSIX paths relative to root."""
    files, assets = [], []
    for p, rel in walk(root):
        if is_editable(p):
            files.append(rel)
        elif p.suffix.lower() in ASSETS:
            assets.append(rel)
    return files, assets


# ---------- reading and writing sources ----------

def read_source(p):
    """(text, encoding, end of line). UTF-8 when the bytes are valid UTF-8, else Latin-1 (which never fails), so a
    file is saved back in the encoding it came in. Line endings are kept as they are."""
    raw = p.read_bytes()
    try:
        text, enc = raw.decode('utf-8'), 'utf-8'
    except UnicodeDecodeError:
        text, enc = raw.decode('latin-1'), 'latin-1'
    return text, enc, '\r\n' if '\r\n' in text else '\n'


def write_source(p, text, encoding='utf-8'):
    p.write_bytes(text.encode(encoding if encoding in ('utf-8', 'latin-1') else 'utf-8'))


def trash(root, p, stamp=None):
    """Move p (a file or folder inside root) to build/.editor-trash/<time>/, under the same path, so that a delete can
    be undone. Returns where it went."""
    base = root / 'build' / '.editor-trash' / (stamp or time.strftime('%Y%m%d-%H%M%S'))
    rel = p.relative_to(root).as_posix()
    dest, n = base / rel, 1
    while dest.exists():
        dest, n = base.with_name(f'{base.name}-{n}') / rel, n + 1
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(p), str(dest))
    return dest


def read_text(p, limit=MAX_SCAN):
    """For scanning only: lenient decoding, universal newlines, at most `limit` bytes."""
    with open(p, 'rb') as f:
        return f.read(limit).decode('utf-8', errors='replace').replace('\r\n', '\n').replace('\r', '\n')


# ---------- LaTeX structure ----------

COMMENT = re.compile(r'(?<!\\)%.*')
MAGIC = re.compile(r'^\s*%\s*!\s*tex\s+(ts-program|program|root|spellcheck|encoding)\s*=\s*(.+?)\s*$', re.I | re.M)
DOCCLASS = re.compile(r'\\documentclass\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}')


def strip_comment(line):
    return COMMENT.sub('', line)


def magic_comments(text):
    """% !TEX key = value comments in the first 50 lines, keys lower-cased."""
    head = '\n'.join(text.splitlines()[:50])
    return {k.lower(): v for k, v in MAGIC.findall(head)}


def doc_class(text):
    """The document class, or None if the file is not a root document."""
    for line in text.splitlines()[:300]:
        m = DOCCLASS.search(strip_comment(line))
        if m:
            return m.group(1).strip()
    return None


def is_root(text):
    return doc_class(text) not in (None, 'subfiles')


def engine_from_magic(text):
    prog = magic_comments(text).get('program') or magic_comments(text).get('ts-program')
    if not prog:
        return None
    prog = prog.strip().lower()
    prog = ENGINE_ALIASES.get(prog, prog)
    return prog if prog in ENGINES else None


def rc_sets_pdf_mode(root):
    """Whether the project's latexmkrc chooses the engine itself ($pdf_mode)."""
    for name in ('.latexmkrc', 'latexmkrc'):
        p = root / name
        if p.is_file() and re.search(r'^[^#\n]*\$pdf_mode\s*=', read_text(p), re.M):
            return True
    return False


NAME_RANK = ['main', 'thesis', 'paper', 'report', 'article', 'manuscript', 'book', 'document', 'root']


def find_roots(root, files):
    """Root documents (with \\documentclass, not subfiles), best candidates first: top-level files, then common
    names, then shallow paths."""
    found = []
    for rel in files:
        if rel.endswith('.tex'):
            try:
                cls = doc_class(read_text(root / rel, 65536))
            except OSError:
                continue
            if cls not in (None, 'subfiles'):
                stem = Path(rel).stem.lower()
                found.append(((rel.count('/'), cls == 'standalone',
                               NAME_RANK.index(stem) if stem in NAME_RANK else len(NAME_RANK), rel), rel))
    return [rel for _, rel in sorted(found)]


def resolve_main(root, main=None):
    """The main file, relative to root (POSIX). Follows "% !TEX root = ..." comments; without `main`, uses main.tex
    or the best root document. Raises ValueError with a message for the user."""
    if main is None:
        if (root / 'main.tex').is_file():
            main = 'main.tex'
        else:
            roots = find_roots(root, list_files(root)[0])
            if not roots:
                raise ValueError(f'no LaTeX document (a .tex file with \\documentclass) in {root}; pass --main')
            main = roots[0]
    p, name = (root / main).resolve(), main
    for _ in range(5):
        if root not in p.parents:
            raise ValueError(f'{name} is outside {root}')
        if not p.is_file():
            raise ValueError(f'no {name} in {root}')
        target = magic_comments(read_text(p, 65536)).get('root')
        if not target:
            break
        # "% !TEX root" is relative to the file that contains it
        p, name = (p.parent / target).resolve(), target
    return p.relative_to(root).as_posix()


def braced(text, start):
    """The contents of the {...} group that opens at text[start - 1]."""
    depth, i = 1, start
    while i < len(text):
        c = text[i]
        if c == '\\':
            i += 2
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return text[start:i]
        i += 1
    return text[start:]


SECTION = re.compile(r'\\(part|chapter|section|subsection|subsubsection|paragraph)\*?\s*(?:\[[^\]]*\]\s*)?\{')
LEVEL = {'part': 0, 'chapter': 1, 'section': 2, 'subsection': 3, 'subsubsection': 4, 'paragraph': 5}
INCLUDE = re.compile(r'\\(input|include|subfile|import|subimport|inputfrom|includefrom|subinputfrom|subincludefrom)'
                     r'\*?\s*\{([^}]*)\}(?:\s*\{([^}]*)\})?')


def resolve_input(root, here, cmd, a, b, main_dir=None):
    """The project file an \\input-like command reads, relative to root, or None. main_dir: the main file's folder,
    which TeX runs in (default: root)."""
    main_dir = main_dir or root
    if cmd in ('import', 'inputfrom', 'includefrom'):
        bases, name = [main_dir / a.strip()], b
    elif cmd in ('subimport', 'subinputfrom', 'subincludefrom'):
        bases, name = [here / a.strip()], b
    else:   # \input and \include are relative to the main file's folder; also try the including file's folder
        bases, name = [main_dir, here], a
    if name is None:
        return None
    name = name.strip().strip('"')
    for base in bases:
        for cand in (base / name, base / (name + '.tex')):
            try:
                p = cand.resolve()
            except OSError:
                continue
            if p.is_file() and (p == root or root in p.parents):
                return p.relative_to(root).as_posix()
    return None


def outline(root, main):
    """Sectioning commands in document order, following \\input, \\include, \\subfile and \\import from the main
    file: ({'level', 'title', 'file', 'line'} list, list of \\include arguments, as written)."""
    entries, includes, seen = [], [], set()
    main_dir = (root / main).parent

    def visit(rel, depth):
        if rel in seen or depth > 20:
            return
        seen.add(rel)
        try:
            text = read_text(root / rel)
        except OSError:
            return
        here = (root / rel).parent
        for n, raw in enumerate(text.splitlines(), 1):
            line = strip_comment(raw)
            if '\\' not in line:
                continue
            if depth == 0 and '\\end{document}' in line:
                break
            events = [(m.start(), 's', m) for m in SECTION.finditer(line)] + \
                     [(m.start(), 'i', m) for m in INCLUDE.finditer(line)]
            for _, kind, m in sorted(events, key=lambda e: e[0]):
                if kind == 's':
                    title = ' '.join(braced(line, m.end()).split())
                    entries.append({'level': LEVEL[m.group(1)], 'title': title, 'file': rel, 'line': n})
                else:
                    if m.group(1) == 'include':
                        includes.append(m.group(2).strip())
                    target = resolve_input(root, here, m.group(1), m.group(2), m.group(3), main_dir)
                    if target and target.endswith('.tex'):
                        visit(target, depth + 1)

    visit(main, 0)
    return entries, list(dict.fromkeys(includes))


LABEL = re.compile(r'\\label\s*\{([^}]+)\}')
NEWCMD = re.compile(r'\\(?:(?:re|provide)?newcommand|DeclareRobustCommand)\*?\s*\{?\s*\\(?P<cmd>[A-Za-z@]+)\s*\}?'
                    r'\s*(?:\[(?P<args>\d)\])?'
                    r'|\\(?:New|Renew|Provide|Declare)DocumentCommand\s*\{?\s*\\(?P<doc>[A-Za-z@]+)\s*\}?\s*\{'
                    r'|\\DeclareMathOperator\*?\s*\{?\s*\\(?P<op>[A-Za-z@]+)'
                    r'|\\def\s*\\(?P<def>[A-Za-z@]+)'
                    r'|\\(?:(?:re)?newenvironment\*?|(?:New|Renew|Provide|Declare)DocumentEnvironment|newtheorem\*?'
                    r'|declaretheorem\s*(?:\[[^\]]*\])?)\s*\{(?P<env>[A-Za-z@*]+)\}')


def mandatory_args(spec):
    """How many mandatory arguments an xparse argument specification such as "s O{default} m m" has."""
    while True:  # drop {...} groups (default values, processors), innermost first, so an "m" in them is not counted
        spec, n = re.subn(r'\{[^{}]*\}', '', spec)
        if not n:
            return spec.count('m')


def labels(root, files):
    """\\label keys with where they are and a short context (the line, or the line before)."""
    out = []
    for rel in files:
        if not rel.endswith('.tex'):
            continue
        try:
            lines = read_text(root / rel).splitlines()
        except OSError:
            continue
        for n, raw in enumerate(lines, 1):
            line = strip_comment(raw)
            for m in LABEL.finditer(line):
                ctx = (line[:m.start()] + line[m.end():]).strip()
                if not ctx and n > 1:
                    ctx = strip_comment(lines[n - 2]).strip()
                out.append({'key': m.group(1).strip(), 'file': rel, 'line': n, 'context': ctx[:100]})
    return out


def commands(root, files):
    """Commands and environments the project defines (\\newcommand, \\DeclareRobustCommand, \\NewDocumentCommand,
    \\DeclareMathOperator, \\def, \\newenvironment, \\NewDocumentEnvironment, \\newtheorem, \\declaretheorem)."""
    out = {}
    for rel in files:
        if not rel.endswith(('.tex', '.sty', '.cls')):
            continue
        try:
            text = read_text(root / rel)
        except OSError:
            continue
        for n, raw in enumerate(text.splitlines(), 1):
            line = strip_comment(raw)
            for m in NEWCMD.finditer(line):
                if m['env']:
                    out.setdefault('env:' + m['env'], {'name': m['env'], 'env': True, 'args': 0, 'file': rel,
                                                       'line': n})
                else:
                    name = m['cmd'] or m['doc'] or m['op'] or m['def']
                    args = mandatory_args(braced(line, m.end())) if m['doc'] else int(m['args'] or 0)
                    out.setdefault(name, {'name': name, 'env': False, 'args': args, 'file': rel, 'line': n})
    return list(out.values())


BIBENTRY = re.compile(r'@\s*(\w+)\s*[{(]\s*([^,\s{}()]+)\s*,')


def bib_field(entry, name):
    m = re.search(r'(?<![\w-])' + name + r'\s*=\s*', entry, re.I)
    if not m:
        return ''
    i = m.end()
    if i < len(entry) and entry[i] == '{':
        value = braced(entry, i + 1)
    elif i < len(entry) and entry[i] == '"':
        j = entry.find('"', i + 1)
        value = entry[i + 1:j if j > 0 else len(entry)]
    else:
        value = re.match(r'[^,}\n]*', entry[i:]).group(0)
    return ' '.join(value.replace('{', '').replace('}', '').split())


def bib_entries(root, files):
    """Entries of the project's .bib files: key, type, title, author, year, file and line."""
    out = []
    for rel in files:
        if not rel.endswith('.bib'):
            continue
        try:
            text = read_text(root / rel)
        except OSError:
            continue
        starts = list(BIBENTRY.finditer(text))
        for i, m in enumerate(starts):
            kind = m.group(1).lower()
            if kind in ('string', 'comment', 'preamble'):
                continue
            body = text[m.end():starts[i + 1].start() if i + 1 < len(starts) else len(text)]
            out.append({'key': m.group(2), 'type': kind, 'title': bib_field(body, 'title')[:160],
                        'author': bib_field(body, 'author')[:120], 'year': bib_field(body, 'year')[:10],
                        'file': rel, 'line': text.count('\n', 0, m.start()) + 1})
    return out


# ---------- search and replace ----------

def compile_query(q, regex=False, case=False, word=False):
    """A compiled pattern for a search; raises re.error for a bad regular expression."""
    pattern = q if regex else re.escape(q)
    if word:
        pattern = r'(?<!\w)(?:' + pattern + r')(?!\w)'
    return re.compile(pattern, 0 if case else re.IGNORECASE)


def search(root, files, rx, limit=2000):
    """Matches line by line: [{'path', 'line', 'col', 'len', 'text'}], and whether the limit cut the list short."""
    out = []
    for rel in files:
        p = root / rel
        try:
            if p.stat().st_size > MAX_SCAN:
                continue
            lines = read_text(p).split('\n')
        except OSError:
            continue
        for n, line in enumerate(lines, 1):
            for m in rx.finditer(line):
                if m.end() == m.start():
                    continue
                out.append({'path': rel, 'line': n, 'col': m.start(), 'len': m.end() - m.start(),
                            'text': line[:400]})
                if len(out) >= limit:
                    return out, True
    return out, False


REPL_TOKEN = re.compile(r'\$(\$|&|\d{1,2}|<([^>]*)>)')


def expand_replacement(template, m):
    """The replacement for one regular-expression match, with the same rules as the editor's find bar
    (static/findtext.js): $1…$99 insert a group, $& the whole match, $<name> a named group, $$ a dollar sign.
    Everything else, backslashes included, is literal, so "\\textbf{$1}" works as written."""
    groups = m.re.groups

    def token(t):
        tok, name = t.group(1), t.group(2)
        if tok == '$':
            return '$'
        if tok == '&':
            return m.group(0)
        if name is not None:
            return (m.groupdict().get(name) or '') if name in m.groupdict() else t.group(0)
        if 1 <= int(tok) <= groups:
            return m.group(int(tok)) or ''
        if len(tok) == 2 and 1 <= int(tok[0]) <= groups:   # "$10" with fewer than 10 groups: group 1, then "0"
            return (m.group(int(tok[0])) or '') + tok[1]
        return t.group(0)

    return REPL_TOKEN.sub(token, template)


def replace(root, files, rx, replacement, regex=False):
    """Replace every match, line by line, keeping each file's encoding and line endings. Returns {path: count}.
    With regex, the replacement can refer to groups as $1 (see expand_replacement); otherwise it is literal."""
    repl = (lambda m: expand_replacement(replacement, m)) if regex else (lambda _m: replacement)
    changed = []  # every file is rewritten in memory first, so a failure part-way changes nothing
    for rel in files:
        p = root / rel
        try:
            if p.stat().st_size > MAX_SCAN:
                continue
            text, enc, _ = read_source(p)
        except OSError:
            continue
        total, out = 0, []
        for line in text.splitlines(keepends=True):
            body = line.rstrip('\r\n')
            new, n = rx.subn(repl, body)
            total += n
            out.append(new + line[len(body):])
        if total:  # encoding can fail too (a Latin-1 file cannot hold every character), so do it before writing
            changed.append((rel, p, ''.join(out).encode(enc), total))
    for _, p, data, _ in changed:
        p.write_bytes(data)
    return {rel: n for rel, _, _, n in changed}
