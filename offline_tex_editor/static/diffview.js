// The overlay over the editor that shows a git diff, a commit, or the source changes in the latest compile.
import { $, el, app, api } from './util.js';

let diffPath = null, showingChanges = false;

function open(title, canOpen) {
  $('diffTitle').textContent = title; $('diffOpen').hidden = !canOpen;
  $('diffView').hidden = false; $('diffBody').scrollTop = 0;
}

export function renderDiff(text) {
  $('diffBody').replaceChildren(...text.split('\n').map(l => el('div', {
    className: l.startsWith('@@') ? 'hunk'
      : /^(---$|diff |index |--- |\+\+\+ |new file|deleted file|similarity|rename |commit |Author:|Date:)/.test(l) ? 'meta'
      : l.startsWith('+') ? 'add' : l.startsWith('-') ? 'del' : '',
  }, l || ' ')));
}

// A path relative to the git top level -> the editor's path, or null when it is outside the project.
const editorPath = p => {
  const g = app.git; if (!g || !g.prefix) return p;
  return p.startsWith(g.prefix + '/') ? p.slice(g.prefix.length + 1) : null;
};

export async function showDiff(path, staged) {
  const r = await api(`/api/git/diff?path=${encodeURIComponent(path)}&staged=${staged ? 1 : 0}`);
  if (!r.ok) return app.gitOut(r.error, 'err');
  diffPath = path; showingChanges = false;
  renderDiff(r.diff); open(`${path} — ${staged ? 'staged' : 'working tree'}`, !!editorPath(path));
}

export async function showCommit(rev, subject) {
  const r = await api('/api/git/show?rev=' + encodeURIComponent(rev));
  if (!r.ok) return app.gitOut(r.error, 'err');
  diffPath = null; showingChanges = false;
  renderDiff(r.diff); open(`${rev} ${subject}`, false);
}

// Word-level diff of the sources between the last two compiles that differ.
export async function showChanges() {
  const r = await api('/api/changes');
  if (!r.ok) return;
  diffPath = null; showingChanges = true;
  const body = $('diffBody'), when = t => new Date(t * 1000).toLocaleString(), rows = [];
  const line = (cls, text) => { const d = el('div', { className: cls }, text); rows.push(d); return d; };
  let title = 'Changes in the latest compile';
  if (!r.baseline) line('meta', 'No earlier compile to compare with yet. Changes appear after the next compile that changes the source.');
  else {
    title += ` (${when(r.to)}, vs ${when(r.from)})`;
    if (!r.files.length) line('meta', 'No source changes.');
    r.files.forEach(f => {
      line('cfile', `${f.path}${f.status === 'modified' ? '' : '  (' + f.status + ')'}`);
      f.hunks.forEach(h => {
        const go = () => { if (f.status !== 'deleted') { closeDiff(); app.openFile(f.path, h.line, { flash: true }); } };
        line('hunk chunk', `line ${h.line}`).onclick = go;
        const d = line('cbody chunk', ''); d.onclick = go;
        h.segs.forEach(([kind, text]) => d.append(el('span', { className: kind === 'eq' ? '' : kind === 'add' ? 'ins' : 'rem' }, text)));
      });
    });
  }
  body.replaceChildren(...rows);
  open(title, false);
}

export function closeDiff() { $('diffView').hidden = true; diffPath = null; showingChanges = false; app.cm.refresh(); }
const refreshChangesIfOpen = () => { if (showingChanges && !$('diffView').hidden) showChanges(); };

$('changesBtn').onclick = showChanges;
$('diffClose').onclick = closeDiff;
$('diffOpen').onclick = () => { const p = editorPath(diffPath); closeDiff(); if (p) app.openFile(p); };

Object.assign(app, { showDiff, showCommit, showChanges, closeDiff, refreshChangesIfOpen, isDiffOpen: () => !$('diffView').hidden });
