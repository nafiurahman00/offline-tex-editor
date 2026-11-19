// Entry point: loads the modules (each one wires up its part of the page), then starts the editor.
import { $, app, state, project, store, api, setStatus, debounce } from './util.js';
import './editor.js';
import './findbar.js';
import './complete.js';
import './files.js';
import './diffview.js';
import './compile.js';
import './pdfview.js';
import './panels.js';
import './git.js';
import './tools.js';

app.project = project;

// ---------- theme ----------
const applyTheme = t => { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; };
applyTheme(store.get('theme', null));
$('themeBtn').onclick = () => {
  const dark = matchMedia('(prefers-color-scheme: dark)').matches;
  const cur = document.documentElement.dataset.theme || (dark ? 'dark' : 'light');
  const next = cur === 'dark' ? 'light' : 'dark'; applyTheme(next); store.set('theme', next);
};

// ---------- side panels ----------
export function showTab(t) {
  for (const b of document.querySelectorAll('#sideTabs button')) {
    const on = b.dataset.tab === t;
    b.classList.toggle('on', on); $(b.dataset.tab + 'Pane').hidden = !on;
  }
  store.set('sideTab', t);
  if (t === 'git') app.gitRefresh(false);
  if (t === 'outline') app.refreshOutline();
}
document.querySelectorAll('#sideTabs button').forEach(b => { b.onclick = () => showTab(b.dataset.tab); });
app.showTab = showTab;

// ---------- keeping things fresh ----------
const afterSave = debounce(() => {
  app.gitSoon(); app.refreshCompletions();
  if (!$('outlinePane').hidden) app.refreshOutline();
}, 600);
app.onSaved = afterSave;
app.filesChanged = () => { afterSave(); app.refreshSettings(); };
app.onCursor = () => app.markCurrentSection();
app.onFileOpened = () => { app.markCurrentSection(); };

// ---------- keyboard ----------
document.addEventListener('keydown', e => {
  if (e.defaultPrevented) return;   // the editor handled it (its own search, completion, …)
  const mod = e.ctrlKey || e.metaKey, key = e.key.toLowerCase();
  if (mod && e.shiftKey && key === 'f') { e.preventDefault(); app.showSearch(); return; }
  // Ctrl/Cmd+F outside the editor (and its search box) searches the PDF.
  if (mod && !e.shiftKey && !e.altKey && key === 'f' && $('diffView').hidden && $('preview').hidden &&
      !app.cm.getWrapperElement().contains(e.target)) { e.preventDefault(); app.openFind(); return; }
  if (e.key === 'Escape') {
    if (!$('tools').hidden) app.closeTools();
    else if (!$('preview').hidden) app.closePreview();
    else if (!$('diffView').hidden) app.closeDiff();
  }
});

// ---------- start ----------
(async () => {
  await app.loadFiles();
  await app.restoreTabs(project.main);
  showTab(store.get('sideTab', 'files'));
  app.refreshSettings(); app.refreshCompletions(); app.gitRefresh(false);
  const s = await api('/api/status');
  if (s.running) setStatus('initial compile…', 'run');
  else if (s.finished) app.showResult(s);
  await app.loadPdf();
})();
