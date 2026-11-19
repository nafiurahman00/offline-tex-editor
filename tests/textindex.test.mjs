// Tests for the PDF find index (offline_tex_editor/static/textindex.js). Run with: node --test tests/
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { normalize, indexText, findAll, matchSpans } from '../offline_tex_editor/static/textindex.js';

const items = (...rows) => rows.map(([str, hasEOL = false]) => ({ str, hasEOL }));

test('case, ligatures and white space are normalized', () => {
  const { text } = indexText(items(['The ﬁrst   EFFICIENT', true], ['oﬄine run.']));
  assert.equal(text, 'the first efficient offline run.');
  assert.equal(normalize('  Two\tWords '), ' two words ');
});

test('a word hyphenated across lines matches whole', () => {
  const { text } = indexText(items(['an exam-', true], ['ple here']));
  assert.equal(text, 'an example here');
  assert.deepEqual(findAll(text, 'example'), [{ start: 3, end: 10 }]);
});

test('a hyphen after a non-letter is kept', () => {
  assert.equal(indexText(items(['pages 1-', true], ['5'])).text, 'pages 1- 5');
});

test('matches map back to the characters of each item', () => {
  const { text, map } = indexText(items(['interna-', true], ['tionalization rocks']));
  const [m] = findAll(text, 'internationalization');
  assert.deepEqual([...matchSpans(map, m.start, m.end)], [[0, [0, 7]], [1, [0, 13]]]);
});

test('a ligature maps to its one source character', () => {
  const { text, map } = indexText(items(['eﬃcient']));
  const [m] = findAll(text, 'ffi');
  assert.deepEqual([...matchSpans(map, m.start, m.end)], [[0, [1, 2]]]);
});

test('math italic letters (unicode-math) match plain letters', () => {
  assert.deepEqual(findAll(indexText(items(['let 𝑥 = 1'])).text, 'x ='), [{ start: 4, end: 7 }]);
});

test('findAll finds every non-overlapping match, and nothing for an empty query', () => {
  assert.deepEqual(findAll('aaaa', 'aa'), [{ start: 0, end: 2 }, { start: 2, end: 4 }]);
  assert.deepEqual(findAll('abc', ''), []);
});
