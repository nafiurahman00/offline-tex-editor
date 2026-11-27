// Tests for selecting several files in the file list (offline_tex_editor/static/selection.js). Run with:
// node --test tests/
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { rangeBetween, topmost, forDeleting } from '../offline_tex_editor/static/selection.js';

const order = ['main.tex', 'refs.bib', 'figures', 'figures/a.png', 'figures/b.png', 'sections', 'sections/intro.tex'];

test('Shift+click selects everything shown between the two rows, in either direction', () => {
  assert.deepEqual(rangeBetween(order, 'refs.bib', 'figures/a.png'), ['refs.bib', 'figures', 'figures/a.png']);
  assert.deepEqual(rangeBetween(order, 'figures/b.png', 'main.tex'),
                   ['main.tex', 'refs.bib', 'figures', 'figures/a.png', 'figures/b.png']);
  assert.deepEqual(rangeBetween(order, 'sections', 'sections'), ['sections']);
});

test('without a row to start from, Shift+click selects the clicked row', () => {
  assert.deepEqual(rangeBetween(order, null, 'refs.bib'), ['refs.bib']);
  assert.deepEqual(rangeBetween(order, 'gone.tex', 'refs.bib'), ['refs.bib']);
  assert.deepEqual(rangeBetween(order, 'main.tex', 'gone.tex'), []);
});

test('files in a selected folder go with the folder', () => {
  assert.deepEqual(topmost(['figures', 'figures/a.png', 'refs.bib', 'figures/sub/x.png']), ['figures', 'refs.bib']);
  assert.deepEqual(topmost(['figures/a.png', 'figures/b.png']), ['figures/a.png', 'figures/b.png']);
  // a name that only starts like the folder is not inside it
  assert.deepEqual(topmost(['fig', 'figures/a.png']), ['fig', 'figures/a.png']);
});

test('deleting keeps the main document and its folders, but not what is selected in them', () => {
  assert.deepEqual(forDeleting(['paper', 'paper/sec/s.tex', 'notes.tex'], 'paper/main.tex'),
                   { remove: ['paper/sec/s.tex', 'notes.tex'], keep: ['paper'] });
  assert.deepEqual(forDeleting(['a', 'a/b', 'a/b/main.tex', 'a/b/c', 'a/b/c/x.tex', 'a/y.tex'], 'a/b/main.tex'),
                   { remove: ['a/b/c', 'a/y.tex'], keep: ['a', 'a/b', 'a/b/main.tex'] });
  assert.deepEqual(forDeleting(['paperx', 'pap'], 'paper/main.tex'), { remove: ['paperx', 'pap'], keep: [] });
});
