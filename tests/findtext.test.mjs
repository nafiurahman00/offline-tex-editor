// Tests for find and replace in the editor (offline_tex_editor/static/findtext.js). Run with: node --test tests/*.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildRegExp, lineMatches, expandReplacement } from '../offline_tex_editor/static/findtext.js';

const find = (q, line, o) => lineMatches(buildRegExp(q, o), line).map(x => line.slice(x.ch, x.ch + x.len));

test('plain text is matched literally, case-insensitive by default', () => {
  assert.deepEqual(find('a.b', 'a.b axb A.B'), ['a.b', 'A.B']);
  assert.deepEqual(find('a.b', 'a.b axb A.B', { caseSensitive: true }), ['a.b']);
  assert.deepEqual(find('\\section{', '\\section{One} \\subsection{Two}'), ['\\section{']);
});

test('whole words, also next to non-ASCII letters', () => {
  assert.deepEqual(find('the', 'the other theory, the', { wholeWord: true }), ['the', 'the']);
  assert.deepEqual(find('caf', 'café caf', { wholeWord: true }), ['caf']);
});

test('regular expressions, with invalid ones reported', () => {
  assert.deepEqual(find('\\\\(?:sub)?section', '\\section \\subsection \\paragraph', { regex: true }),
                   ['\\section', '\\subsection']);
  assert.equal(typeof buildRegExp('(', { regex: true }).error, 'string');
  assert.equal(buildRegExp(''), null);
});

test('empty matches are skipped instead of looping', () => {
  assert.deepEqual(find('x*', 'ab xx c', { regex: true }), ['xx']);
  assert.deepEqual(find('^', 'abc', { regex: true }), []);
});

test('replacements: $1, $&, $<name>, $$; backslashes literal', () => {
  const m = /(\w+) (?<last>\w+)/.exec('Donald Knuth');
  assert.equal(expandReplacement('\\textbf{$1}', m), '\\textbf{Donald}');
  assert.equal(expandReplacement('$2, $1', m), 'Knuth, Donald');
  assert.equal(expandReplacement('[$&]', m), '[Donald Knuth]');
  assert.equal(expandReplacement('$<last>', m), 'Knuth');
  assert.equal(expandReplacement('$$1', m), '$1');
  assert.equal(expandReplacement('$10', m), 'Donald0');
  assert.equal(expandReplacement('$3 $<nope>', m), '$3 $<nope>');
});

test('the editor and the server expand replacements the same way', () => {
  // Same table as tests/test_server.py (Structure.test_replacement_rules)
  const m = /(\w+) (?<last>\w+)/.exec('Donald Knuth');
  const cases = { '\\textbf{$1}': '\\textbf{Donald}', '$2, $1': 'Knuth, Donald', '[$&]': '[Donald Knuth]',
    '$<last>': 'Knuth', '$$1': '$1', '$10': 'Donald0', '$3': '$3', '$<nope>': '$<nope>', 'no groups': 'no groups' };
  for (const [t, want] of Object.entries(cases)) assert.equal(expandReplacement(t, m), want, t);
});
