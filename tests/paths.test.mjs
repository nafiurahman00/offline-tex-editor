// Tests for file names as the main document sees them (offline_tex_editor/static/paths.js). Run with:
// node --test tests/
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fromMain } from '../offline_tex_editor/static/paths.js';

test('a main document at the top sees project paths as they are', () => {
  assert.equal(fromMain('figures/plot.png', 'main.tex'), 'figures/plot.png');
  assert.equal(fromMain('refs.bib', 'main.tex'), 'refs.bib');
});

test('files below the main document\'s folder are relative to it', () => {
  assert.equal(fromMain('paper/figures/plot.png', 'paper/main.tex'), 'figures/plot.png');
  assert.equal(fromMain('paper/refs.bib', 'paper/main.tex'), 'refs.bib');
  assert.equal(fromMain('a/b/sections/intro.tex', 'a/b/main.tex'), 'sections/intro.tex');
});

test('files elsewhere in the project get ../', () => {
  assert.equal(fromMain('notes.tex', 'paper/main.tex'), '../notes.tex');
  assert.equal(fromMain('shared/refs.bib', 'paper/main.tex'), '../shared/refs.bib');
  assert.equal(fromMain('a/c/x.tex', 'a/b/main.tex'), '../c/x.tex');
  assert.equal(fromMain('x.tex', 'a/b/main.tex'), '../../x.tex');
});

test('a file named like the main document\'s folder is not taken for that folder', () => {
  assert.equal(fromMain('paper', 'paper/main.tex'), '../paper');
  assert.equal(fromMain('paperx/a.tex', 'paper/main.tex'), '../paperx/a.tex');
});
