"""Git operations for the editor: status, diff, stage/unstage, commit, pull (fast-forward, rebase or merge), conflict
handling, push, sync (pull then push), connecting a remote, starting a repository, log.

Every command runs with an argument list (no shell), with `--` before paths, and with GIT_TERMINAL_PROMPT=0 so a
missing credential fails instead of waiting for input that never comes; GIT_EDITOR=true does the same for commit
messages. Paths are relative to the repository top level, use forward slashes, and must stay inside it.
"""
import os
import re
import subprocess
from pathlib import Path

ENV = dict(os.environ, GIT_TERMINAL_PROMPT='0', GIT_OPTIONAL_LOCKS='0', LC_ALL='C', GIT_EDITOR='true',
           GIT_MERGE_AUTOEDIT='no')
MAX_DIFF = 400_000


class GitError(Exception):
    pass


def run(cwd, *args, timeout=60, check=True):
    p = subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True, encoding='utf-8', errors='replace',
                       timeout=timeout, env=ENV)
    if check and p.returncode != 0:
        raise GitError(explain((p.stderr or p.stdout).strip() or f'git {args[0]} failed ({p.returncode})'))
    return p


def explain(msg):
    """Git's message, plus what to do about the usual reasons a push, pull or fetch is refused."""
    if any(t in msg for t in ('could not read Username', 'could not read Password', 'terminal prompts disabled',
                              'Authentication failed')):
        return (msg + '\n\nGit has no credentials for this remote. For GitHub over https, sign in once with the GitHub '
                'CLI ("gh auth login", then "gh auth setup-git") or set up another credential helper; for ssh, add your '
                'key to your GitHub account.')
    if 'Permission denied (publickey)' in msg:
        return msg + '\n\nGitHub did not accept your ssh key: add it to your GitHub account, or use the https address.'
    if 'Repository not found' in msg or 'does not appear to be a git repository' in msg:
        return msg + '\n\nCheck the repository address, and that your GitHub account can access it.'
    return msg


def toplevel(root):
    p = run(root, 'rev-parse', '--show-toplevel', check=False)
    return Path(p.stdout.strip()).resolve() if p.returncode == 0 else None


def repo_top(root):
    top = toplevel(root)
    if top is None:
        raise GitError('not a git repository')
    return top


def inside(top, rel):
    p = (top / rel).resolve()
    if p != top and top not in p.parents:
        raise PermissionError(f'{rel} is outside the repository')
    return p.relative_to(top).as_posix()


def git_path(top, name):
    return top / run(top, 'rev-parse', '--git-path', name).stdout.strip()


def repo_state(top):
    """'rebasing', 'merging' or None."""
    if git_path(top, 'rebase-merge').exists() or git_path(top, 'rebase-apply').exists():
        return 'rebasing'
    if git_path(top, 'MERGE_HEAD').exists():
        return 'merging'
    return None


def parse_status(raw):
    """Parse `git status --porcelain=v1 -z -b` into a branch line and file entries."""
    parts = raw.split('\0')
    branch, files, i = '', [], 0
    while i < len(parts):
        e = parts[i]
        i += 1
        if not e:
            continue
        if e.startswith('## '):
            branch = e[3:]
            continue
        x, y, path = e[0], e[1], e[3:]
        orig = None
        if x in 'RC':                      # rename/copy: the next field is the original path
            orig = parts[i]
            i += 1
        conflict = 'U' in (x, y) or (x, y) in (('A', 'A'), ('D', 'D'))
        files.append({'path': path, 'orig': orig, 'x': x, 'y': y,
                      'staged': x not in ' ?!' and not conflict, 'unstaged': (y != ' ' or x == '?') and not conflict,
                      'untracked': x == '?', 'conflict': conflict})
    return branch, files


def parse_branch(line):
    """'main...origin/main [ahead 1, behind 2]' -> dict."""
    out = {'branch': line, 'upstream': None, 'ahead': 0, 'behind': 0}
    head, _, track = line.partition(' [')
    if '...' in head:
        out['branch'], out['upstream'] = head.split('...', 1)
    else:
        out['branch'] = head
    for part in track.rstrip(']').split(', '):
        if part.startswith('ahead '):
            out['ahead'] = int(part.split()[1])
        elif part.startswith('behind '):
            out['behind'] = int(part.split()[1])
    if out['branch'].startswith('No commits yet on '):
        out['branch'] = out['branch'][len('No commits yet on '):]
    return out


def status(root):
    top = toplevel(root)
    if top is None:
        return {'repo': False}
    raw = run(top, 'status', '--porcelain=v1', '-z', '-b', '--untracked-files=all').stdout
    branch, files = parse_status(raw)
    remotes = run(top, 'remote', check=False).stdout.split()
    prefix = root.relative_to(top).as_posix() if root != top else ''
    return {'repo': True, 'top': str(top), 'prefix': prefix, 'remotes': remotes, 'state': repo_state(top),
            **parse_branch(branch), 'files': files}


def diff(root, rel, staged=False):
    top = repo_top(root)
    rel = inside(top, rel)
    tracked = run(top, 'ls-files', '--error-unmatch', '--', rel, check=False).returncode == 0
    if staged:
        p = run(top, 'diff', '--cached', '--no-color', '--', rel, check=False)
    elif tracked:
        p = run(top, 'diff', '--no-color', '--', rel, check=False)
    else:                                   # untracked: show the whole file as added
        p = run(top, 'diff', '--no-color', '--no-index', '--', os.devnull, rel, check=False)
    text = p.stdout
    if len(text) > MAX_DIFF:
        text = text[:MAX_DIFF] + '\n… diff truncated …\n'
    return {'path': rel, 'staged': staged, 'diff': text or '(no textual changes)'}


def stage(root, paths, everything=False):
    """Stage the given paths, or with everything every change in the repository (whatever the editor last
    displayed, so a file saved a moment ago is not missed)."""
    top = repo_top(root)
    if everything:
        run(top, 'add', '-A')
        return status(root)
    rels = [inside(top, p) for p in paths]
    if rels:
        run(top, 'add', '-A', '--', *rels)
    return status(root)


def unstage(root, paths):
    top = repo_top(root)
    rels = [inside(top, p) for p in paths]
    has_head = run(top, 'rev-parse', '--verify', 'HEAD', check=False).returncode == 0
    if rels:
        if has_head:
            run(top, 'reset', '-q', 'HEAD', '--', *rels)
        else:
            run(top, 'rm', '--cached', '-q', '-r', '--', *rels)
    return status(root)


def commit(root, message, amend=False):
    top = repo_top(root)
    st = status(root)
    if any(f['conflict'] for f in st['files']):
        raise GitError('resolve the conflicts first (edit the files, then mark them resolved)')
    if not message.strip() and not amend and st['state'] != 'merging':
        raise GitError('empty commit message')
    if not amend and st['state'] != 'merging' and not [f for f in st['files'] if f['staged']]:
        raise GitError('nothing staged to commit')
    args = ['commit', '-q', '-m', message] if message.strip() else ['commit', '-q', '--no-edit']
    if amend:
        args.insert(1, '--amend')
    run(top, *args)
    return {'commit': run(top, 'log', '-1', '--format=%h %s').stdout.strip(), **status(root)}


def push(root):
    top = repo_top(root)
    st = status(root)
    if not st['remotes']:
        raise GitError('no remote configured (git remote add origin <url>)')
    if st['upstream']:
        p = run(top, 'push', timeout=180)
    else:
        p = run(top, 'push', '-u', st['remotes'][0], 'HEAD', timeout=180)
    return {'output': (p.stderr + p.stdout).strip(), **status(root)}


PULL = {'ff': ['pull', '--ff-only'],
        'rebase': ['pull', '--rebase', '--autostash'],
        'merge': ['pull', '--no-rebase', '--no-edit', '--autostash']}


def pull(root, mode='ff'):
    """Pull. 'ff' refuses when the histories have diverged (diverged: True in the reply); 'rebase' and 'merge' then
    integrate them, and stop with conflict: True when files conflict."""
    if mode not in PULL:
        raise GitError(f'unknown pull mode {mode!r}')
    top = repo_top(root)
    p = run(top, *PULL[mode], timeout=180, check=False)
    out = (p.stdout + p.stderr).strip()
    st = status(root)
    if p.returncode != 0:
        if st['state'] or any(f['conflict'] for f in st['files']):
            return {'output': out, 'conflict': True, **st}
        if mode == 'ff' and ('fast-forward' in out or 'diverg' in out):
            return {'output': out, 'diverged': True, **st}
        raise GitError(out or f'git pull failed ({p.returncode})')
    return {'output': out, **st}


def sync(root, mode='ff'):
    """Bring the branch and its remote in step: fetch, pull when behind, push when ahead. A branch that was never
    pushed is linked to the remote branch of the same name, or pushed to create it. When both sides have new
    commits, 'ff' stops with diverged: True so the user can choose; 'rebase' or 'merge' then integrates them."""
    if mode not in PULL:
        raise GitError(f'unknown sync mode {mode!r}')
    top = repo_top(root)
    st = status(root)
    if st['state'] or any(f['conflict'] for f in st['files']):
        raise GitError('finish or abort the merge or rebase first')
    if not st['remotes']:
        raise GitError('this repository has no remote yet: connect it to a GitHub repository first')
    if run(top, 'rev-parse', '--verify', '--quiet', 'HEAD', check=False).returncode != 0:
        raise GitError('make a first commit, then sync')
    if st['branch'].startswith('HEAD'):
        raise GitError('not on a branch (detached HEAD): check out a branch first')
    remote = 'origin' if 'origin' in st['remotes'] else st['remotes'][0]
    run(top, 'fetch', '--quiet', remote, timeout=120)
    st = status(root)
    if not st['upstream']:
        if run(top, 'rev-parse', '--verify', '--quiet', f'refs/remotes/{remote}/{st["branch"]}',
               check=False).returncode == 0:
            run(top, 'branch', f'--set-upstream-to={remote}/{st["branch"]}')
            st = status(root)
        else:
            p = run(top, 'push', '-u', remote, 'HEAD', timeout=180)
            return {'output': (p.stderr + p.stdout).strip(), 'pushed': True, **status(root)}
    out = []
    if st['behind']:
        if st['ahead'] and mode == 'ff':
            return {'output': 'Your branch and the remote both have new commits.', 'diverged': True, **st}
        r = pull(root, mode if st['ahead'] else 'ff')
        if r.get('conflict') or r.get('diverged'):
            return r
        out.append(r.get('output', ''))
        st = status(root)
    if st['ahead']:
        p = run(top, 'push', timeout=180)
        out.append((p.stderr + p.stdout).strip())
    return {'output': '\n'.join(o for o in out if o).strip() or 'Already in sync.', **status(root)}


# https://, ssh:// and git:// addresses, or scp-like user@host:path. Nothing else: git's ext:: transport, for one,
# runs a command.
REMOTE_URL = re.compile(r'^(?:(?:https?|ssh|git)://[^\s]+|[\w.-]+@[\w.-]+:[^\s]+)$')


def set_remote(root, url, name='origin'):
    """Connect the repository to a remote (a GitHub repository's address), or change the address."""
    top = repo_top(root)
    url = url.strip()
    if not REMOTE_URL.match(url):
        raise GitError('use a repository address such as https://github.com/you/project.git or '
                       'git@github.com:you/project.git')
    if name in run(top, 'remote', check=False).stdout.split():
        run(top, 'remote', 'set-url', name, url)
    else:
        run(top, 'remote', 'add', name, url)
    return status(root)


def init(root):
    """Start a git repository in the project folder."""
    if toplevel(root) is not None:
        raise GitError('this project is already in a git repository')
    run(root, 'init', '-q', '-b', 'main')
    return status(root)


def abort(root):
    top = repo_top(root)
    state = repo_state(top)
    if state is None:
        raise GitError('no merge or rebase in progress')
    run(top, 'rebase' if state == 'rebasing' else 'merge', '--abort')
    return status(root)


def resume(root):
    """Continue a rebase, or commit a merge, once every conflict is resolved and staged."""
    top = repo_top(root)
    st = status(root)
    if any(f['conflict'] for f in st['files']):
        raise GitError('some files still have conflicts: edit them, then mark them resolved')
    if st['state'] == 'rebasing':
        p = run(top, 'rebase', '--continue', check=False)
        st = status(root)
        if p.returncode != 0 and not st['state']:
            raise GitError((p.stderr or p.stdout).strip())
        return {'output': (p.stdout + p.stderr).strip(), 'conflict': bool(st['state']), **st}
    if st['state'] == 'merging':
        run(top, 'commit', '-q', '--no-edit')
        return status(root)
    raise GitError('no merge or rebase in progress')


def fetch(root):
    top = repo_top(root)
    run(top, 'fetch', '--quiet', timeout=120, check=False)
    return status(root)


def log(root, n=20):
    top = repo_top(root)
    if run(top, 'rev-parse', '--verify', 'HEAD', check=False).returncode != 0:
        return {'commits': []}
    fmt = '%h%x1f%an%x1f%ar%x1f%s'
    out = run(top, 'log', f'-{n}', f'--format={fmt}').stdout
    commits = [dict(zip(('hash', 'author', 'when', 'subject'), l.split('\x1f'))) for l in out.splitlines() if l]
    return {'commits': commits}


def show(root, rev):
    top = repo_top(root)
    if not rev or not all(c.isalnum() for c in rev):
        raise GitError('bad revision')
    text = run(top, 'show', '--no-color', '--stat', '--patch', rev).stdout
    return {'rev': rev, 'diff': text[:MAX_DIFF]}
