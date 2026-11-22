"""WebDAV sync: keep the project in step with a folder on a WebDAV server (Nextcloud, ownCloud, a NAS, ...) that a
team shares. Everyone edits their own copy and syncs; nothing is live.

build/.editor-webdav/state.json records each synced file as it was at the last sync: its hash here and its version
(ETag) on the server, plus its size and mtime so that unchanged files are not read again. build/.editor-webdav/base/
keeps a copy of each text file as it was then. A sync compares both sides with that record:

- changed only here: uploaded, with If-Match, so a version a teammate uploaded in the meantime is never overwritten;
- changed only on the server: downloaded;
- deleted on one side: deleted on the other (here, moved to the editor's trash). A file deleted on one side and
  changed on the other is kept, with the changes;
- changed on both sides: a text file is merged with `git merge-file` against its copy from the last sync. A clean
  merge is saved and uploaded; overlapping edits get conflict markers here, and the file is uploaded once they are
  gone. Without git, without a copy from the last sync, or for an image, the server's version is saved next to the
  file as "name (server copy).ext", and the file is not uploaded until that copy is deleted. Copies are not synced.

Folders, empty ones too, are recorded the same way. A folder new on one side is made on the other; a folder deleted on
one side is deleted on the other once it is empty, never with anything in it (a teammate's files that are not synced,
say): then it stays where it is, and later syncs leave it alone. Hidden folders (.vscode) are made only for files.

On a file system that ignores case (Windows, and macOS as usually set up), Fig.png and fig.png are one file. There a
rename that changed only the case is followed: one made on the server is made here, one made here is made on the
server with MOVE (the server's wins when both renamed). Names the server has in two spellings cannot both be kept
here: they are left out of the sync, and the user is asked to rename one. Elsewhere they are synced, with a warning.

Only editable sources and images/PDFs are synced, never build/ or .git/, and paths from the server must stay inside the
project. A new or changed .latexmkrc from the server (Perl code that runs when compiling) is held back until the user
accepts it. Passwords stay in memory or come from ~/.netrc: nothing here writes them to disk. They are sent with Basic
authentication, or Digest when the server asks for it. Redirects are not followed, so the password goes only to the
address the user entered.
"""
import base64
import contextlib
import difflib
import hashlib
import http.client
import json
import netrc
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlsplit

from . import project

TIMEOUT = 60
MAX_FILE = 256 * 2**20
MAX_FOLDERS = 5000
NETRC = None  # a netrc file to read instead of ~/.netrc (for the tests)
LOOPBACK = {'localhost', '127.0.0.1', '::1'}
COPY = re.compile(r' \(server copy(?: \d+)?\)(\.[^./]*)?$')
MARKER = re.compile(rb'^(?:<<<<<<<|>>>>>>>)(?: |\r?$)', re.M)
DAV = '{DAV:}'
PROPFIND = (b'<?xml version="1.0" encoding="utf-8"?>\n<d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/>'
            b'<d:getetag/><d:getcontentlength/><d:getlastmodified/></d:prop></d:propfind>')
PLAN_KEYS = ('upload', 'download', 'delete_here', 'delete_there', 'conflicts', 'compare', 'held', 'rename_here',
             'rename_there', 'clashes')
RESULT_KEYS = ('uploaded', 'downloaded', 'deleted_here', 'deleted_there', 'merged', 'conflicts', 'copies', 'held',
               'blocked', 'retry', 'kept', 'skipped', 'renamed_here', 'renamed_there', 'clashes')
DIGEST_HASHES = {'MD5': 'md5', 'SHA-256': 'sha256', 'SHA-512-256': 'sha512_256'}

passwords = {}   # (url, user) -> password, for as long as the editor runs
challenges = {}  # (url, user) -> the server's last Digest challenge, so later requests answer it straight away
_case = {}       # project folder -> whether its file system ignores case


class DavError(Exception):
    """A problem the user can fix, with a message that says how."""


class NeedPassword(DavError):
    """No password for the connection (the editor was restarted), or the server refused it."""


class Changed(Exception):
    """The file changed on the server, or here, while the sync was running."""


# ---------- addresses and paths ----------

def normalize_url(url):
    """The folder's address, checked, with the path quoted and ending in a slash."""
    url = (url or '').strip()
    u = urlsplit(url)
    if u.scheme not in ('http', 'https') or not u.hostname or any(c.isspace() for c in u.netloc):
        raise DavError("enter the folder's full address, starting with https://")
    if u.scheme == 'http' and u.hostname not in LOOPBACK:
        raise DavError('use an https:// address: over http, the password would be sent unencrypted')
    if u.username is not None or u.password is not None:
        raise DavError('enter the user name in its own field, not in the address')
    if u.query or u.fragment:
        raise DavError('the address of a WebDAV folder has no ? or # part')
    path = quote(unquote(u.path or '/'), safe="/:@!$&'()*+,;=~")
    return f'{u.scheme}://{u.netloc}{path.rstrip("/")}/'


def valid_rel(rel):
    """A relative POSIX path that names the same file on every system: no '..', no drive letters or backslashes."""
    parts = rel.split('/')
    return bool(rel) and all(p and p not in ('.', '..') and p == p.rstrip(' .')
                             and not any(c in p for c in '\\:') and all(ord(c) >= 32 for c in p) for p in parts)


def syncable(rel):
    p = PurePosixPath(rel)
    return (not project.skipped(rel) and not COPY.search(p.name)
            and (project.is_editable(p) or p.suffix.lower() in project.ASSETS))


def syncable_dir(rel):
    """A folder that is synced also when it is empty: not build/ or .git/, and not hidden (.vscode)."""
    return valid_rel(rel) and not project.skipped(rel) and not any(p.startswith('.') for p in rel.split('/'))


def is_text(rel):
    return project.is_editable(PurePosixPath(rel))


def is_rc(rel):
    return PurePosixPath(rel).name in project.EDITABLE_NAMES


def inside(root, rel):
    """The local path for rel: inside root, also after following symbolic links, and not in build/ or .git/."""
    p = (root / rel).resolve()
    if root not in p.parents or project.skipped(p.relative_to(root)):
        raise PermissionError(f'{rel} is outside the project')
    return p


def parent(rel):
    return rel.rpartition('/')[0]


def under(rel, prefix):
    return rel == prefix or rel.startswith(prefix + '/')


def copy_name(rel, n=1):
    """Where the server's version of rel goes when it cannot be merged: "name (server copy).ext"."""
    p = PurePosixPath(rel)
    tag = ' (server copy)' if n == 1 else f' (server copy {n})'
    return str(p.with_name(p.stem + tag + (p.suffix or '.txt')))


def find_copy(root, rel):
    return next((copy_name(rel, n) for n in range(1, 21) if (root / copy_name(rel, n)).exists()), None)


def free_copy(root, rel):
    for n in range(1, 21):
        if not (root / copy_name(rel, n)).exists():
            return copy_name(rel, n)
    raise DavError(f'{rel}: too many server copies; delete some first')


def blocked(root, rel, data):
    """Why rel must not be uploaded yet ({why, copy}), or None."""
    if is_text(rel) and MARKER.search(data):
        return {'why': 'conflict markers'}
    copy = find_copy(root, rel)
    return {'why': 'server copy', 'copy': copy} if copy else None


def sha(data):
    return hashlib.sha256(data).hexdigest()


# ---------- names that differ only in case ----------

def case_insensitive(root):
    """Whether the project's file system ignores case (Windows, and macOS as usually set up): found by trying it."""
    if root not in _case:
        d = state_dir(root)
        d.mkdir(parents=True, exist_ok=True)
        probe = d / 'case-probe'
        probe.write_bytes(b'')
        try:
            _case[root] = (d / 'CASE-PROBE').exists()
        finally:
            probe.unlink()
    return _case[root]


def canon(rel):
    """A name as it is compared for renames: macOS takes accents written as one character or as two for one name."""
    return unicodedata.normalize('NFC', rel) if sys.platform == 'darwin' else rel


def name_key(insensitive):
    """What two names have in common when the file system takes them for one file."""
    return (lambda rel: canon(rel).casefold()) if insensitive else (lambda rel: rel)


def first_difference(a, b):
    """The leading parts of paths a and b, up to the first folder or file name that differs: what to rename."""
    pa, pb = a.split('/'), b.split('/')
    for i in range(min(len(pa), len(pb))):
        if canon(pa[i]) != canon(pb[i]):
            return '/'.join(pa[:i + 1]), '/'.join(pb[:i + 1])
    return a, b


def rekey(d, old, new):
    """Rename old, and everything under it, in the keys of dict d (or in set d)."""
    for k in [k for k in d if under(k, old)]:
        nk = new + k[len(old):]
        if isinstance(d, dict):
            d[nk] = d.pop(k)
        else:
            d.discard(k)
            d.add(nk)


def rename_local(root, old, new):
    """Rename old to new, which differ only in case, through a temporary name: some systems ignore a rename that only
    changes the case."""
    src = inside(root, old)
    tmp = src.with_name(f'.{src.name}.editor-rename')
    os.rename(src, tmp)
    os.rename(tmp, src.with_name(new.rsplit('/', 1)[-1]))


def unify(root, client, local, remote, folders, files, rep, dry=False):
    """Give each file one spelling here, on the server and in the record, where the file system ignores case: follow
    renames that changed only the case, and leave out names the server has in two spellings. With dry, only work out
    what would be done (for a check). Returns (local, skipped): the files here, looked at again after renames, and a
    test for the paths left out of this sync."""
    insensitive = case_insensitive(root)
    key = name_key(True)  # names apart only in case are found on every system: teammates may be on Windows or macOS
    groups = {}
    for r in list(remote) + [d for d in folders if d]:
        groups.setdefault(key(r), set()).add(r)
    out = {k for k, names in groups.items() if len(names) > 1}
    rep['clashes'] = [{'names': sorted(groups[k]), 'skipped': insensitive} for k in sorted(out)]
    if not insensitive:
        return local, lambda rel: False

    def skipped(rel):
        parts = rel.split('/')
        return any(key('/'.join(parts[:i])) in out for i in range(1, len(parts) + 1))

    def leave_out(rel, error):
        rep['skipped'].append({'path': rel, 'error': error})
        out.add(key(rel))

    dirs_there = {key(d): d for d in folders if d and not skipped(d)}

    def respell(rel):  # in the server's spelling of the folders it is in
        parts = rel.split('/')
        for i in range(len(parts) - 1):
            there = dirs_there.get(key('/'.join(parts[:i + 1])))
            if there:
                parts[i] = there.rsplit('/', 1)[-1]
        return '/'.join(parts)

    def target(here, there, before):
        if there and before and canon(there) != canon(before):
            return there  # renamed on the server (it wins when both renamed)
        if here and before and canon(here) != canon(before):
            return here   # renamed here
        return there or respell(here or before)

    def keyed(d):
        return {key(r): r for r in d if not skipped(r)}

    # Renames here first; then look at the files here again, as a folder renamed holds other files.
    for _ in range(3):
        kr, ks, todo = keyed(remote), keyed(files), {}
        for k, here in keyed(local).items():
            t = target(here, kr.get(k), ks.get(k))
            if canon(here) != canon(t):
                old, new = first_difference(here, t)
                todo.setdefault(key(old), (old, new))
        for old, new in todo.values():
            rep['renamed_here'].append([old, new])
            if not dry:
                try:
                    rename_local(root, old, new)
                except OSError as e:
                    leave_out(old, str(e))
        if not todo or dry:
            break
        local = scan_local(root, files)

    # Then renames on the server, and one spelling for each file everywhere.
    kl, ks, kr = keyed(local), keyed(files), keyed(remote)
    for k in sorted(set(kl) | set(ks) | set(kr)):
        here, before = kl.get(k), ks.get(k)
        if skipped(here or before or kr.get(k)):
            continue
        t = target(here, kr.get(k), before)
        for _ in range(t.count('/') + 1):
            there = kr.get(k)
            if not there or canon(there) == canon(t):
                break
            old, new = first_difference(there, t)
            if not dry:
                try:
                    move(client, old, new, folders)
                except Changed:
                    rep['retry'].append(old)
                    out.add(key(old))
                    break
            rep['renamed_there'].append([old, new])
            rekey(remote, old, new)
            rekey(folders, old, new)
            kr = keyed(remote)
        if skipped(t):
            continue
        if here and here != t:
            local[t] = local.pop(here)
        there = kr.get(k)
        if there and there != t:
            remote[t] = remote.pop(there)
        if before and before != t:
            files[t] = files.pop(before)
            if not dry:
                data = read_base(root, before)
                forget_base(root, before)
                if data is not None:
                    write_base(root, t, data)
    return local, skipped


# ---------- talking to the server ----------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # answered as an error instead: the password must not follow the redirect to another address


_opener = urllib.request.build_opener(_NoRedirect)
_AUTH_PARAM = re.compile(r'\s*,?\s*([!#$%&\'*+.^_`|~0-9A-Za-z-]+)(?:\s*=\s*("(?:[^"\\]|\\.)*"|[^\s,]*))?')


def _reason(e):
    text = str(getattr(e, 'reason', e))
    if 'CERTIFICATE_VERIFY_FAILED' in text:
        text += (". Python does not trust the server's certificate. If the server uses a certificate of its own, it "
                 "cannot be used. On macOS with Python from python.org, run 'Install Certificates.command' once.")
    return text


def digest_challenge(headers):
    """The parameters of the Digest challenge in a reply's WWW-Authenticate headers ({realm, nonce, qop, ...}), or
    None. A header can hold several challenges ('Basic realm="x", Digest realm="x", nonce=...')."""
    for value in (headers.get_all('WWW-Authenticate') or []) if headers is not None else []:
        m = re.search(r'(?:^|[\s,])Digest\s+', value, re.I)
        if not m:
            continue
        params, pos = {}, m.end()
        while True:
            t = _AUTH_PARAM.match(value, pos)
            if not t or t.end() == pos or t.group(2) is None:  # the end, or the next challenge's scheme
                break
            v = t.group(2)
            params.setdefault(t.group(1).lower(), re.sub(r'\\(.)', r'\1', v[1:-1]) if v.startswith('"') else v)
            pos = t.end()
        if params.get('nonce'):
            return params
    return None


def digest_answer(c, user, password, method, uri, nc, cnonce=None):
    """The Authorization header that answers Digest challenge c (RFC 7616, with qop "auth", or RFC 2069 without)."""
    algorithm = c.get('algorithm', 'MD5')
    name = DIGEST_HASHES.get(algorithm.upper().replace('-SESS', ''))
    qops = [q.strip().lower() for q in c.get('qop', '').split(',') if q.strip()]
    if name is None or name not in hashlib.algorithms_available or (qops and 'auth' not in qops):
        raise DavError(f'the server asks for a kind of Digest sign-in that is not supported (algorithm {algorithm}, '
                       f'qop {c.get("qop", "none")})')

    def h(s):
        return hashlib.new(name, s.encode('utf-8'), usedforsecurity=False).hexdigest()

    def q(s):
        return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'
    realm, nonce, cnonce, count = c.get('realm', ''), c.get('nonce', ''), cnonce or os.urandom(16).hex(), f'{nc:08x}'
    ha1 = h(f'{user}:{realm}:{password}')
    if algorithm.upper().endswith('-SESS'):
        ha1 = h(f'{ha1}:{nonce}:{cnonce}')
    ha2 = h(f'{method}:{uri}')
    response = h(f'{ha1}:{nonce}:{count}:{cnonce}:auth:{ha2}') if qops else h(f'{ha1}:{nonce}:{ha2}')
    fields = [f'username={q(user)}' if user.isascii() else f"username*=UTF-8''{quote(user, safe='')}",
              f'realm={q(realm)}', f'nonce={q(nonce)}', f'uri={q(uri)}', f'algorithm={algorithm}',
              f'response={q(response)}']
    if 'opaque' in c:
        fields.append(f'opaque={q(c["opaque"])}')
    if qops:
        fields += ['qop=auth', f'nc={count}', f'cnonce={q(cnonce)}']
    return 'Digest ' + ', '.join(fields)


class Client:
    """Requests to one folder. Signs in with Basic authentication, or with Digest once the server asks for it."""

    def __init__(self, url, user='', password=None):
        self.url, self.host = url, urlsplit(url).hostname
        self.base = unquote(urlsplit(url).path)
        self.user, self.password = user, password or ''
        self.digest, self.nc = (challenges.get((url, user)) if user else None), 0

    def authorization(self, method, url):
        if not self.user:
            return None
        if self.digest is None:
            return 'Basic ' + base64.b64encode(f'{self.user}:{self.password}'.encode()).decode()
        self.nc += 1
        return digest_answer(self.digest, self.user, self.password, method, urlsplit(url).path, self.nc)

    def call(self, method, rel='', data=None, headers=None, folder=False):
        """(status, headers, body) of one request for rel, a path relative to the folder."""
        url = self.url + quote(rel) + ('/' if folder and rel else '')
        for attempt in range(2):
            used = self.digest
            status, hdrs, body = self._send(method, url, data, headers, rel)
            if status != 401 or not self.user or attempt:
                return status, hdrs, body
            c = digest_challenge(hdrs)
            # No Digest offered, or the same challenge answered already: the password itself was refused. A new
            # challenge (Basic was sent, or the nonce expired) is answered once.
            if c is None or (used is not None and c.get('nonce') == used.get('nonce')):
                return status, hdrs, body
            self.digest, self.nc = c, 0
            challenges[(self.url, self.user)] = c

    def _send(self, method, url, data, headers, rel):
        req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
        auth = self.authorization(method, url)
        if auth:
            req.add_header('Authorization', auth)
        try:
            with _opener.open(req, timeout=TIMEOUT) as r:
                body = r.read(MAX_FILE + 1)
                if len(body) > MAX_FILE:
                    raise DavError(f'{rel or "the reply"} is larger than {MAX_FILE // 2**20} MB')
                return r.status, r.headers, body
        except urllib.error.HTTPError as e:
            status, hdrs = e.code, e.headers
            e.close()
            return status, hdrs, b''
        except urllib.error.URLError as e:
            raise DavError(f'cannot reach {self.host}: {_reason(e)}')
        except (OSError, http.client.HTTPException) as e:
            raise DavError(f'the connection to {self.host} failed: {_reason(e)}')


def expect(status, headers, what, ok):
    if status in ok:
        return
    if status == 401:
        raise NeedPassword('the server did not accept the user name and password. If your account uses two-factor '
                           'sign-in, create an app password (in Nextcloud: Settings, Security) and use that.')
    if status in (301, 302, 303, 307, 308):
        raise DavError(f'the server sends {what} to another address ({headers.get("Location")}); if that is the '
                       f'folder, enter that address instead')
    if status == 403:
        raise DavError(f'the server refused access to {what}')
    if status == 404:
        raise DavError(f'{what} was not found on the server')
    if status == 405:
        raise DavError(f'the server does not allow this for {what}; is this the address of a WebDAV folder?')
    if status == 507:
        raise DavError('the server is out of space')
    raise DavError(f'the server answered {status} for {what}')


def parse_multistatus(data, base):
    """[(rel, is_folder, version)] for the entries of a PROPFIND reply that are inside `base` (a decoded path that
    ends in a slash); rel is '' for base itself. The version is the ETag, or the date and size without one."""
    if b'<!DOCTYPE' in data or b'<!ENTITY' in data:
        raise DavError('the server sent an unexpected reply')
    try:
        tree = ET.fromstring(data)
    except ET.ParseError:
        raise DavError('the server sent a reply that is not WebDAV; is this the address of a WebDAV folder?')
    out = []
    for resp in tree.iter(DAV + 'response'):
        path = unquote(urlsplit((resp.findtext(DAV + 'href') or '').strip()).path)
        if path.rstrip('/') + '/' == base:
            rel = ''
        elif path.startswith(base):
            rel = path[len(base):].rstrip('/')
        else:
            continue
        prop = next((ps.find(DAV + 'prop') for ps in resp.findall(DAV + 'propstat')
                     if ' 200' in (ps.findtext(DAV + 'status') or '')), None)
        if prop is None:
            continue
        folder = prop.find(f'{DAV}resourcetype/{DAV}collection') is not None
        version = entity_tag(prop.findtext(DAV + 'getetag')) or '{}|{}'.format(
            (prop.findtext(DAV + 'getlastmodified') or '').strip(), (prop.findtext(DAV + 'getcontentlength') or '').strip())
        out.append((rel, folder, version))
    return out


def propfind(client, rel, depth, folder=True, missing_ok=False):
    """The entries for rel (and its contents, with depth 1); None when it is not there and missing_ok."""
    status, hdrs, data = client.call('PROPFIND', rel, PROPFIND, {'Depth': str(depth),
                                                               'Content-Type': 'application/xml; charset=utf-8'}, folder)
    if status == 404 and missing_ok:
        return None
    expect(status, hdrs, (rel or 'the folder') + ('/' if folder and rel else ''), (207,))
    return parse_multistatus(data, client.base)


def scan_remote(client):
    """({rel: version} of the synced files on the server, set of its folders), one folder level per request."""
    files, folders, todo = {}, {''}, ['']
    while todo:
        d = todo.pop()
        for rel, folder, version in propfind(client, d, 1):
            if rel == d or not valid_rel(rel):
                continue
            if folder:
                if rel not in folders and not project.skipped(rel):
                    folders.add(rel)
                    todo.append(rel)
                    if len(folders) > MAX_FOLDERS:
                        raise DavError('the folder on the server has too many subfolders')
            elif syncable(rel):
                files[rel] = version
    return files, folders


def entity_tag(etag):
    """An ETag as headers carry it, with its quotes. Some servers (WsgiDAV) list it without them; it would never
    match the one a PUT returns, and could not be sent in If-Match."""
    etag = (etag or '').strip()
    return etag if not etag or etag.startswith(('"', 'W/')) else f'"{etag}"'


def strong(version):
    return version is not None and version.startswith('"')


def version_of(client, rel):
    found = [v for r, folder, v in propfind(client, rel, 0, folder=False) if r == rel]
    return found[0] if found else None


def download(client, rel):
    status, hdrs, data = client.call('GET', rel)
    expect(status, hdrs, rel, (200,))
    return data


def make_folders(client, d, folders):
    """MKCOL folder d, and the folders it is in, where the server does not have them yet."""
    parts = d.split('/') if d else []
    for i in range(1, len(parts) + 1):
        sub = '/'.join(parts[:i])
        if sub not in folders:
            status, hdrs, _ = client.call('MKCOL', sub, folder=True)
            if status != 405:  # 405: it exists already
                expect(status, hdrs, sub + '/', (201,))
            folders.add(sub)


def upload(client, rel, data, version, folders):
    """PUT rel, making its folders. version: the server's version it replaces, or None for a new file. Returns the new
    version."""
    make_folders(client, parent(rel), folders)
    headers = {'Content-Type': 'application/octet-stream'}
    if version is None:
        headers['If-None-Match'] = '*'
    elif strong(version):
        headers['If-Match'] = version
    status, hdrs, _ = client.call('PUT', rel, data, headers)
    if status == 412:
        raise Changed(rel)
    expect(status, hdrs, rel, (200, 201, 204))
    etag = entity_tag(hdrs.get('ETag'))
    return etag if strong(etag) else version_of(client, rel)


def move(client, src, dst, folders):
    """MOVE file or folder src to dst on the server, without replacing anything there."""
    make_folders(client, parent(dst), folders)
    status, hdrs, _ = client.call('MOVE', src, headers={'Destination': client.url + quote(dst), 'Overwrite': 'F'})
    if status == 412:
        raise Changed(src)
    expect(status, hdrs, src, (201, 204))


def delete(client, rel, version):
    status, hdrs, _ = client.call('DELETE', rel, headers={'If-Match': version} if strong(version) else {})
    if status == 412:
        raise Changed(rel)
    if status != 404:
        expect(status, hdrs, rel, (200, 204))


def delete_folder(client, d):
    """Delete folder d on the server if it is empty. Returns False when it is not, or changed meanwhile. (DELETE on a
    folder deletes all that is in it, so it is asked for only after looking, and with If-Match where the folder has an
    ETag that changes with what is in it, as on Nextcloud and ownCloud.)"""
    entries = propfind(client, d, 1, missing_ok=True)
    if entries is None:
        return True
    if any(rel != d for rel, _, _ in entries):
        return False
    version = next((v for rel, _, v in entries if rel == d), None)
    status, hdrs, _ = client.call('DELETE', d, headers={'If-Match': version} if strong(version) else {}, folder=True)
    if status == 412:
        return False
    if status != 404:
        expect(status, hdrs, d + '/', (200, 204))
    return True


# ---------- credentials and the saved state ----------

def from_netrc(url, user):
    """(user, password) for the server in ~/.netrc, or None. Only an entry for the server's own host name counts."""
    try:
        n = netrc.netrc(NETRC)
    except FileNotFoundError:
        return None
    except (OSError, netrc.NetrcParseError) as e:
        raise DavError(f'cannot read your .netrc file: {e}')
    host = urlsplit(url).hostname
    if host not in n.hosts:
        return None
    login, _, password = n.authenticators(host)
    if password is None or (user and login != user):
        return None
    return login, password


def credentials(st):
    """(user, password, where the password comes from) for the saved connection."""
    url, user = st['url'], st.get('user', '')
    if (url, user) in passwords:
        return user, passwords[(url, user)], 'session'
    found = from_netrc(url, user)
    if found:
        return found[0], found[1], 'netrc'
    if user:
        raise NeedPassword(f'enter the password for {user} on {urlsplit(url).hostname} again: it is kept only while '
                           'the editor runs')
    return '', None, 'none'


def state_dir(root):
    return root / 'build' / '.editor-webdav'


def load_state(root):
    try:
        st = json.loads((state_dir(root) / 'state.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if not isinstance(st, dict) or not st.get('url'):
        return {}
    files = st.get('files') if isinstance(st.get('files'), dict) else {}
    st['files'] = {rel: rec for rel, rec in files.items()
                   if valid_rel(rel) and syncable(rel) and isinstance(rec, dict) and 'hash' in rec}
    for k in ('folders', 'folders_left'):
        st[k] = [d for d in st.get(k, []) if isinstance(d, str) and syncable_dir(d)] \
            if isinstance(st.get(k), list) else []
    return st


def save_state(root, st):
    d = state_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / 'state.json.tmp'
    tmp.write_text(json.dumps(st, indent=1, sort_keys=True), encoding='utf-8')
    os.replace(tmp, d / 'state.json')


def connected(root):
    st = load_state(root)
    if not st:
        raise DavError('this project is not connected to a WebDAV folder')
    return st


def client_for(st):
    user, password, _ = credentials(st)
    return Client(st['url'], user, password)


def base_file(root, rel):
    return state_dir(root) / 'base' / rel


def read_base(root, rel):
    try:
        return base_file(root, rel).read_bytes()
    except OSError:
        return None


def write_base(root, rel, data):
    b = base_file(root, rel)
    b.parent.mkdir(parents=True, exist_ok=True)
    b.write_bytes(data)


def forget_base(root, rel):
    try:
        base_file(root, rel).unlink()
    except OSError:
        pass


def record(root, files, rel, data, version, stat=None):
    """Remember rel as synced: data is its content on both sides now, version the server's version of it. `stat` (of
    the local file, taken before reading it) lets the next scan skip reading it while it stays the same; a file
    changed in the last two seconds is read again anyway, as a save in the same clock tick would not show."""
    rec = {'hash': sha(data), 'etag': version}
    if stat is not None and time.time() - stat.st_mtime > 2:
        rec.update(size=stat.st_size, mtime=stat.st_mtime_ns)
    files[rel] = rec
    if is_text(rel):
        write_base(root, rel, data)
    else:
        forget_base(root, rel)


def forget(root, files, rel):
    files.pop(rel, None)
    forget_base(root, rel)


def scan_local(root, files):
    """{rel: (hash, stat)} of the project files that are synced."""
    out = {}
    editable, assets = project.list_files(root)
    for rel in editable + assets:
        if not syncable(rel):
            continue
        try:
            p = inside(root, rel)
            st = p.stat()
            rec = files.get(rel)
            if rec and rec.get('size') == st.st_size and rec.get('mtime') == st.st_mtime_ns:
                h = rec['hash']
            else:
                h = sha(p.read_bytes())
        except (OSError, PermissionError):
            continue
        out[rel] = (h, st)
    return out


def local_dirs(root):
    out = []
    for d in project.list_dirs(root):
        if syncable_dir(d):
            try:
                inside(root, d)
            except PermissionError:
                continue
            out.append(d)
    return out


def write_local(root, rel, data):
    p = inside(root, rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f'.{p.name}.editor-sync')
    tmp.write_bytes(data)
    if p.exists():
        shutil.copymode(p, tmp)
    os.replace(tmp, p)


def read_local(root, rel):
    try:
        return inside(root, rel).read_bytes()
    except FileNotFoundError:
        return None


# ---------- deciding ----------

def classify(rel, rec, here, there, accept=()):
    """What a sync does with rel. here: its hash, there: its server version (None where it does not exist); rec: the
    record of the last sync, or None."""
    changed_here = here != (rec['hash'] if rec else None)
    changed_there = there != (rec.get('etag') if rec else None)
    if not (changed_here or changed_there):
        return 'same'
    if changed_there and there is not None and is_rc(rel) and rel not in accept:
        return 'held'
    if not changed_there:
        return 'delete_there' if here is None else 'upload'
    if not changed_here:
        return 'delete_here' if there is None else 'download'
    if here is None:
        return 'forget' if there is None else 'download'  # deleted here, changed there: keep the changes
    if there is None:
        return 'upload'  # deleted there, changed here: keep the changes
    return 'conflicts' if rec else 'compare'


def folder_actions(root, st, folders, remote, key, skipped):
    """What to do with folders, after the files: {make_there, make_here, delete_here, delete_there: [...]} and the
    folders that stay as they are. A folder left (deleted on one side, but not empty on the other) is left alone while
    it is on one side only."""
    known, left = ({key(d): d for d in st.get(k, [])} for k in ('folders', 'folders_left'))
    here = {key(d): d for d in local_dirs(root) if not skipped(d)}
    there = {key(d): d for d in folders if d and syncable_dir(d) and not skipped(d)}
    files_there = {key(r) for r in remote}
    acts = {'make_there': [], 'make_here': [], 'delete_here': [], 'delete_there': [], 'keep': [], 'left': []}
    for k in set(known) | set(left) | set(here) | set(there):
        h, t = here.get(k), there.get(k)
        if h and t:
            acts['keep'].append(t)
        elif (h or t) and k in left:
            acts['left'].append(h or t)
        elif h:
            if k in known:
                acts['delete_here'].append(h)
            elif k not in files_there:  # (a file of that name on the server: the folder cannot be made there)
                acts['make_there'].append(h)
        elif t:
            acts['delete_there' if k in known else 'make_here'].append(t)
    return acts


def is_main_test(root, main):
    key = name_key(case_insensitive(root))
    return lambda rel: main is not None and key(rel) == key(main)


def changes_here(root, st):
    """[{path, state}] of the files (and folders, ending in /) changed here since the last sync, and the files that
    cannot be uploaded yet."""
    files = st.get('files', {})
    local = scan_local(root, files)
    key = name_key(case_insensitive(root))
    kl, ks = {key(r): r for r in local}, {key(r): r for r in files}
    changed, held = [], []
    for k in sorted(set(kl) | set(ks), key=lambda k: kl.get(k) or ks.get(k)):
        rel, before = kl.get(k), ks.get(k)
        h, rec = (local[rel][0] if rel else None), (files[before] if before else None)
        if rel and before and canon(rel) != canon(before):
            changed.append({'path': rel, 'state': 'renamed', 'from': before})
        elif h == (rec['hash'] if rec else None):
            continue
        else:
            changed.append({'path': rel or before, 'state': 'deleted' if h is None else 'changed' if rec else 'new'})
        if h is not None:
            why = blocked(root, rel, read_local(root, rel) or b'')
            if why:
                held.append(dict(why, path=rel))
    known, left = ({key(d) for d in st.get(k, [])} for k in ('folders', 'folders_left'))
    here = {key(d): d for d in local_dirs(root)}
    paths = [c['path'] for c in changed]
    for k, d in here.items():
        if k not in known and k not in left and not any(p.startswith(d + '/') for p in paths):
            changed.append({'path': d + '/', 'state': 'new'})
    for d in st.get('folders', []):
        if key(d) not in here and not any(p.startswith(d + '/') for p in paths):
            changed.append({'path': d + '/', 'state': 'deleted'})
    return changed, held


def status(root):
    """The connection and what changed here, without asking the server."""
    st = load_state(root)
    view = {'connected': bool(st), 'merge': shutil.which('git') is not None}
    if not st:
        return view
    try:
        source, need = credentials(st)[2], False
    except DavError:
        source, need = None, True
    changed, held = changes_here(root, st)
    return dict(view, url=st['url'], user=st.get('user', ''), password=source, needs_password=need,
                last_sync=st.get('last_sync'), changed_here=changed, blocked=held)


# ---------- connecting, checking, syncing ----------

def connect(root, url, user='', password=''):
    """Check the folder and the password, and save the connection (not the password). Connecting again to the same
    folder keeps what the last sync recorded."""
    url, user = normalize_url(url), (user or '').strip()
    if password and not user:
        raise DavError('enter the user name too')
    if password:
        login, pw = user, password
    else:
        found = from_netrc(url, user)
        if found:
            login, pw = found
        elif user:
            raise NeedPassword('enter the password, or put it in your .netrc file')
        else:
            login, pw = '', None
    entries = propfind(Client(url, login, pw), '', 0)
    here = [folder for rel, folder, _ in entries if rel == '']
    if not here:
        raise DavError("the server's reply does not match this address")
    if not here[0]:
        raise DavError('this is the address of a file; enter the address of a folder')
    st = load_state(root)
    if (st.get('url'), st.get('user', '')) != (url, user):
        shutil.rmtree(state_dir(root), ignore_errors=True)
        st = {'url': url, 'user': user, 'files': {}, 'folders': [], 'folders_left': []}
    save_state(root, st)
    if password:
        passwords[(url, user)] = password
    return status(root)


def disconnect(root):
    st = load_state(root)
    if st:
        passwords.pop((st['url'], st.get('user', '')), None)
        challenges.pop((st['url'], st.get('user', '')), None)
    shutil.rmtree(state_dir(root), ignore_errors=True)
    return status(root)


@contextlib.contextmanager
def session(st):
    """A client for the saved connection. A password the server refuses is forgotten, so the panel asks again."""
    try:
        yield client_for(st)
    except NeedPassword:
        passwords.pop((st['url'], st.get('user', '')), None)
        raise


def check(root, main=None):
    """What a sync would do now: {upload: [...], download: [...], ...}, asking the server but changing nothing."""
    st = connected(root)
    with session(st) as client:
        remote, folders = scan_remote(client)
    files = st['files']
    rep = {k: [] for k in RESULT_KEYS}
    local, skipped = unify(root, None, scan_local(root, files), remote, folders, files, rep, dry=True)
    is_main = is_main_test(root, main)
    plan = {k: [] for k in PLAN_KEYS}
    for rel in sorted(set(local) | set(remote) | set(files)):
        if skipped(rel):
            continue
        action = classify(rel, files.get(rel), local[rel][0] if rel in local else None, remote.get(rel))
        if action == 'delete_here' and is_main(rel):
            action = 'upload'
        if action in plan:
            plan[action].append(rel)
    acts = folder_actions(root, st, folders, remote, name_key(case_insensitive(root)), skipped)
    for act, name in (('make_there', 'upload'), ('make_here', 'download'), ('delete_here', 'delete_here'),
                      ('delete_there', 'delete_there')):
        plan[name] += [d + '/' for d in sorted(acts[act]) if not any(p.startswith(d + '/') for p in plan[name])]
    plan.update(rename_here=rep['renamed_here'], rename_there=rep['renamed_there'], clashes=rep['clashes'])
    return dict(status(root), plan=plan)


def sync(root, main=None, accept=()):
    """Sync every file and folder (see the module's docstring). Returns the status, what was done ('result') and what
    is left ('plan')."""
    st = connected(root)
    accept = {a for a in accept if isinstance(a, str)} if isinstance(accept, list) else set()
    rep = {k: [] for k in RESULT_KEYS}
    stamp = time.strftime('%Y%m%d-%H%M%S')
    files = st['files']
    is_main = is_main_test(root, main)
    with session(st) as client:
        remote, folders = scan_remote(client)
        try:
            local, skipped = unify(root, client, scan_local(root, files), remote, folders, files, rep)
            for rel in sorted(set(local) | set(remote) | set(files)):
                if skipped(rel):
                    continue
                here, stat = local.get(rel, (None, None))
                try:
                    _sync_one(root, client, files, folders, rep, rel, here, stat, remote.get(rel), is_main, accept,
                              stamp)
                except Changed:
                    rep['retry'].append(rel)
                except OSError as e:  # a path that leads out of the project, a folder where a file goes, a full disk
                    rep['skipped'].append({'path': rel, 'error': str(e)})
            _sync_folders(root, client, st, folders, remote, rep, skipped)
            st['last_sync'] = time.time()
        finally:
            save_state(root, st)
    plan = {k: [] for k in PLAN_KEYS}
    plan.update(held=rep['held'], clashes=rep['clashes'])
    return dict(status(root), result=rep, plan=plan)


def _sync_one(root, client, files, folders, rep, rel, here, stat, there, is_main, accept, stamp):
    rec = files.get(rel)
    action = classify(rel, rec, here, there, accept)
    if action == 'delete_here' and is_main(rel):
        action = 'upload'  # never delete the document being compiled: put it back on the server
        rep['kept'].append(rel)
    if action == 'same':
        if stat is not None and 'mtime' not in rec and time.time() - stat.st_mtime > 2:
            rec.update(size=stat.st_size, mtime=stat.st_mtime_ns)
    elif action == 'held':
        rep['held'].append(rel)
    elif action == 'forget':
        forget(root, files, rel)
    elif action == 'upload':
        data = read_local(root, rel)
        if data is None:
            raise Changed(rel)
        why = blocked(root, rel, data)
        if why:
            rep['blocked'].append(dict(why, path=rel))
            return
        version = upload(client, rel, data, there, folders)
        record(root, files, rel, data, version, stat if sha(data) == here else None)
        rep['uploaded'].append(rel)
    elif action == 'delete_there':
        delete(client, rel, there)
        forget(root, files, rel)
        rep['deleted_there'].append(rel)
    elif action == 'delete_here':
        mine = read_local(root, rel)
        if mine is None or sha(mine) != here:
            raise Changed(rel)
        project.trash(root, inside(root, rel), stamp)
        forget(root, files, rel)
        rep['deleted_here'].append(rel)
    elif action == 'download':
        data = download(client, rel)
        mine = read_local(root, rel)
        if (sha(mine) if mine is not None else None) != here:
            raise Changed(rel)  # saved here while syncing: leave it for the next sync
        if mine != data:
            write_local(root, rel, data)
            rep['downloaded'].append(rel)
        record(root, files, rel, data, there, stat if mine == data else None)
    else:  # 'conflicts' or 'compare': changed on both sides, or on both sides and never synced
        theirs, mine = download(client, rel), read_local(root, rel)
        if mine is None or sha(mine) != here:
            raise Changed(rel)
        if theirs == mine:
            record(root, files, rel, mine, there, stat)
            return
        base = read_base(root, rel) if action == 'conflicts' and is_text(rel) else None
        merged = merge(mine, base, theirs) if base is not None else None
        if merged is not None and merged[1] == 0:
            if merged[0] != mine:
                write_local(root, rel, merged[0])
            version = upload(client, rel, merged[0], there, folders) if merged[0] != theirs else there
            record(root, files, rel, merged[0], version)
            rep['merged'].append(rel)
        elif merged is not None:
            # Overlapping edits: conflict markers here. Recorded as the server's version, so the file is uploaded
            # by the first sync after the markers are gone.
            write_local(root, rel, merged[0])
            record(root, files, rel, theirs, there)
            rep['conflicts'].append(rel)
        else:
            copy = free_copy(root, rel)
            write_local(root, copy, theirs)
            record(root, files, rel, theirs, there)
            rep['copies'].append({'path': rel, 'copy': copy})


def _sync_folders(root, client, st, folders, remote, rep, skipped):
    """Make and delete folders, after the files (whose uploads and downloads make the folders they need)."""
    acts = folder_actions(root, st, folders, remote, name_key(case_insensitive(root)), skipped)
    keep, left = set(acts['keep']), set(acts['left'])
    depth = lambda d: (d.count('/'), d)  # noqa: E731  (parents first; the same order every time)

    def attempt(d, fn):
        try:
            return fn()
        except OSError as e:  # a file where the folder goes, a path that leads out of the project
            rep['skipped'].append({'path': d + '/', 'error': str(e)})
        except Changed:
            rep['retry'].append(d + '/')
            keep.add(d)

    for d in sorted(acts['make_there'], key=depth):
        if attempt(d, lambda: make_folders(client, d, folders) or True):
            keep.add(d)
            rep['uploaded'].append(d + '/')
    for d in sorted(acts['make_here'], key=depth):
        if attempt(d, lambda: inside(root, d).mkdir(parents=True, exist_ok=True) or True):
            keep.add(d)
            rep['downloaded'].append(d + '/')
    for d in sorted(acts['delete_here'], key=depth, reverse=True):
        try:
            inside(root, d).rmdir()
            rep['deleted_here'].append(d + '/')
        except OSError:  # not empty: what is in it (not synced) stays, and so does the folder
            left.add(d)
    for d in sorted(acts['delete_there'], key=depth, reverse=True):
        if delete_folder(client, d):
            folders.discard(d)
            rep['deleted_there'].append(d + '/')
        else:
            left.add(d)
    st['folders'], st['folders_left'] = sorted(keep), sorted(left)


def merge(mine, base, theirs):
    """Three-way merge with `git merge-file`: (merged bytes, number of conflicts), or None without git or for files
    git cannot merge (binary ones)."""
    if shutil.which('git') is None:
        return None
    with tempfile.TemporaryDirectory() as d:
        paths = []
        for name, data in (('mine', mine), ('base', base), ('theirs', theirs)):
            p = Path(d) / name
            p.write_bytes(data)
            paths.append(str(p))
        try:
            r = subprocess.run(['git', 'merge-file', '-p', '-L', 'this computer', '-L', 'last sync', '-L', 'server',
                                *paths], cwd=d, capture_output=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            return None
    return (r.stdout, r.returncode) if 0 <= r.returncode < 128 else None


def peek(root, rel):
    """A unified diff from the file here to the server's version: to review a .latexmkrc before accepting it."""
    if not isinstance(rel, str) or not valid_rel(rel) or not syncable(rel) or not is_text(rel):
        raise DavError(f'{rel}: not a text file that is synced')
    with session(connected(root)) as client:
        theirs = download(client, rel)
    mine = read_local(root, rel) or b''
    diff = difflib.unified_diff(mine.decode('utf-8', 'replace').splitlines(), theirs.decode('utf-8', 'replace').splitlines(),
                                f'{rel} (this computer)', f'{rel} (server)', lineterm='')
    return {'path': rel, 'diff': '\n'.join(diff) or 'No differences.'}


def clone(root, url, user, ask_password):
    """For --webdav: connect root (created if needed) to the folder and sync it, asking for the password with
    ask_password(prompt) when there is none. Returns a summary to print."""
    root.mkdir(parents=True, exist_ok=True)
    try:
        connect(root, url, user)
    except NeedPassword:
        if not user:
            raise DavError('the server asks for a user name: add --webdav-user NAME')
        connect(root, url, user, ask_password(f'Password for {user} on {urlsplit(normalize_url(url)).hostname}: '))
    r = sync(root)['result']
    count = lambda k: sum(1 for p in r[k] if not p.endswith('/'))  # noqa: E731
    lines = [f'WebDAV: {count("downloaded")} downloaded, {count("uploaded")} uploaded']
    if r['copies']:
        lines.append('These files differ from the server, whose versions are saved next to them as "(server copy)": '
                     + ', '.join(c['path'] for c in r['copies']))
    if r['held']:
        lines.append(f'Not downloaded: {", ".join(r["held"])}. It runs as a program when you compile; review and '
                     'accept it in the WebDAV panel.')
    for c in r['clashes']:
        if c['skipped']:
            lines.append(f'Not synced: {" and ".join(c["names"])}, which differ only in case and cannot both be kept '
                         'on this computer. Rename one of them on the server.')
    return '\n'.join(lines)
