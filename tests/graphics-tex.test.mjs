// Check that generated edits are valid input to TeX, not just the expected JavaScript strings.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, writeFileSync, copyFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { texSafeName, includeGraphics } from '../offline_tex_editor/static/paths.js';

const haveTex = spawnSync('pdflatex', ['--version']).status === 0;
function compile(dir, name, body) {
  writeFileSync(join(dir, name + '.tex'), '\\documentclass{article}\n\\usepackage{graphicx}\n' +
    '\\begin{document}\n' + body + '\n\\end{document}\n');
  const r = spawnSync('pdflatex', ['-interaction=nonstopmode', '-halt-on-error', name + '.tex'],
    { cwd: dir, encoding: 'utf8', timeout: 30000 });
  assert.equal(r.status, 0, r.error?.message || r.stdout + r.stderr);
}

test('an uploaded PDF name with TeX caret conversion can be included', { skip: !haveTex }, () => {
  const dir = mkdtempSync(join(tmpdir(), 'ote-graphics-'));
  try {
    compile(dir, 'figure', 'A figure');
    const name = texSafeName('a^^41.pdf');
    copyFileSync(join(dir, 'figure.pdf'), join(dir, name));
    compile(dir, 'main', includeGraphics(name, 'main.tex'));
  } finally { rmSync(dir, { recursive: true, force: true }); }
});
