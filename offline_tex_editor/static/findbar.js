// Find and replace in the open file: a bar above the editor with case, whole-word and regular-expression options,
// a match count, every match highlighted, replace one, and replace all (one undo step).
import { $, app, state, setStatus, debounce } from './util.js';
import { buildRegExp, lineMatches, expandReplacement } from './findtext.js';

const cm = app.cm;
const LIMIT = 50000;
let rx = null, matches = [], overlay = null, curMark = null, cur = -1, start = null;

const opts = () => ({ regex: $('fbRegex').checked, caseSensitive: $('fbCase').checked, wholeWord: $('fbWord').checked });
const isOpen = () => !$('findBar').hidden;
const pos = (line, ch) => ({ line, ch });
const before = (a, b) => CodeMirror.cmpPos(a, b) < 0;
const fresh = () => new RegExp(rx.source, rx.flags);   // own lastIndex, so searches don't disturb each other

// Find every match and highlight them.
function compute() {
  if (overlay) { cm.removeOverlay(overlay); overlay = null; }
  const q = buildRegExp($('fbFind').value, opts());
  rx = q && !q.error ? q : null;
  $('fbFind').classList.toggle('bad', !!(q && q.error));
  $('fbFind').title = q && q.error ? `Not a valid regular expression: ${q.error}` : '';
  matches = [];
  if (rx) {
    const r = fresh();
    for (let l = 0, n = cm.lineCount(); l < n && matches.length < LIMIT; l++)
      for (const x of lineMatches(r, cm.getLine(l))) matches.push({ line: l, ch: x.ch, len: x.len });
    const o = fresh();
    overlay = { token(stream) {
      o.lastIndex = stream.pos;
      const m = o.exec(stream.string);
      if (!m) { stream.skipToEnd(); return null; }
      if (m.index > stream.pos) { stream.pos = m.index; return null; }
      if (!m[0].length) { stream.next(); return null; }
      stream.pos += m[0].length;
      return 'fb-match';
    } };
    cm.addOverlay(overlay);
  }
  syncCurrent();
}

// Which match (if any) is the current selection; mark it and update the count.
function syncCurrent() {
  if (curMark) { curMark.clear(); curMark = null; }
  const from = cm.getCursor('from'), to = cm.getCursor('to');
  cur = matches.findIndex(m => m.line === from.line && m.ch === from.ch && to.line === m.line && to.ch === m.ch + m.len);
  if (cur >= 0) curMark = cm.markText(from, to, { className: 'cm-fb-current' });
  const n = matches.length, more = n >= LIMIT ? '+' : '', c = $('fbCount');
  c.textContent = !$('fbFind').value || !rx ? '' : !n ? 'No results' : cur >= 0 ? `${cur + 1} of ${n}${more}` : `${n}${more} found`;
  c.className = $('fbFind').value && rx && !n ? 'none' : '';
}

function select(i) {
  const m = matches[i];
  cm.setSelection(pos(m.line, m.ch), pos(m.line, m.ch + m.len));
  cm.scrollIntoView({ from: pos(m.line, m.ch), to: pos(m.line, m.ch + m.len) }, 80);
  syncCurrent();
}
// The first match at or after p (wrapping to the first one).
const firstFrom = p => { const i = matches.findIndex(m => !before(pos(m.line, m.ch), p)); return i < 0 ? 0 : i; };

export function go(dir) {
  if (!isOpen()) return openFindBar(false);
  if (!matches.length) return;
  const n = matches.length;
  if (cur >= 0) return select((cur + dir + n) % n);
  if (dir > 0) return select(firstFrom(cm.getCursor('to')));
  const from = cm.getCursor('from');
  let i = n - 1;
  while (i >= 0 && !before(pos(matches[i].line, matches[i].ch + matches[i].len), from)) i--;
  select(i < 0 ? n - 1 : i);
}

// The text that replaces match m (the match array is needed for $1 and friends).
function replacementFor(m) {
  const template = $('fbReplace').value;
  if (!$('fbRegex').checked) return template;
  const r = fresh();
  r.lastIndex = m.ch;
  return expandReplacement(template, r.exec(cm.getLine(m.line)));
}

function replaceOne() {
  if (!rx || !matches.length) return;
  if (cur < 0) return go(1);   // the first press selects the next match, the next one replaces it
  const m = matches[cur], text = replacementFor(m);
  cm.replaceRange(text, pos(m.line, m.ch), pos(m.line, m.ch + m.len), '+findreplace');
  compute();
  if (matches.length) select(firstFrom(pos(m.line, m.ch + text.length)));
}

function replaceAll() {
  if (!rx || !matches.length) return;
  const n = matches.length, capped = n >= LIMIT;
  cm.operation(() => {   // one operation, so one undo step
    for (let i = n - 1; i >= 0; i--) {   // from the end, so earlier positions stay valid
      const m = matches[i];
      cm.replaceRange(replacementFor(m), pos(m.line, m.ch), pos(m.line, m.ch + m.len), '+findreplace');
    }
  });
  compute();
  setStatus(`replaced ${n} in ${state.current}${capped ? ' (the first 50000; run it again for the rest)' : ''}`, 'ok');
}

function showReplace(on) {
  $('fbReplaceRow').hidden = !on;
  $('fbToggle').textContent = on ? '▾' : '▸';
  $('fbToggle').setAttribute('aria-expanded', String(on));
  $('fbToggle').title = on ? 'Hide replace' : 'Show replace (Ctrl+H, Cmd+Option+F on a Mac)';
}

const barKeys = { Esc: () => closeFindBar() };

// Open the bar (with the replace row when withReplace), starting from the selected text, if any.
export function openFindBar(withReplace = false) {
  const wasOpen = isOpen();
  $('findBar').hidden = false;
  if (withReplace) showReplace(true);
  const sel = cm.getSelection();
  if (sel && !sel.includes('\n') && sel.length <= 200)
    $('fbFind').value = $('fbRegex').checked ? sel.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') : sel;
  start = cm.getCursor('from');
  if (!wasOpen) cm.addKeyMap(barKeys);
  compute();
  const input = withReplace && $('fbFind').value ? $('fbReplace') : $('fbFind');
  input.focus(); input.select();
}

export function closeFindBar() {
  if (!isOpen()) return;
  $('findBar').hidden = true;
  if (overlay) { cm.removeOverlay(overlay); overlay = null; }
  if (curMark) { curMark.clear(); curMark = null; }
  matches = []; rx = null;
  cm.removeKeyMap(barKeys);
  cm.focus();
}

// Typing searches as you go: select the first match from where the search started.
const searchAsYouType = debounce(() => { compute(); if (matches.length) select(firstFrom(start || cm.getCursor('from'))); }, 120);
$('fbFind').addEventListener('input', searchAsYouType);
for (const id of ['fbCase', 'fbWord', 'fbRegex']) $(id).addEventListener('change', () => { searchAsYouType(); $('fbFind').focus(); });

const isMod = e => e.ctrlKey || e.metaKey;
const wantsReplace = e => (e.ctrlKey && !e.metaKey && e.key.toLowerCase() === 'h') || (e.metaKey && e.altKey && e.code === 'KeyF');
$('fbFind').addEventListener('keydown', e => {
  if (e.key === 'Enter') { e.preventDefault(); go(e.shiftKey ? -1 : 1); }
  else if (e.key === 'Escape') { e.preventDefault(); closeFindBar(); }
  else if (wantsReplace(e)) { e.preventDefault(); showReplace($('fbReplaceRow').hidden); if (!$('fbReplaceRow').hidden) $('fbReplace').focus(); }
  else if (isMod(e) && e.key.toLowerCase() === 'f' && !e.shiftKey) { e.preventDefault(); $('fbFind').select(); }
});
$('fbReplace').addEventListener('keydown', e => {
  if (e.key === 'Enter') { e.preventDefault(); if (isMod(e)) replaceAll(); else replaceOne(); }
  else if (e.key === 'Escape') { e.preventDefault(); closeFindBar(); }
  else if (isMod(e) && e.key.toLowerCase() === 'f' && !e.shiftKey) { e.preventDefault(); $('fbFind').focus(); $('fbFind').select(); }
});
$('fbNext').onclick = () => go(1);
$('fbPrev').onclick = () => go(-1);
$('fbReplaceOne').onclick = replaceOne;
$('fbReplaceAll').onclick = replaceAll;
$('fbClose').onclick = closeFindBar;
$('fbToggle').onclick = () => { showReplace($('fbReplaceRow').hidden); ($('fbReplaceRow').hidden ? $('fbFind') : $('fbReplace')).focus(); };
// Hand the query to the search in all files.
$('fbAll').onclick = () => {
  $('searchInput').value = $('fbFind').value;
  $('sCase').checked = $('fbCase').checked; $('sWord').checked = $('fbWord').checked; $('sRegex').checked = $('fbRegex').checked;
  $('replaceInput').value = $('fbReplace').value;
  app.showTab('search'); app.runSearch(); $('searchInput').focus();
};

cm.on('changes', debounce(() => { if (isOpen()) compute(); }, 150));
cm.on('swapDoc', () => { if (isOpen()) { start = cm.getCursor('from'); compute(); } });
cm.on('cursorActivity', () => { if (isOpen()) syncCurrent(); });

Object.assign(app, { openFindBar, closeFindBar, findNext: go });
