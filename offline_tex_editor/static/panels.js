// Side panels: search (and replace) in all files, and the document outline.
import { $, el, app, state, api, post, setStatus, confirmClick, debounce } from './util.js';

// ---------- search all files ----------
let lastSearch = null;
const params = () => ({ q: $('searchInput').value, case: $('sCase').checked ? 1 : 0, word: $('sWord').checked ? 1 : 0,
                        regex: $('sRegex').checked ? 1 : 0 });

export async function runSearch() {
  const p = params();
  lastSearch = p;
  if (!p.q) { $('searchResults').replaceChildren(); $('searchCount').textContent = ''; $('replaceAllBtn').disabled = true; return; }
  const r = await api('/api/search?' + new URLSearchParams(p));
  if (lastSearch !== p) return;   // a newer search is under way
  if (!r.ok) { $('searchCount').textContent = r.error; $('replaceAllBtn').disabled = true; return; }
  const byFile = new Map();
  r.matches.forEach(m => { if (!byFile.has(m.path)) byFile.set(m.path, []); byFile.get(m.path).push(m); });
  const rows = [];
  for (const [path, ms] of byFile) {
    rows.push(el('div', { className: 'sf', title: path }, path, el('small', {}, String(ms.length))));
    for (const m of ms) {
      const start = Math.max(0, m.col - 30), pre = (start ? '…' : '') + m.text.slice(start, m.col).trimStart();
      rows.push(el('div', { className: 'sm', title: m.text.trim(), onclick: () => app.openFile(m.path, m.line, { ch: m.col, len: m.len }) },
        el('i', {}, String(m.line)), pre, el('b', {}, m.text.slice(m.col, m.col + m.len)), m.text.slice(m.col + m.len, m.col + m.len + 120)));
    }
  }
  $('searchResults').replaceChildren(...rows);
  const n = r.matches.length;
  $('searchCount').textContent = n ? `${n}${r.truncated ? '+' : ''} in ${byFile.size} file${byFile.size === 1 ? '' : 's'}` : 'No results';
  $('replaceAllBtn').disabled = !n;
}
const searchSoon = debounce(runSearch, 250);
['searchInput'].forEach(id => $(id).addEventListener('input', searchSoon));
['sCase', 'sWord', 'sRegex'].forEach(id => $(id).addEventListener('change', runSearch));
$('searchInput').addEventListener('keydown', e => { if (e.key === 'Enter') runSearch(); });

$('replaceAllBtn').onclick = () => {
  const n = $('searchCount').textContent.split(' ')[0];
  confirmClick($('replaceAllBtn'), `Replace ${n}?`, async () => {
    if (!(await app.saveAll())) return;
    const p = params();
    const r = await post('/api/replace', { q: p.q, regex: !!p.regex, case: !!p.case, word: !!p.word, replacement: $('replaceInput').value });
    if (!r.ok) return setStatus('replace failed: ' + r.error, 'err');
    await app.reloadClean();
    setStatus(`replaced ${r.replaced} in ${Object.keys(r.files).length} file(s)`, 'ok');
    runSearch(); app.filesChanged?.();
    if ($('auto').checked) app.compile();
  });
};

export function showSearch() {
  app.showTab('search');
  const sel = app.cm.getSelection();
  if (sel && !sel.includes('\n')) $('searchInput').value = sel;
  $('searchInput').focus(); $('searchInput').select();
  runSearch();
}

// ---------- outline ----------
let outline = [];
export async function refreshOutline() {
  const r = await api('/api/outline');
  if (!r.ok) return;
  outline = r.entries;
  const min = Math.min(...outline.map(e => e.level), 9);
  $('outline').replaceChildren(...(outline.length ? outline.map((e, i) => {
    const row = el('div', { className: `o l${e.level}`, title: `${e.file}:${e.line}`, dataset: { i },
                            onclick: () => app.openFile(e.file, e.line, { flash: true }) }, e.title || '(untitled)');
    row.style.paddingLeft = (8 + (e.level - min) * 12) + 'px';
    return row;
  }) : [el('div', { className: 'muted', style: 'padding: 8px' }, 'No \\section commands found in the main document.')]));
  markCurrentSection();
}
// Highlight the section the cursor is in.
export function markCurrentSection() {
  if ($('outlinePane').hidden || !state.current) return;
  const line = app.cm.getCursor().line + 1;
  let cur = -1;
  outline.forEach((e, i) => { if (e.file === state.current && e.line <= line) cur = i; });
  document.querySelectorAll('#outline .o').forEach(r => r.classList.toggle('cur', +r.dataset.i === cur));
}

Object.assign(app, { runSearch, showSearch, refreshOutline, markCurrentSection });
