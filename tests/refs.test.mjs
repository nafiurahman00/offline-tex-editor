// Tests for finding the label argument of a cross-reference (offline_tex_editor/static/refs.js). Run with:
// node --test tests/
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { refArgument } from '../offline_tex_editor/static/refs.js';

test('reference commands want a label in braces', () => {
  for (const cmd of ['ref', 'eqref', 'pageref', 'autoref', 'nameref', 'cref', 'Cref', 'vref', 'labelcref', 'cpageref'])
    assert.deepEqual(refArgument(`see \\${cmd}{sec:`), { typed: 'sec:', close: '}' }, cmd);
  assert.deepEqual(refArgument('\\cref*{'), { typed: '', close: '}' });
  assert.deepEqual(refArgument('\\cref{a,b'), { typed: 'a,b', close: '}' });
});

test('\\href and \\hyperref{URL} take a web address, not a label', () => {
  assert.equal(refArgument('\\href{'), null);
  assert.equal(refArgument('\\href{https://example.org'), null);
  assert.equal(refArgument('\\hyperref{'), null);
});

test('\\hyperref[label] wants a label in square brackets', () => {
  assert.deepEqual(refArgument('\\hyperref[fig:'), { typed: 'fig:', close: ']' });
  assert.equal(refArgument('\\hyperref[fig:plot]{the '), null);
});

test('no label wanted outside the argument', () => {
  assert.equal(refArgument('\\ref{sec:a} and '), null);
  assert.equal(refArgument('\\label{'), null);
  assert.equal(refArgument('plain text'), null);
});
