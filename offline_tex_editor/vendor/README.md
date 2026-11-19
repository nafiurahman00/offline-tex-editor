# Vendored libraries

Bundled so the editor works fully offline. Files are unmodified copies from cdnjs; licenses from the npm packages.

| Library | Version | License | Source |
|---|---|---|---|
| CodeMirror | 5.65.16 | MIT (`codemirror/LICENSE`) | https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/ |
| pdf.js | 4.10.38 | Apache-2.0 (`pdfjs/LICENSE`) | https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.10.38/ (`pdf.min.mjs`, `pdf.worker.min.mjs`); identical to `build/` in the npm package `pdfjs-dist@4.10.38` |

To update, download the same file list at the new version, check it against the npm package, and replace this table and the checksums below (`sha256sum` from this folder; CI checks them). Before choosing a pdf.js version, check its security advisories (github.com/mozilla/pdf.js/security/advisories): 4.10.38 is outside the ranges of CVE-2024-4367 (<= 4.1.392) and CVE-2026-16633 (5.6.83 to 6.2.107).

## SHA-256

```
a69862c96bb9201693e29c78d041f9085a79785cafaf80a9cb3d228c6f99f828  ./codemirror/addon/dialog/dialog.min.css
b11516c9c96f4d593f4427d4d70cddcb3520ebe6c19048cd62db641250ea2dc4  ./codemirror/addon/dialog/dialog.min.js
f359c5f395a2a08191557d40b61ef8c3893803da9019171cc44b8d9c8c7d4a3a  ./codemirror/addon/edit/matchbrackets.min.js
98d3a590d2d60037ead413dc70aae17b05da6bde62528d2365a540fde4852887  ./codemirror/addon/search/jump-to-line.min.js
0b5f19d72d5febca9b2bb3d471489eadd92ff2c18d29fef5335bbb536cba6692  ./codemirror/addon/search/searchcursor.min.js
bbca0b4ba03a94163a1eaa604b45ef038c12d8572b7cc16a41dd287b40cc998c  ./codemirror/addon/selection/active-line.min.js
11077112ab6955d29fe41085c62365c7d4a2f00a570c7475e2aec2a8cbc85fc4  ./codemirror/codemirror.min.css
f102bb61fb2ac45c3a611847edd9948faad0ab22a83a2dfa9974be5031079b15  ./codemirror/codemirror.min.js
6d31310a4719d151d198b604864fa7cb7dcaa5013888863585e06d7c7085f3d8  ./codemirror/mode/markdown/markdown.min.js
6d19a4ba8b05a354935ceebf490582faffa047c86c4715a2b504b14319eb6399  ./codemirror/mode/python/python.min.js
ebc5cb8e191cf20207a2477db7dc4ffb08329e9e40d3487b9113ae1c68eafa53  ./codemirror/mode/stex/stex.min.js
27fc2a057a00f92a4334ad06e17dbd7259912954e9fb7f76400bcca5fd190a9c  ./pdfjs/pdf.min.mjs
1baa1844c89c80a5b2797c916e75ab29254be46d8e9cb53cb6364d7aad84be36  ./pdfjs/pdf.worker.min.mjs
```
