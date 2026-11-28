# Integrating Offline TeX Grammar

`offline-tex-grammar` is an optional, separately maintained private companion. It adds grammar checks to the
editor without adding a required dependency to the editor. You need access to
[`offline-tex-editor/offline-tex-grammar`](https://github.com/offline-tex-editor/offline-tex-grammar) to clone it.
The editor's repository and installation do not include the companion's source or LanguageTool binaries.

## Install both checkouts in one environment

Use the editor checkout containing the `--grammar` option. Older installed releases may not recognize that
option; install the current checkout as shown below. The checkouts can be in any directories when both are
installed in the same Python environment.

From a parent directory, clone both repositories using your configured GitHub credentials:

```bash
git clone https://github.com/offline-tex-editor/offline-tex-editor.git
git clone https://github.com/offline-tex-editor/offline-tex-grammar.git
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ./offline-tex-editor -e ./offline-tex-grammar
offline-tex-editor --grammar --root /path/to/your/project --open
```

For existing checkouts, activate a virtual environment and install their actual paths with the same
`python -m pip install -e ... -e ...` command. Editable installation makes code updates available after a
server restart. The editor and companion must be installed for the interpreter that launches the server.
An installation in another virtual environment or pipx environment is not visible to that interpreter.

On Windows, use PowerShell from the same parent directory:

```powershell
git clone https://github.com/offline-tex-editor/offline-tex-editor.git
git clone https://github.com/offline-tex-editor/offline-tex-grammar.git
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .\offline-tex-editor -e .\offline-tex-grammar
offline-tex-editor --grammar --root C:\path\to\your\project --open
```

If environment activation is unavailable, call `.venv/bin/python -m offline_tex_editor` on Linux/macOS,
or `.\.venv\Scripts\python.exe -m offline_tex_editor` on Windows, with the same arguments.

## Run directly from neighboring checkouts

For local development, the directory layout can be:

```text
Projects/
  offline-tex-editor/
  offline-tex-grammar/
```

From `offline-tex-editor`, run:

```bash
./start.sh --grammar --root /path/to/your/project --open
```

On Windows, run `.\start.cmd --grammar --root C:\path\to\your\project --open` instead.
The editor first looks for an installed `offline_tex_grammar` package. If it is absent, it loads the neighboring
checkout. Installing the companion in the launching interpreter takes precedence over the neighboring copy.

The companion also provides `offline-tex-editor-grammar`, which launches the same editor with grammar enabled.
Choose either that command or `offline-tex-editor --grammar`; the plain `offline-tex-editor` command keeps
grammar disabled. The integration targets Offline TeX Editor 0.3.x.

## Enable the fuller offline engine

The companion's starter engine has 14 rules and needs only Python. For broader checks, install a Java runtime
compatible with LanguageTool 6.6 (`java -version` should work; Java 21 was used for local verification).
Then, in the shared Python environment, run:

```bash
python -m offline_tex_grammar.setup
```

For neighboring checkouts without installation, run that command from `offline-tex-grammar` instead.
Setup downloads a pinned, SHA-256-verified LanguageTool 6.6 archive from languagetool.org, about 240 MiB,
and installs it under `~/.cache/offline-tex-grammar/LanguageTool-6.6` on all platforms. It preserves the
distribution's own license files. An existing distribution can be selected through
`OFFLINE_TEX_LANGUAGETOOL_HOME`, pointing to the directory containing `languagetool-server.jar`.

Restart the editor after setup. The first check starts a private localhost Java process; later checks reuse it.
The process stops when the editor exits. Grammar checks do not download anything, call a cloud API, or need
API keys. The browser calls the editor's existing JSON API, and only the Python adapter calls LanguageTool.
No separate public port or CORS configuration is needed. The local engine does not include cloud AI features;
see the [LanguageTool server documentation](https://dev.languagetool.org/http-server.html).

## Review suggestions

1. Open the editor at the URL printed by the server. With multiple projects running, this may be a port other
   than 8765; use `--port NUMBER` if you want a fixed port.
2. Open the **A with a checkmark** icon in the left sidebar. The panel identifies the file or project, word
   count, and engine: **LanguageTool (offline)** or **14 starter rules**.
3. Use **Check now** for the current buffer or **Check project** for `.tex`/`.ltx` files. A main file containing
   `\input`/`\include` and no direct issues automatically follows up with a project check.
4. Open a project result to see its source highlights and individual fixes. Fixes use the editor's normal
   undo history and stay unsaved until you save the file.
5. Open **Files** to see amber grammar icons beside affected files. Hover for the count or click to review.

Project checks use saved files plus the current unsaved buffer. Other unsaved tabs are reviewed when opened.
The companion skips common LaTeX syntax, math, code, and unknown macro arguments; it does not expand TeX macros.
Suggestions are recommendations, and some wording or punctuation choices may be intentional.

File markers describe the latest checked snapshots. They clear when those buffers change and survive file
filtering and folder expansion. Results are held in the browser session; after a page reload, check again to
repopulate them. **Ignore rule** is saved per project, and **Reset ignored rules** restores those rules.

## Verify the connection

```bash
offline-tex-editor --help
python -c "import offline_tex_grammar; print(offline_tex_grammar.__file__)"
```

The help output should contain `--grammar`, and the import should identify the intended companion checkout.
For neighboring checkouts without installation, run the import command from the grammar directory.

With the editor running, this synthetic check should return JSON diagnostics:

```bash
curl -s -H 'Content-Type: application/json' \
  -d '{"text":"The results shows a significant improvement."}' \
  http://127.0.0.1:8765/api/grammar/check
```

Substitute the server's actual port. Broader agreement checking needs the local LanguageTool engine; the
response identifies it through `engine: "languagetool"`. The starter engine reports `engine: "builtin"`.

## Update and troubleshoot

Pull updates in both repositories, restart the grammar-enabled server, and reload its browser tab. Save any
unsaved edits before reloading. A browser refresh alone does not reload Python adapter code in a running server.

| Symptom | Check |
|---|---|
| No grammar icon | Confirm `--grammar` is in the running server's command, use its printed URL, restart it after updates, then hard-refresh the browser. |
| `unrecognized arguments: --grammar` | The command points at an older editor installation. Install this checkout in the launching environment, or run its `start.sh`/`start.cmd`. |
| Companion package not found | Install both packages in the same environment or use the neighboring directory layout. |
| Only 14 starter rules | Verify `java -version`, run setup, check the engine directory/environment variable, and restart the editor. |
| No suggestions in `main.tex` | It may mostly contain includes. Run **Check project**, or open a prose section; zero suggestions do not guarantee error-free writing. |
| No file markers after reload | Run a check again, then open **Files**. Expand folders to see marked files. |
| Local engine startup/check error | Inspect `~/.cache/offline-tex-grammar/server-PID.log`, where `PID` is the editor process ID. |

Omit `--grammar` to return to the editor alone. The companion does not modify project files or the editor's
installed source when it starts, and disabling it does not remove anything from a project.
