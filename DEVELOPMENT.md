# Development

Notes for working on Offline TeX Editor. This repository is private; users get the editor from PyPI
(`pip install offline-tex-editor`) and read [README.md](README.md), which is also the PyPI page and the website.

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
./start.sh --root example        # run the editor from this checkout (Windows: start.cmd)
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

## Releasing to PyPI

Only the wheel is published: it contains the `offline_tex_editor` package and its license files, not the tests,
the example, CI or the git history. (The package's Python and JavaScript files are readable by anyone who installs
it, as with any pure-Python package.)

One-time setup on PyPI: create the `offline-tex-editor` project with a trusted publisher (pypi.org → Your account →
Publishing → Add a new pending publisher → GitHub): project name `offline-tex-editor`, owner `KSMubasshir`,
repository `offline-tex-editor`, workflow `release.yml`, environment name left empty.

For each release:

1. Update `__version__` in `offline_tex_editor/__init__.py` and commit.
2. Tag and push: `git tag v0.2.0 && git push origin v0.2.0`. The `release` workflow checks that the tag matches the
   version, builds the wheel and uploads it.
3. Rebuild and publish the website (below), so its version number matches.

To build the wheel by hand instead: `python3 -m pip wheel --no-deps -w dist .`, then upload `dist/*.whl` with
`twine upload`.

## The website

The website is generated from README.md by `site/build.py` (standard library only) and adds a contact section and
a suggestion box. The suggestion box is a plain form that opens a pre-filled issue on the public repository, using
the issue forms in `site/public/.github/ISSUE_TEMPLATE/`.

Its settings are in `site/config.json`: the public repository, the site address, the maintainer, and an optional
contact email (shown on the page when set). If you change the site address or the public repository, also update
the links in README.md and `pyproject.toml`; `tests/test_site.py` checks that they agree.

To publish (the public repository, offline-tex-editor/offline-tex-editor.github.io in the offline-tex-editor
organization, holds only the generated files):

```bash
git clone https://github.com/offline-tex-editor/offline-tex-editor.github.io.git ../offline-tex-editor.github.io
python3 site/build.py --out ../offline-tex-editor.github.io
cd ../offline-tex-editor.github.io && git add -A && git commit -m "Update the website" && git push
```

That repository serves GitHub Pages from its `main` branch at https://offline-tex-editor.github.io/, with private
vulnerability reporting on. It lives in an organization on purpose: project sites in a personal account are served
under that account's custom domain, if it has one. To preview locally: `python3 site/build.py` and open
`site/_build/index.html`, or serve it with `python3 -m http.server --directory site/_build`.
