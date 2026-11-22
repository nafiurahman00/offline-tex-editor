"""Tests for WebDAV sync, against a small WebDAV server that runs in the test (standard library only):

    python3 -m unittest discover -s tests -v

The merge tests need git and are skipped without it.
"""
import base64
import hashlib
import http.client
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from offline_tex_editor import server, webdav  # noqa: E402
from test_server import ServerCase, write  # noqa: E402

HAVE_GIT = shutil.which('git') is not None
MAIN = '\\documentclass{article}\n\\begin{document}\nHello.\n\\input{intro}\n\\end{document}\n'
INTRO = ''.join(f'Line {i} of the introduction.\n' for i in range(1, 21))
with tempfile.TemporaryDirectory() as _d:
    (Path(_d) / 'probe').touch()
    FS_IGNORES_CASE = (Path(_d) / 'PROBE').exists()


def parent(path):
    return path.rpartition('/')[0]


def under(path, prefix):
    return path == prefix or path.startswith(prefix + '/')


class FakeDav:
    """A WebDAV server kept in memory, with what the sync uses: PROPFIND (Depth 0 and 1), GET, PUT, DELETE, MKCOL and
    MOVE, ETags (a folder's changes with what is in it, as on Nextcloud), If-Match and If-None-Match, and Basic or
    Digest authentication. It tells names apart by case on every system. The tests' folder is <url> = .../Thesis/,
    and the paths the helpers (write, read, ...) take are relative to it.

    extra: more entries, as (href, is_folder), for every Depth 1 PROPFIND reply: a hostile server.
    put_etag: whether PUT replies carry the new ETag. hooks: {method: f}, f(path) is called before such a request is
    answered, with the path under the user's folder (Thesis/...). digest: None for Basic, or the Digest algorithm to
    ask for ('MD5' or 'SHA-256'); both: offer Basic too, in the same header.
    """

    def __init__(self, user='alice', password='s3cret'):
        self.user, self.password = user, password
        self.prefix = '/dav/files/alice/'
        self.data, self.dirs, self.version = {}, {'', 'Thesis'}, 0  # path under the prefix -> (content, version)
        self.digest, self.both, self.stale = None, False, False
        self.realm, self.nonce, self.opaque = 'dav', 'nonce-1', 'opaque-1'
        self.extra, self.put_etag, self.hooks, self.log = [], True, {}, []
        self.lock = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def reply(self, code, body=b'', headers=None):
                self.send_response(code)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def handle_one(self):
                n = int(self.headers.get('Content-Length') or 0)
                body = self.rfile.read(n) if n else b''
                path, auth = unquote(urlsplit(self.path).path), self.headers.get('Authorization') or ''
                fake.log.append((self.command, path, auth))
                with fake.lock:
                    if not fake.authorized(self.command, self.path, auth):
                        return self.reply(401, headers={'WWW-Authenticate': fake.challenge()})
                    if path.startswith('/moved/'):
                        return self.reply(301, headers={'Location': 'https://elsewhere.example/'})
                    if not path.startswith(fake.prefix):
                        return self.reply(404)
                    rel = path[len(fake.prefix):].strip('/')
                    if self.command in fake.hooks:
                        fake.hooks[self.command](rel)
                    return getattr(self, 'dav_' + self.command)(rel, body)

            do_PROPFIND = do_GET = do_PUT = do_DELETE = do_MKCOL = do_MOVE = handle_one

            def dav_PROPFIND(self, rel, body):
                if not fake.there(rel):
                    return self.reply(404)
                entries = [rel]
                if rel in fake.dirs and self.headers.get('Depth') == '1':
                    entries += sorted(p for p in set(fake.data) | fake.dirs if p and parent(p) == rel)
                xml = ['<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">']
                for r in entries:
                    folder = r in fake.dirs
                    xml.append(fake.response(quote(fake.prefix + r) + ('/' if folder else ''), folder, fake.etag(r)))
                if self.headers.get('Depth') == '1':
                    xml += [fake.response(h, folder, '"x"') for h, folder in fake.extra]
                xml.append('</d:multistatus>')
                self.reply(207, ''.join(xml).encode(), {'Content-Type': 'application/xml; charset=utf-8'})

            def dav_GET(self, rel, body):
                if rel not in fake.data:
                    return self.reply(405 if rel in fake.dirs else 404)
                self.reply(200, fake.data[rel][0], {'ETag': fake.etag(rel)})

            def dav_PUT(self, rel, body):
                if rel in fake.dirs:
                    return self.reply(405)
                if parent(rel) not in fake.dirs:
                    return self.reply(409)
                if not fake.preconditions(self.headers, rel):
                    return self.reply(412)
                existed = rel in fake.data
                fake.store(rel, body)
                self.reply(204 if existed else 201, headers={'ETag': fake.etag(rel)} if fake.put_etag else {})

            def dav_DELETE(self, rel, body):
                if not fake.there(rel):
                    return self.reply(404)
                if not fake.preconditions(self.headers, rel):
                    return self.reply(412)
                fake.drop(rel)
                self.reply(204)

            def dav_MKCOL(self, rel, body):
                if fake.there(rel):
                    return self.reply(405)
                if parent(rel) not in fake.dirs:
                    return self.reply(409)
                fake.dirs.add(rel)
                self.reply(201)

            def dav_MOVE(self, rel, body):
                dst = unquote(urlsplit(self.headers.get('Destination', '')).path)
                if not dst.startswith(fake.prefix):
                    return self.reply(502)
                dst = dst[len(fake.prefix):].strip('/')
                if not fake.there(rel):
                    return self.reply(404)
                if not fake.preconditions(self.headers, rel) or (fake.there(dst) and self.headers.get('Overwrite') == 'F'):
                    return self.reply(412)
                if parent(dst) not in fake.dirs:
                    return self.reply(409)
                fake.drop(dst)
                fake.move(rel, dst)
                self.reply(201)

        self.srv = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f'http://127.0.0.1:{self.srv.server_address[1]}{self.prefix}Thesis/'

    # ---------- the server's side ----------

    def there(self, path):
        return path in self.data or path in self.dirs

    def etag(self, path):
        if path in self.data:
            return f'"{self.data[path][1]}"'
        inside = sorted((p, v) for p, (_, v) in self.data.items() if under(p, path)) + sorted(
            d for d in self.dirs if under(d, path))
        return '"d' + hashlib.sha1(repr(inside).encode()).hexdigest()[:12] + '"'

    def preconditions(self, headers, path):
        match, none = headers.get('If-Match'), headers.get('If-None-Match')
        return not ((match and (not self.there(path) or self.etag(path) != match)) or (none == '*' and self.there(path)))

    def store(self, path, data):
        self.version += 1
        self.data[path] = (data, self.version)

    def drop(self, path):
        for p in [p for p in self.data if under(p, path)]:
            del self.data[p]
        self.dirs -= {d for d in self.dirs if under(d, path)}

    def move(self, src, dst):
        for p in [p for p in self.data if under(p, src)]:
            self.data[dst + p[len(src):]] = self.data.pop(p)
        moved = {d for d in self.dirs if under(d, src)}
        self.dirs = (self.dirs - moved) | {dst + d[len(src):] for d in moved}

    def authorized(self, method, target, auth):
        if self.digest is None:
            return auth == 'Basic ' + base64.b64encode(f'{self.user}:{self.password}'.encode()).decode()
        if not auth.startswith('Digest '):
            return False
        p = {k: v.strip('"') for k, v in re.findall(r'(\w+)=("[^"]*"|[^,\s]+)', auth[7:])}
        if p.get('nonce') != self.nonce:
            self.stale = True
            return False
        name = {'MD5': 'md5', 'SHA-256': 'sha256'}[self.digest]

        def h(s):
            return hashlib.new(name, s.encode()).hexdigest()
        ha1, ha2 = h(f'{self.user}:{self.realm}:{self.password}'), h(f'{method}:{p.get("uri")}')
        want = h(f'{ha1}:{self.nonce}:{p.get("nc")}:{p.get("cnonce")}:auth:{ha2}')
        return (p.get('username') == self.user and p.get('uri') == target and p.get('qop') == 'auth'
                and p.get('algorithm') == self.digest and p.get('opaque') == self.opaque and p.get('response') == want)

    def challenge(self):
        if self.digest is None:
            return 'Basic realm="dav"'
        c = f'Digest realm="{self.realm}", nonce="{self.nonce}", qop="auth", algorithm={self.digest}, opaque="{self.opaque}"'
        if self.stale:
            c, self.stale = c + ', stale=true', False
        return 'Basic realm="dav", ' + c if self.both else c

    @staticmethod
    def response(href, folder, etag):
        props = ('<d:resourcetype><d:collection/></d:resourcetype>' if folder else '<d:resourcetype/>') + (
            f'<d:getetag>{etag}</d:getetag>' if etag else '')
        return (f'<d:response><d:href>{href}</d:href><d:propstat><d:prop>{props}</d:prop>'
                '<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>')

    # ---------- helpers for the tests: what a teammate (or the server's web page) does ----------

    def _p(self, rel):
        return f'Thesis/{rel}' if rel else 'Thesis'

    def write(self, rel, data):
        self.mkdir(parent(rel))
        self.store(self._p(rel), data if isinstance(data, bytes) else data.encode())

    def mkdir(self, rel):
        parts = self._p(rel).split('/')
        self.dirs |= {'/'.join(parts[:i]) for i in range(1, len(parts) + 1)}

    def read(self, rel):
        return self.data[self._p(rel)][0]

    def text(self, rel):
        return self.read(rel).decode()

    def exists(self, rel):
        return self.there(self._p(rel))

    def remove(self, rel):
        self.drop(self._p(rel))

    def rename(self, old, new):
        self.move(self._p(old), self._p(new))

    def files(self):
        return sorted(p[len('Thesis/'):] for p in self.data if p.startswith('Thesis/'))

    def folders(self):
        return sorted(d[len('Thesis/'):] for d in self.dirs if d.startswith('Thesis/'))

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class DavCase(unittest.TestCase):
    """A fake server and two teammates' copies of the project, A (which has the project) and B (empty)."""

    def setUp(self):
        webdav.passwords.clear()
        webdav.challenges.clear()
        self.dav = FakeDav()
        self.tmp = tempfile.TemporaryDirectory()
        self.a = Path(self.tmp.name).resolve() / 'a'
        self.b = Path(self.tmp.name).resolve() / 'b'
        write(self.a, {'main.tex': MAIN, 'intro.tex': INTRO, 'refs.bib': '@book{k, title={T}}\n',
                       'figs/plot.png': b'\x89PNG one', 'build/main.aux': 'not synced', 'notes.docx': b'not synced'})
        self.b.mkdir()

    def tearDown(self):
        self.dav.close()
        self.tmp.cleanup()
        webdav.NETRC = None
        webdav.passwords.clear()
        webdav.challenges.clear()

    def connect(self, root, password='s3cret'):
        return webdav.connect(root, self.dav.url, 'alice', password)

    def sync(self, root, **kw):
        return webdav.sync(root, 'main.tex', **kw)['result']

    def both(self):
        """A has synced the project, and B has downloaded it."""
        self.connect(self.a)
        self.sync(self.a)
        self.connect(self.b)
        self.sync(self.b)

    def names(self, root):
        return sorted(p.relative_to(root).as_posix() for p in root.rglob('*') if 'build' not in p.relative_to(root).parts)


class Addresses(unittest.TestCase):
    def test_normalize_url(self):
        n = webdav.normalize_url
        self.assertEqual(n(' https://cloud.example.org/remote.php/dav/files/me/My Thesis '),
                         'https://cloud.example.org/remote.php/dav/files/me/My%20Thesis/')
        self.assertEqual(n('https://cloud.example.org/a%20b/'), 'https://cloud.example.org/a%20b/')
        self.assertEqual(n('http://localhost:8080/dav'), 'http://localhost:8080/dav/')
        for bad in ('http://cloud.example.org/dav/', 'ftp://x/', 'cloud.example.org/dav', '',
                    'https://me:pw@cloud.example.org/', 'https://cloud.example.org/?a=1'):
            with self.subTest(bad=bad), self.assertRaises(webdav.DavError):
                n(bad)

    def test_paths(self):
        for good in ('main.tex', 'figs/plot.png', '.latexmkrc', 'a b/c.tex'):
            self.assertTrue(webdav.valid_rel(good), good)
        for bad in ('', '../x.tex', 'a/../../x.tex', '/etc/x.tex', 'a//b.tex', 'c:x.tex', 'a\\b.tex', 'x.tex.',
                    'x.tex ', 'a/./b.tex', 'x\n.tex'):
            self.assertFalse(webdav.valid_rel(bad), bad)
        self.assertTrue(webdav.syncable('figs/plot.png'))
        for no in ('build/main.tex', '.git/config.tex', 'notes.docx', 'intro (server copy).tex', 'x.exe'):
            self.assertFalse(webdav.syncable(no), no)
        self.assertTrue(webdav.syncable_dir('chapters/drafts'))
        for no in ('.vscode', 'a/.hidden', 'build', 'a/build'):
            self.assertFalse(webdav.syncable_dir(no), no)
        self.assertEqual(webdav.copy_name('sec/intro.tex'), 'sec/intro (server copy).tex')
        self.assertEqual(webdav.copy_name('fig.png', 2), 'fig (server copy 2).png')
        self.assertEqual(webdav.copy_name('.latexmkrc'), '.latexmkrc (server copy).txt')
        self.assertEqual(webdav.first_difference('figs/a.png', 'Figs/a.png'), ('figs', 'Figs'))
        self.assertEqual(webdav.first_difference('sec/a.tex', 'sec/A.tex'), ('sec/a.tex', 'sec/A.tex'))

    def test_multistatus_outside_the_folder_is_dropped(self):
        base = '/dav/Thesis/'
        xml = ('<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">'
               + FakeDav.response('/dav/Thesis/', True, None)
               + FakeDav.response('https://host/dav/Thesis/a%20b.tex', False, '"1"')
               + FakeDav.response('/dav/Other/x.tex', False, '"2"')
               + FakeDav.response('/dav/Thesis/sub/', True, None)
               + '<d:response><d:href>/dav/Thesis/gone.tex</d:href><d:propstat><d:prop/>'
                 '<d:status>HTTP/1.1 404 Not Found</d:status></d:propstat></d:response>'
               + '</d:multistatus>').encode()
        self.assertEqual(webdav.parse_multistatus(xml, base),
                         [('', True, '|'), ('a b.tex', False, '"1"'), ('sub', True, '|')])
        with self.assertRaises(webdav.DavError):
            webdav.parse_multistatus(b'<!DOCTYPE x [<!ENTITY a "aaaa">]><x>&a;</x>', base)


class DigestAnswers(unittest.TestCase):
    def test_the_rfc_examples(self):
        # RFC 2617, section 3.5 (MD5), and RFC 7616, section 3.9.1 (SHA-256)
        c = {'realm': 'testrealm@host.com', 'nonce': 'dcd98b7102dd2f0e8b11d0f600bfb0c093', 'qop': 'auth,auth-int',
             'opaque': '5ccc069c403ebaf9f0171e9517f40e41'}
        h = webdav.digest_answer(c, 'Mufasa', 'Circle Of Life', 'GET', '/dir/index.html', 1, cnonce='0a4f113b')
        for part in ('Digest username="Mufasa"', 'response="6629fae49393a05397450978507c4ef1"', 'qop=auth',
                     'nc=00000001', 'cnonce="0a4f113b"', 'opaque="5ccc069c403ebaf9f0171e9517f40e41"',
                     'uri="/dir/index.html"'):
            self.assertIn(part, h)
        c = {'realm': 'http-auth@example.org', 'nonce': '7ypf/xlj9XXwfDPEoM4URrv/xwf94BcCAzFZH4GiTo0v', 'qop': 'auth',
             'algorithm': 'SHA-256', 'opaque': 'FQhe/qaU925kfnzjCev0ciny7QMkPqMAFRtzCUYo5tdS'}
        h = webdav.digest_answer(c, 'Mufasa', 'Circle of Life', 'GET', '/dir/index.html', 1,
                                 cnonce='f2/wE4q74E6zIJEtWaHKaf5wv/H5QzzpXusqGemxURZJ')
        self.assertIn('response="753927fa0e85d155564e2e272a28d1802ca10daf4496794697cf8db5856cb6c1"', h)
        self.assertIn('algorithm=SHA-256', h)
        with self.assertRaisesRegex(webdav.DavError, 'not supported'):
            webdav.digest_answer({'nonce': 'n', 'qop': 'auth-int'}, 'u', 'p', 'GET', '/', 1)
        with self.assertRaisesRegex(webdav.DavError, 'not supported'):
            webdav.digest_answer({'nonce': 'n', 'algorithm': 'SHA-1'}, 'u', 'p', 'GET', '/', 1)

    def test_challenges(self):
        def headers(*values):
            m = http.client.HTTPMessage()
            for v in values:
                m['WWW-Authenticate'] = v
            return m
        c = webdav.digest_challenge(headers('Basic realm="x", Digest realm="dav, \\"team\\"", nonce="n1", '
                                            'qop="auth,auth-int", algorithm=SHA-256, stale=TRUE'))
        self.assertEqual(c, {'realm': 'dav, "team"', 'nonce': 'n1', 'qop': 'auth,auth-int', 'algorithm': 'SHA-256',
                             'stale': 'TRUE'})
        self.assertEqual(webdav.digest_challenge(headers('Basic realm="x"', 'Digest realm="y", nonce="n2"'))['nonce'],
                         'n2')
        self.assertIsNone(webdav.digest_challenge(headers('Basic realm="Digest"')))
        self.assertIsNone(webdav.digest_challenge(headers('Digest realm="no nonce"')))


class Connecting(DavCase):
    def test_wrong_password_and_folder_checks(self):
        with self.assertRaises(webdav.NeedPassword) as cm:
            self.connect(self.a, 'wrong')
        self.assertIn('user name and password', str(cm.exception))
        self.assertFalse(webdav.status(self.a)['connected'])
        with self.assertRaises(webdav.NeedPassword):  # a user name without a password
            webdav.connect(self.a, self.dav.url, 'alice', '')
        self.dav.write('file.tex', 'x')
        with self.assertRaisesRegex(webdav.DavError, 'address of a file'):
            webdav.connect(self.a, self.dav.url + 'file.tex', 'alice', 's3cret')
        with self.assertRaisesRegex(webdav.DavError, 'not found'):
            webdav.connect(self.a, self.dav.url + 'missing/', 'alice', 's3cret')
        moved = self.dav.url.replace('/dav/files/alice/', '/moved/')
        with self.assertRaisesRegex(webdav.DavError, 'elsewhere.example'):
            webdav.connect(self.a, moved, 'alice', 's3cret')
        self.assertFalse(any('elsewhere' in p for _, p, _ in self.dav.log))

    def test_password_stays_in_memory(self):
        st = self.connect(self.a)
        self.assertTrue(st['connected'])
        self.assertEqual((st['password'], st['needs_password']), ('session', False))
        saved = (self.a / 'build' / '.editor-webdav' / 'state.json').read_text()
        self.assertIn(self.dav.url, saved)
        self.assertNotIn('s3cret', saved)
        webdav.passwords.clear()  # the editor was restarted
        st = webdav.status(self.a)
        self.assertTrue(st['connected'] and st['needs_password'])
        with self.assertRaises(webdav.NeedPassword):
            self.sync(self.a)

    def test_netrc(self):
        rc = Path(self.tmp.name) / 'netrc'
        rc.write_text('machine elsewhere.example login alice password nope\n'
                      'machine 127.0.0.1 login alice password s3cret\n')
        webdav.NETRC = str(rc)
        st = webdav.connect(self.a, self.dav.url, '', '')
        self.assertEqual(st['password'], 'netrc')
        self.assertEqual(self.sync(self.a)['uploaded'], ['figs/plot.png', 'intro.tex', 'main.tex', 'refs.bib'])

    def test_a_refused_password_is_forgotten(self):
        self.connect(self.a)
        self.dav.password = 'changed'
        with self.assertRaises(webdav.NeedPassword):
            self.sync(self.a)
        self.assertTrue(webdav.status(self.a)['needs_password'])

    def test_disconnect(self):
        self.connect(self.a)
        self.sync(self.a)
        self.assertFalse(webdav.disconnect(self.a)['connected'])
        self.assertFalse((self.a / 'build' / '.editor-webdav').exists())
        self.assertTrue((self.a / 'main.tex').exists())


class Digest(DavCase):
    def test_md5(self):
        self.dav.digest = 'MD5'
        self.connect(self.a)
        self.assertEqual(len(self.sync(self.a)['uploaded']), 4)
        auths = [a.split()[0] for _, _, a in self.dav.log]
        # Basic only for the first request; then Digest, also in the next session (the sync), with no new 401
        self.assertEqual(auths[:2], ['Basic', 'Digest'])
        self.assertEqual(set(auths[2:]), {'Digest'})

    def test_sha256_offered_with_basic(self):
        self.dav.digest, self.dav.both = 'SHA-256', True
        self.connect(self.a)
        self.assertEqual(len(self.sync(self.a)['uploaded']), 4)

    def test_an_expired_nonce_is_renewed(self):
        self.dav.digest = 'MD5'
        self.connect(self.a)
        self.dav.nonce = 'nonce-2'
        self.assertEqual(len(self.sync(self.a)['uploaded']), 4)

    def test_wrong_password(self):
        self.dav.digest = 'MD5'
        with self.assertRaises(webdav.NeedPassword):
            self.connect(self.a, 'wrong')
        self.assertEqual(len(self.dav.log), 2)  # Basic, then one Digest answer: no retrying a refused password


class Syncing(DavCase):
    def test_upload_download_and_nothing_to_do(self):
        self.connect(self.a)
        r = self.sync(self.a)
        self.assertEqual(r['uploaded'], ['figs/plot.png', 'intro.tex', 'main.tex', 'refs.bib'])
        self.assertEqual(self.dav.files(), r['uploaded'])  # not build/, not other file types
        self.assertEqual(self.dav.read('figs/plot.png'), b'\x89PNG one')
        self.assertFalse(any(self.sync(self.a).values()))
        self.connect(self.b)
        self.assertEqual(webdav.check(self.b)['plan']['download'], r['uploaded'])
        self.assertEqual(self.sync(self.b)['downloaded'], r['uploaded'])
        self.assertEqual((self.b / 'intro.tex').read_text(), INTRO)
        self.assertFalse(any(self.sync(self.b).values()))
        self.assertFalse(any(webdav.check(self.b)['plan'].values()))

    def test_changes_go_both_ways(self):
        self.both()
        write(self.b, {'intro.tex': INTRO + 'More from B.\n', 'sections/new.tex': 'New section.\n'})
        st = webdav.status(self.b)
        self.assertEqual(st['changed_here'], [{'path': 'intro.tex', 'state': 'changed'},
                                              {'path': 'sections/new.tex', 'state': 'new'}])
        self.assertEqual(self.sync(self.b)['uploaded'], ['intro.tex', 'sections/new.tex'])
        plan = webdav.check(self.a)['plan']
        self.assertEqual(plan['download'], ['intro.tex', 'sections/new.tex'])
        self.assertEqual(self.sync(self.a)['downloaded'], ['intro.tex', 'sections/new.tex'])
        self.assertEqual((self.a / 'sections/new.tex').read_text(), 'New section.\n')
        # deleting goes both ways too; a file deleted here goes to the trash
        (self.a / 'refs.bib').unlink()
        self.assertEqual(self.sync(self.a)['deleted_there'], ['refs.bib'])
        self.assertFalse(self.dav.exists('refs.bib'))
        self.assertEqual(self.sync(self.b)['deleted_here'], ['refs.bib'])
        self.assertFalse((self.b / 'refs.bib').exists())
        self.assertTrue(list((self.b / 'build' / '.editor-trash').glob('*/refs.bib')))

    def test_deleted_on_one_side_changed_on_the_other_is_kept(self):
        self.both()
        (self.a / 'refs.bib').unlink()
        write(self.b, {'refs.bib': '@book{k, title={Changed}}\n'})
        self.sync(self.b)
        self.assertEqual(self.sync(self.a)['downloaded'], ['refs.bib'])
        self.assertIn('Changed', (self.a / 'refs.bib').read_text())
        self.dav.remove('intro.tex')  # deleted on the server; changed here
        write(self.a, {'intro.tex': 'Mine.\n'})
        self.assertEqual(self.sync(self.a)['uploaded'], ['intro.tex'])
        self.assertEqual(self.dav.text('intro.tex'), 'Mine.\n')

    def test_main_document_is_not_deleted(self):
        self.both()
        self.dav.remove('main.tex')
        r = self.sync(self.b)
        self.assertEqual((r['kept'], r['uploaded'], r['deleted_here']), (['main.tex'], ['main.tex'], []))
        self.assertTrue(self.dav.exists('main.tex'))

    def test_never_synced_files_on_both_sides(self):
        write(self.b, {'main.tex': MAIN, 'intro.tex': 'B wrote this.\n'})
        self.connect(self.a)
        self.sync(self.a)
        self.connect(self.b)
        self.assertEqual(webdav.check(self.b)['plan']['compare'], ['intro.tex', 'main.tex'])
        r = self.sync(self.b)
        # main.tex is the same on both sides; intro.tex differs and there is no common version to merge from
        self.assertEqual(r['copies'], [{'path': 'intro.tex', 'copy': 'intro (server copy).tex'}])
        self.assertEqual((self.b / 'intro (server copy).tex').read_text(), INTRO)
        self.assertEqual((self.b / 'intro.tex').read_text(), 'B wrote this.\n')
        # intro.tex is not uploaded while the copy is there, and the copy is never uploaded
        waiting = [{'path': 'intro.tex', 'why': 'server copy', 'copy': 'intro (server copy).tex'}]
        self.assertEqual(self.sync(self.b)['blocked'], waiting)
        self.assertEqual(webdav.status(self.b)['blocked'], waiting)
        self.assertEqual(self.dav.text('intro.tex'), INTRO)
        self.assertNotIn('intro (server copy).tex', self.dav.files())
        (self.b / 'intro (server copy).tex').unlink()
        self.assertEqual(self.sync(self.b)['uploaded'], ['intro.tex'])
        self.assertEqual(self.dav.text('intro.tex'), 'B wrote this.\n')

    def test_images_changed_on_both_sides(self):
        self.both()
        write(self.a, {'figs/plot.png': b'\x89PNG from A'})
        write(self.b, {'figs/plot.png': b'\x89PNG from B'})
        self.sync(self.a)
        r = self.sync(self.b)
        self.assertEqual(r['copies'], [{'path': 'figs/plot.png', 'copy': 'figs/plot (server copy).png'}])
        self.assertEqual((self.b / 'figs/plot (server copy).png').read_bytes(), b'\x89PNG from A')

    def test_latexmkrc_is_held_until_accepted(self):
        self.both()
        self.dav.write('.latexmkrc', '$pdf_mode = 5;\n')
        r = self.sync(self.b)
        self.assertEqual(r['held'], ['.latexmkrc'])
        self.assertFalse((self.b / '.latexmkrc').exists())
        self.assertEqual(webdav.check(self.b)['plan']['held'], ['.latexmkrc'])
        self.assertIn('+$pdf_mode = 5;', webdav.peek(self.b, '.latexmkrc')['diff'])
        r = self.sync(self.b, accept=['.latexmkrc'])
        self.assertEqual(r['downloaded'], ['.latexmkrc'])
        self.assertEqual((self.b / '.latexmkrc').read_text(), '$pdf_mode = 5;\n')
        write(self.b, {'.latexmkrc': '$pdf_mode = 1;\n'})  # changes made here are uploaded as usual
        self.assertEqual(self.sync(self.b)['uploaded'], ['.latexmkrc'])

    def test_changed_on_the_server_during_the_sync(self):
        self.both()
        write(self.a, {'intro.tex': INTRO + 'From A.\n'})
        # a teammate uploads intro.tex between this sync's listing and its upload
        self.dav.hooks['PUT'] = lambda path: path == 'Thesis/intro.tex' and self.dav.write('intro.tex', 'Teammate.\n')
        r = self.sync(self.a)
        self.assertEqual((r['retry'], r['uploaded']), (['intro.tex'], []))
        self.assertEqual(self.dav.text('intro.tex'), 'Teammate.\n')  # not overwritten
        self.dav.hooks.clear()
        self.assertEqual(webdav.check(self.a)['plan']['conflicts'], ['intro.tex'])

    def test_server_without_etags_in_put_replies(self):
        self.dav.put_etag = False
        self.connect(self.a)
        self.assertEqual(len(self.sync(self.a)['uploaded']), 4)
        self.assertFalse(any(self.sync(self.a).values()))

    def test_hostile_server_paths(self):
        p = self.dav.prefix + 'Thesis/'
        self.dav.extra = [(p + '..%2F..%2Fevil.tex', False), (p + '../../evil.tex', False), (p + 'build/x.tex', False),
                          (p + '.git/config.tex', False), (p + 'x.exe', False), ('/elsewhere/y.tex', False),
                          (p + 'c%3A/z.tex', False), (p + '.git/', True), (p + 'build/', True)]
        self.connect(self.b)
        r = self.sync(self.b)
        self.assertFalse(any(r.values()), r)
        self.assertEqual([p.name for p in self.b.iterdir()], ['build'])
        self.assertFalse((Path(self.tmp.name) / 'evil.tex').exists())
        self.assertFalse(any(m == 'GET' for m, _, _ in self.dav.log))

    def test_a_folder_where_the_server_has_a_file_is_skipped(self):
        self.connect(self.a)
        self.sync(self.a)
        (self.b / 'intro.tex').mkdir()
        self.connect(self.b)
        r = self.sync(self.b)
        self.assertEqual([s['path'] for s in r['skipped']], ['intro.tex'])
        self.assertEqual(r['downloaded'], ['figs/plot.png', 'main.tex', 'refs.bib'])

    @unittest.skipUnless(hasattr(Path, 'symlink_to') and sys.platform != 'win32', 'symbolic links')
    def test_symbolic_links_out_of_the_project_are_not_followed(self):
        outside = Path(self.tmp.name) / 'outside'
        outside.mkdir()
        (self.b / 'figs').symlink_to(outside, target_is_directory=True)
        self.dav.write('figs/evil.tex', 'x')
        self.connect(self.b)
        self.assertIn('figs/evil.tex', [s['path'] for s in self.sync(self.b)['skipped']])
        self.assertEqual(list(outside.iterdir()), [])


class Folders(DavCase):
    def test_empty_folders_go_both_ways(self):
        for d in ('figures', 'chapters/drafts', '.vscode'):
            (self.a / d).mkdir(parents=True)
        self.connect(self.a)
        r = self.sync(self.a)
        self.assertEqual([p for p in r['uploaded'] if p.endswith('/')], ['chapters/', 'figures/', 'chapters/drafts/'])
        self.assertEqual(self.dav.folders(), ['chapters', 'chapters/drafts', 'figs', 'figures'])  # not .vscode
        self.connect(self.b)
        self.assertEqual([p for p in self.sync(self.b)['downloaded'] if p.endswith('/')],
                         ['chapters/', 'figures/', 'chapters/drafts/'])
        self.assertTrue((self.b / 'chapters/drafts').is_dir() and (self.b / 'figures').is_dir())
        self.assertFalse(any(self.sync(self.b).values()))
        # deleting an empty folder goes both ways too
        (self.a / 'figures').rmdir()
        self.assertEqual(webdav.status(self.a)['changed_here'], [{'path': 'figures/', 'state': 'deleted'}])
        self.assertEqual(self.sync(self.a)['deleted_there'], ['figures/'])
        self.assertNotIn('figures', self.dav.folders())
        self.assertEqual(webdav.check(self.b)['plan']['delete_here'], ['figures/'])
        self.assertEqual(self.sync(self.b)['deleted_here'], ['figures/'])
        self.assertFalse((self.b / 'figures').exists())
        self.dav.mkdir('appendix')
        self.assertEqual(webdav.check(self.b)['plan']['download'], ['appendix/'])
        self.assertEqual(self.sync(self.b)['downloaded'], ['appendix/'])
        (self.b / 'tables').mkdir()
        self.assertEqual(webdav.status(self.b)['changed_here'], [{'path': 'tables/', 'state': 'new'}])

    def test_a_folder_with_a_teammates_other_files_is_not_deleted_on_the_server(self):
        self.both()
        self.dav.write('figs/minutes.docx', b'notes a teammate keeps there')  # not synced
        (self.a / 'figs/plot.png').unlink()
        (self.a / 'figs').rmdir()
        r = self.sync(self.a)
        self.assertEqual((r['deleted_there'], r['uploaded']), (['figs/plot.png'], []))
        self.assertTrue(self.dav.exists('figs/minutes.docx'))
        # it is left alone from now on: not made here again, nor offered for deletion
        self.assertFalse(any(webdav.check(self.a)['plan'].values()))
        self.assertFalse(any(self.sync(self.a).values()))
        self.assertFalse((self.a / 'figs').exists())

    def test_a_folder_deleted_on_the_server_stays_here_while_it_holds_other_files(self):
        self.both()
        write(self.b, {'figs/sketch.xcf': b'not synced'})
        self.dav.remove('figs')
        r = self.sync(self.b)
        self.assertEqual(r['deleted_here'], ['figs/plot.png'])
        self.assertTrue((self.b / 'figs/sketch.xcf').exists())
        self.assertFalse(any(webdav.check(self.b)['plan'].values()))
        self.assertFalse(any(self.sync(self.b).values()))
        self.assertFalse(self.dav.exists('figs'))  # not made again on the server
        self.assertEqual(webdav.status(self.b)['changed_here'], [])

    def test_a_folder_that_gets_a_file_meanwhile_is_not_deleted(self):
        (self.a / 'empty').mkdir()
        self.both()
        (self.b / 'empty').rmdir()
        # a teammate uploads a file into it just before the DELETE: the folder's ETag no longer matches
        self.dav.hooks['DELETE'] = lambda path: path == 'Thesis/empty' and self.dav.write('empty/new.tex', 'x')
        self.assertEqual(self.sync(self.b)['deleted_there'], [])
        self.assertTrue(self.dav.exists('empty/new.tex'))
        self.dav.hooks.clear()
        self.assertEqual(self.sync(self.b)['downloaded'], ['empty/new.tex'])


class IgnoringCase(DavCase):
    """This computer's file system ignores case (as on Windows and macOS). Forced, so that it runs everywhere."""

    def setUp(self):
        super().setUp()
        self.real = webdav.case_insensitive
        webdav.case_insensitive = lambda root: True

    def tearDown(self):
        webdav.case_insensitive = self.real
        super().tearDown()

    def test_renamed_on_the_server(self):
        self.both()
        self.dav.rename('intro.tex', 'Intro.tex')
        self.assertEqual(webdav.check(self.b)['plan']['rename_here'], [['intro.tex', 'Intro.tex']])
        r = self.sync(self.b)
        self.assertEqual(r['renamed_here'], [['intro.tex', 'Intro.tex']])
        self.assertEqual([r[k] for k in ('downloaded', 'uploaded', 'deleted_here', 'deleted_there')], [[]] * 4)
        self.assertIn('Intro.tex', self.names(self.b))
        self.assertNotIn('intro.tex', self.names(self.b))
        self.assertEqual((self.b / 'Intro.tex').read_text(), INTRO)
        self.assertFalse(any(self.sync(self.b).values()))
        self.assertFalse((self.b / 'build' / '.editor-trash').exists())

    def test_renamed_here(self):
        self.both()
        os.rename(self.b / 'intro.tex', self.b / 'Intro.tex')
        self.assertEqual(webdav.status(self.b)['changed_here'],
                         [{'path': 'Intro.tex', 'state': 'renamed', 'from': 'intro.tex'}])
        self.assertEqual(webdav.check(self.b)['plan']['rename_there'], [['intro.tex', 'Intro.tex']])
        r = self.sync(self.b)
        self.assertEqual((r['renamed_there'], r['uploaded'], r['deleted_there']), ([['intro.tex', 'Intro.tex']], [], []))
        self.assertEqual(self.dav.files(), ['Intro.tex', 'figs/plot.png', 'main.tex', 'refs.bib'])
        self.assertFalse(any(self.sync(self.b).values()))
        self.assertEqual(self.sync(self.a)['renamed_here'], [['intro.tex', 'Intro.tex']])  # and A follows

    def test_folder_renamed_on_the_server(self):
        self.both()
        self.dav.rename('figs', 'Figs')
        r = self.sync(self.b)
        self.assertEqual(r['renamed_here'], [['figs', 'Figs']])
        self.assertEqual((self.b / 'Figs/plot.png').read_bytes(), b'\x89PNG one')
        self.assertIn('Figs', self.names(self.b))
        self.assertFalse(any(self.sync(self.b).values()))
        self.assertEqual(self.dav.folders(), ['Figs'])

    def test_a_new_file_goes_into_the_servers_spelling_of_its_folder(self):
        self.dav.mkdir('Appendix')
        write(self.b, {'main.tex': MAIN, 'appendix/a.tex': 'An appendix.\n'})
        self.connect(self.b)
        r = self.sync(self.b)
        self.assertEqual(r['renamed_here'], [['appendix', 'Appendix']])
        self.assertEqual(r['uploaded'], ['Appendix/a.tex', 'main.tex'])
        self.assertEqual(self.dav.folders(), ['Appendix'])
        self.assertFalse(any(self.sync(self.b).values()))

    def test_names_the_server_has_in_two_spellings_are_left_out(self):
        self.both()
        self.dav.write('Refs.bib', '@book{other}\n')
        r = self.sync(self.b)
        self.assertEqual(r['clashes'], [{'names': ['Refs.bib', 'refs.bib'], 'skipped': True}])
        self.assertFalse(any(v for k, v in r.items() if k != 'clashes'), r)
        self.assertEqual((self.b / 'refs.bib').read_text(), '@book{k, title={T}}\n')
        self.assertEqual(webdav.check(self.b)['plan']['clashes'], r['clashes'])
        self.dav.remove('Refs.bib')  # once the server has one spelling again, it syncs as before
        self.assertFalse(any(self.sync(self.b).values()))

    def test_main_document_renamed_on_the_server(self):
        self.both()
        self.dav.rename('main.tex', 'Main.tex')
        r = self.sync(self.b)
        self.assertEqual((r['renamed_here'], r['kept'], r['uploaded']), ([['main.tex', 'Main.tex']], [], []))
        self.assertEqual(self.dav.files(), ['Main.tex', 'figs/plot.png', 'intro.tex', 'refs.bib'])


@unittest.skipIf(FS_IGNORES_CASE, 'this file system ignores case')
class TellingCaseApart(DavCase):
    def test_names_in_two_spellings_are_synced_with_a_warning(self):
        self.both()
        self.dav.write('Refs.bib', '@book{other}\n')
        r = self.sync(self.b)
        self.assertEqual(r['clashes'], [{'names': ['Refs.bib', 'refs.bib'], 'skipped': False}])
        self.assertEqual(r['downloaded'], ['Refs.bib'])
        self.assertTrue((self.b / 'refs.bib').exists())


@unittest.skipUnless(HAVE_GIT, 'git not installed')
class Merging(DavCase):
    def test_edits_in_different_places_are_merged(self):
        self.both()
        write(self.a, {'intro.tex': INTRO.replace('Line 2 ', 'Line TWO ')})
        write(self.b, {'intro.tex': INTRO.replace('Line 19 ', 'Line NINETEEN ')})
        self.sync(self.a)
        r = self.sync(self.b)
        self.assertEqual(r['merged'], ['intro.tex'])
        merged = (self.b / 'intro.tex').read_text()
        self.assertIn('Line TWO ', merged)
        self.assertIn('Line NINETEEN ', merged)
        self.assertEqual(self.dav.text('intro.tex'), merged)
        self.assertEqual(self.sync(self.a)['downloaded'], ['intro.tex'])
        self.assertEqual((self.a / 'intro.tex').read_text(), merged)

    def test_overlapping_edits_get_conflict_markers(self):
        self.both()
        write(self.a, {'intro.tex': INTRO.replace('Line 5 ', 'Line five (A) ')})
        write(self.b, {'intro.tex': INTRO.replace('Line 5 ', 'Line five (B) ')})
        self.sync(self.a)
        r = self.sync(self.b)
        self.assertEqual(r['conflicts'], ['intro.tex'])
        text = (self.b / 'intro.tex').read_text()
        self.assertIn('<<<<<<< this computer\nLine five (B) ', text)
        self.assertIn('>>>>>>> server\n', text)
        self.assertNotIn('<<<<<<<', self.dav.text('intro.tex'))
        # not uploaded while the markers are there
        self.assertEqual(self.sync(self.b)['blocked'], [{'path': 'intro.tex', 'why': 'conflict markers'}])
        write(self.b, {'intro.tex': INTRO.replace('Line 5 ', 'Line five (A and B) ')})
        self.assertEqual(self.sync(self.b)['uploaded'], ['intro.tex'])
        self.sync(self.a)
        self.assertIn('Line five (A and B) ', (self.a / 'intro.tex').read_text())


class Clone(DavCase):
    def test_clone_asks_for_the_password(self):
        self.connect(self.a)
        self.sync(self.a)
        target = Path(self.tmp.name) / 'new' / 'thesis'
        asked = []
        out = webdav.clone(target, self.dav.url, 'alice', lambda prompt: asked.append(prompt) or 's3cret')
        self.assertEqual(asked, ['Password for alice on 127.0.0.1: '])
        self.assertIn('4 downloaded', out)
        self.assertEqual((target / 'main.tex').read_text(), MAIN)
        with self.assertRaisesRegex(webdav.DavError, '--webdav-user'):
            webdav.clone(Path(self.tmp.name) / 'other', self.dav.url, '', lambda prompt: 'x')


class WebDavApi(ServerCase):
    files = {'main.tex': MAIN, 'intro.tex': INTRO}

    def setUp(self):
        super().setUp()
        webdav.passwords.clear()
        webdav.challenges.clear()
        self.dav = FakeDav()

    def tearDown(self):
        self.dav.close()
        webdav.passwords.clear()
        webdav.challenges.clear()
        super().tearDown()

    def test_connect_check_sync(self):
        code, st, _ = self.get('/api/webdav/status')
        self.assertEqual((code, st['connected']), (200, False))
        code, r, _ = self.post('/api/webdav/connect', {'url': 'http://cloud.example.org/x/', 'user': 'a', 'password': 'b'})
        self.assertEqual(code, 400)
        self.assertIn('https', r['error'])
        code, r, _ = self.post('/api/webdav/connect', {'url': self.dav.url, 'user': 'alice', 'password': 'bad'})
        self.assertEqual((code, r.get('needs_password')), (400, True))
        code, st, _ = self.post('/api/webdav/connect', {'url': self.dav.url, 'user': 'alice', 'password': 's3cret'})
        self.assertEqual((code, st['connected'], st['user']), (200, True, 'alice'))
        code, r, _ = self.post('/api/webdav/check')
        self.assertEqual(r['plan']['upload'], ['intro.tex', 'main.tex'])
        code, r, _ = self.post('/api/webdav/sync')
        self.assertEqual((code, r['result']['uploaded'], r['changed_here']), (200, ['intro.tex', 'main.tex'], []))
        self.assertEqual(self.post('/api/webdav/peek', {'path': '../x.tex'})[0], 400)
        self.assertEqual(self.post('/api/webdav/nope')[0], 404)
        webdav.passwords.clear()
        code, r, _ = self.post('/api/webdav/sync')
        self.assertEqual((code, r.get('needs_password')), (400, True))
        self.assertTrue(self.get('/api/webdav/status')[1]['needs_password'])
        code, st, _ = self.post('/api/webdav/disconnect')
        self.assertEqual((code, st['connected']), (200, False))

    def test_main_document_renamed_by_a_sync_is_still_compiled(self):
        real = webdav.case_insensitive
        webdav.case_insensitive = lambda root: True
        try:
            self.post('/api/webdav/connect', {'url': self.dav.url, 'user': 'alice', 'password': 's3cret'})
            self.post('/api/webdav/sync')
            self.dav.rename('main.tex', 'Main.tex')
            code, r, _ = self.post('/api/webdav/sync')
            self.assertEqual((code, r['main'], server.MAIN), (200, 'Main.tex', 'Main.tex'))
        finally:
            webdav.case_insensitive = real

    def test_operations_need_a_json_post(self):
        self.assertEqual(self.get('/api/webdav/sync')[0], 404)
        code, _, _ = self.request('POST', '/api/webdav/connect', body=json.dumps({'url': self.dav.url}),
                                  headers={'Content-Type': 'text/plain'})
        self.assertEqual(code, 403)


if __name__ == '__main__':
    unittest.main()
