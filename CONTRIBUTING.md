# Contributing

Thank you for helping with Offline TeX Editor. Ideas, bug reports and questions go in the
[issue tracker](https://github.com/offline-tex-editor/offline-tex-editor/issues); security problems are reported
privately, as [SECURITY.md](SECURITY.md) explains. This file is about changing the code.

[README.md](README.md) is the user documentation; it is also the PyPI page and the website, so keep it written for
people who use the editor.

## Pull requests

- For anything larger than a small fix, open an issue first, so that we can agree on the approach before you
  spend time on it.
- Follow the design constraints below.
- Add or update tests, and run them before you push. CI runs them on Linux (Python 3.9 and 3.13) and Windows.
- Keep each pull request to one change, and describe what it changes for the people who use the editor.
- Contributions are released under the project's [MIT License](LICENSE).

## Design constraints

These keep the editor easy to install and audit:

- **Standard library only** for Python. No runtime dependencies.
- **No build step** for the front end: `index.html`, `static/app.css` and the ES modules in `static/` are served as
  they are. No inline scripts (the page's Content-Security-Policy forbids them).
- **Fully offline.** No CDN, fonts, analytics or any other network access from the page. Third-party code goes in
  `offline_tex_editor/vendor/`, unmodified, with its license, version and SHA-256 recorded in
  [vendor/README.md](offline_tex_editor/vendor/README.md).
- **Local and single-user.** Keep the request checks in `Handler.refused()`, the path checks in `safe()`,
  `editable()` and `fs_target()` (server.py) and `inside()` (gitapi.py). Run external commands with argument
  lists, never a shell.

## Running and testing

```bash
./start.sh --root example        # run the editor from this checkout (Windows: .\start.cmd)
python3 -m unittest discover -s tests -v
node --test tests/*.test.mjs
```

The Python tests start the real server on a free port against temporary projects. Tests that need `git`,
`latexmk`/`synctex`, `xelatex`, `biber` or `texcount` are skipped when those are missing; CI installs them all.

Layout:

- `offline_tex_editor/server.py`: HTTP server, JSON API, compiling, SyncTeX, file operations
- `offline_tex_editor/project.py`: reading the sources (main document, outline, labels, bibliography, search)
- `offline_tex_editor/latexlog.py`: LaTeX, BibTeX and biber logs, and the error hints
- `offline_tex_editor/gitapi.py`: git operations (argument lists only, no shell; paths confined to the repository)
- `offline_tex_editor/index.html`, `static/`: the page and its ES modules. Each module sets up its part of the page
  and registers its public functions on the shared `app` object from `util.js` (`app.openFile`, `app.compile`, …);
  modules call each other through `app`. Pure logic (like `textindex.js`) is tested with `node --test`.
- `offline_tex_editor/vendor/`: CodeMirror 5.65.16 and pdf.js 4.10.38
- `example/`: a small project to try the editor on; also used by the tests
- `site/`: the public website (see below)

## Releasing to PyPI (maintainers)

Only the wheel is published: it contains the `offline_tex_editor` package and its license files. The rest (tests,
the example, CI, the website) is here on GitHub.

PyPI accepts uploads from this repository's `release` workflow through trusted publishing, so no token is stored
anywhere. The publisher is set on pypi.org (the `offline-tex-editor` project → Manage → Publishing → GitHub):
owner `offline-tex-editor`, repository `offline-tex-editor`, workflow `release.yml`, environment name left empty.

For each release:

1. Update `__version__` in `offline_tex_editor/__init__.py` and commit.
2. Tag and push: `git tag v0.2.0 && git push origin v0.2.0`. The `release` workflow checks that the tag matches the
   version, builds the wheel and uploads it.
3. Rebuild and publish the website (below), so its version number matches.

To build the wheel by hand instead: `python3 -m pip wheel --no-deps -w dist .`, then upload `dist/*.whl` with
`twine upload`.

## The website (maintainers)

The website is generated from README.md by `site/build.py` (standard library only) and adds a contact section and
a suggestion box. The suggestion box is a plain form that opens a pre-filled issue in this repository, using the
issue forms in `.github/ISSUE_TEMPLATE/`.

Its settings are in `site/config.json`: this repository, the website's repository, the site address, the
maintainer, and an optional contact email (shown on the page when set). If you change the site address or this
repository's name, also update the links in README.md and `pyproject.toml`; `tests/test_site.py` checks that they
agree.

To publish (the website's repository, offline-tex-editor/offline-tex-editor.github.io, holds only the generated
files):

```bash
git clone https://github.com/offline-tex-editor/offline-tex-editor.github.io.git ../offline-tex-editor.github.io
python3 site/build.py --out ../offline-tex-editor.github.io
cd ../offline-tex-editor.github.io && git add -A && git commit -m "Update the website" && git push
```

That repository serves GitHub Pages from its `main` branch at https://offline-tex-editor.github.io/. Its issues and
security reports are redirected here. To preview locally: `python3 site/build.py` and open
`site/_build/index.html`, or serve it with `python3 -m http.server --directory site/_build`.
