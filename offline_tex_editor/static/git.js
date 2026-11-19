// The Git panel: changes and diffs, stage, commit, push, pull (also when histories diverged), conflicts, history.
import { $, el, app, api, post } from './util.js';

app.git = null;
export function gitOut(text, cls, ...buttons) {
  $('gitMsgOut').replaceChildren(text || '', ...(buttons.length ? [el('div', { className: 'gitBtns', style: 'margin-top: 4px' }, ...buttons)] : []));
  $('gitMsgOut').className = cls || '';
}
// The editor's path for a path relative to the repository top level (null when outside the project).
const editorPath = p => { const g = app.git; if (!g || !g.prefix) return p; return p.startsWith(g.prefix + '/') ? p.slice(g.prefix.length + 1) : null; };

function renderGit(s) {
  if (!s || !s.ok) { gitOut(s && s.error || 'git status failed', 'err'); return; }
  app.git = s;
  // What the panel offers depends on the stage: no repository, a repository without a remote, or connected.
  $('gitInit').hidden = s.repo;
  for (const id of ['gitCommitBox', 'gitScroll']) $(id).hidden = !s.repo;
  if (!s.repo) {
    $('gitBranch').textContent = 'not a git repository'; $('gitSync').textContent = ''; $('gitLists').replaceChildren();
    $('gitBadge').textContent = ''; $('gitState').hidden = true; $('gitConnect').hidden = true; $('gitRemoteBtns').hidden = true;
    return;
  }
  $('gitConnect').hidden = s.remotes.length > 0;
  $('gitRemoteBtns').hidden = !s.remotes.length;
  $('gitSyncBtn').textContent = 'Sync' + (s.ahead || s.behind ? ` ↑${s.ahead} ↓${s.behind}` : '');
  $('gitBranch').textContent = '⎇ ' + s.branch;
  $('gitSync').textContent = s.upstream ? `↑${s.ahead} ↓${s.behind}` : (s.remotes.length ? 'not pushed yet' : 'no remote');
  $('gitBadge').textContent = s.files.length ? `(${s.files.length})` : '';
  $('gitPush').textContent = s.ahead ? `Push (${s.ahead})` : 'Push';
  const conflicts = s.files.filter(f => f.conflict), staged = s.files.filter(f => f.staged), changed = s.files.filter(f => f.unstaged);

  // A rebase or merge in progress: say what to do next.
  $('gitState').hidden = !s.state;
  if (s.state) {
    const what = s.state === 'rebasing' ? 'Rebase' : 'Merge';
    $('gitState').replaceChildren(
      el('div', {}, conflicts.length
        ? `${what} in progress: ${conflicts.length} file${conflicts.length > 1 ? 's have' : ' has'} conflicts. Edit them (keep the right side of each <<<<<<< … >>>>>>> block), then mark them resolved.`
        : `${what} in progress: all conflicts are resolved.`),
      el('div', { className: 'gitBtns', style: 'margin-top: 4px' },
        el('button', { className: 'primary', disabled: conflicts.length > 0, onclick: () => gitOp('continue') }, s.state === 'rebasing' ? 'Continue rebase' : 'Finish merge'),
        el('button', { onclick: () => gitOp('abort') }, `Abort ${what.toLowerCase()}`)));
  }
  $('gitCommit').disabled = s.state === 'rebasing';

  const rows = [];
  const section = (title, items, kind) => {
    const h = el('div', { className: 'gsec' }, `${title} (${items.length})`);
    if (items.length && kind !== 'conflict')
      h.append(el('button', { onclick: () => gitOp(kind === 'staged' ? 'unstage' : 'stage', { paths: items.map(f => f.path) }) }, kind === 'staged' ? 'Unstage all' : 'Stage all'));
    rows.push(h);
    items.forEach(f => {
      const code = kind === 'conflict' ? 'C' : kind === 'staged' ? f.x : (f.untracked ? 'U' : f.y);
      const name = f.path.slice(f.path.lastIndexOf('/') + 1), dir = f.path.includes('/') ? f.path.slice(0, f.path.lastIndexOf('/')) : '';
      const btn = kind === 'conflict'
        ? el('button', { title: 'Mark resolved (stage it)', onclick: e => { e.stopPropagation(); gitOp('stage', { paths: [f.path] }); } }, 'Resolved')
        : el('button', { title: kind === 'staged' ? 'Unstage' : 'Stage', onclick: e => { e.stopPropagation(); gitOp(kind === 'staged' ? 'unstage' : 'stage', { paths: [f.path] }); } }, kind === 'staged' ? '−' : '+');
      const open = () => { const p = editorPath(f.path); if (kind === 'conflict' && p) app.openFile(p); else app.showDiff(f.path, kind === 'staged'); };
      rows.push(el('div', { className: 'gf', title: f.path + (f.orig ? ` (from ${f.orig})` : ''), onclick: open },
        el('span', { className: `st ${code}` }, code), el('span', { className: 'nm' }, name + ' ', el('small', {}, dir)), btn));
    });
  };
  if (conflicts.length) section('Conflicts', conflicts, 'conflict');
  section('Staged', staged, 'staged'); section('Changes', changed, 'changed');
  if (!s.files.length) rows.push(el('div', { className: 'gsec' }, 'Working tree clean'));
  $('gitLists').replaceChildren(...rows);
}

export async function gitRefresh(fetchRemote) {
  renderGit(fetchRemote ? await post('/api/git/fetch') : await api('/api/git/status'));
  if (!$('gitPane').hidden && app.git && app.git.repo) {
    const l = await api('/api/git/log');
    $('gitLog').replaceChildren(...(l.commits || []).map(c => el('div', { className: 'gl', title: `${c.author}, ${c.when}`, onclick: () => app.showCommit(c.hash, c.subject) },
      el('code', {}, c.hash), ' ', el('span', {}, c.subject), ' ', el('small', {}, c.when))));
  }
}
let gitT;
export const gitSoon = () => { clearTimeout(gitT); gitT = setTimeout(() => gitRefresh(false), 400); };

async function gitOp(op, body) {
  const r = await post('/api/git/' + op, body);
  if (!r.ok) { gitOut(r.error, 'err'); return gitRefresh(false); }
  renderGit(r);
  if (op === 'continue' || op === 'abort') { gitOut(r.output || (op === 'abort' ? 'Aborted.' : 'Done.'), 'ok'); await app.reloadClean(); gitRefresh(false); }
  app.closeDiff();
}

async function gitCommit() {
  if (!(await app.saveAll())) return;
  const msg = $('gitMsg').value.trim();
  if (!msg && app.git?.state !== 'merging') return gitOut('Write a commit message first.', 'err');
  const r = await post('/api/git/commit', { message: msg });
  if (!r.ok) return gitOut(r.error, 'err');
  $('gitMsg').value = ''; gitOut('Committed ' + r.commit, 'ok'); renderGit(r); gitRefresh(false);
}

async function pull(mode = 'ff') {
  $('gitPull').disabled = true; gitOut(mode === 'ff' ? 'Pulling…' : mode === 'rebase' ? 'Rebasing…' : 'Merging…');
  const r = await post('/api/git/pull', { mode });
  $('gitPull').disabled = false;
  if (!r.ok) return gitOut(r.error, 'err');
  renderGit(r);
  if (r.diverged) return gitOut('Your branch and the remote both have new commits. How should they be combined?', '',
    el('button', { className: 'primary', title: 'Replay your commits on top of the remote ones (linear history)', onclick: () => pull('rebase') }, 'Rebase my commits'),
    el('button', { title: 'Create a merge commit', onclick: () => pull('merge') }, 'Merge'));
  if (r.conflict) gitOut('Some files conflict. Open them from the Conflicts list, fix them, and mark them resolved.', 'err');
  else gitOut(r.output || 'Up to date.', 'ok');
  await app.reloadClean(); gitRefresh(false); app.filesChanged?.();
}

// Sync: pull, then push. When both sides have new commits, ask how to combine them, as Pull does.
async function sync(mode = 'ff') {
  if (!(await app.saveAll())) return;
  $('gitSyncBtn').disabled = true; gitOut(mode === 'ff' ? 'Syncing…' : mode === 'rebase' ? 'Rebasing, then pushing…' : 'Merging, then pushing…');
  const r = await post('/api/git/sync', { mode });
  $('gitSyncBtn').disabled = false;
  if (!r.ok) return gitOut(r.error, 'err');
  renderGit(r);
  if (r.diverged) return gitOut('Your branch and GitHub both have new commits. How should they be combined?', '',
    el('button', { className: 'primary', title: 'Replay your commits on top of the remote ones, then push', onclick: () => sync('rebase') }, 'Rebase my commits'),
    el('button', { title: 'Create a merge commit, then push', onclick: () => sync('merge') }, 'Merge'));
  if (r.conflict) gitOut('Some files conflict. Open them from the Conflicts list, fix them, mark them resolved, then sync again.', 'err');
  else gitOut((r.output || 'Already in sync.') + (r.files.length ? '\nChanges you have not committed stay on this computer until you commit them.' : ''), 'ok');
  await app.reloadClean(); gitRefresh(false); app.filesChanged?.();
}

async function connect() {
  const url = $('gitRemoteUrl').value.trim();
  if (!url) return $('gitRemoteUrl').focus();
  const r = await post('/api/git/remote', { url });
  if (!r.ok) return gitOut(r.error, 'err');
  $('gitRemoteUrl').value = '';
  renderGit(r);
  gitOut(r.files.length || !(await api('/api/git/log')).commits?.length
    ? 'Connected. Commit your files, then press Sync to send them to GitHub.' : 'Connected. Press Sync to send your commits to GitHub.', 'ok');
}

async function initRepo() {
  const r = await post('/api/git/init');
  if (!r.ok) return gitOut(r.error, 'err');
  renderGit(r);
  gitOut('Started a git repository. Stage your files and commit them, then connect it to GitHub.', 'ok');
  gitRefresh(false);
}

async function push() {
  $('gitPush').disabled = true; gitOut('Pushing…');
  const r = await post('/api/git/push');
  $('gitPush').disabled = false;
  if (!r.ok) return gitOut(r.error, 'err');
  gitOut(r.output || 'Done.', 'ok'); renderGit(r); gitRefresh(false);
}

$('gitCommit').onclick = gitCommit;
$('gitStageAll').onclick = async () => { if (await app.saveAll()) gitOp('stage', { all: true }); };
$('gitPush').onclick = push;
$('gitPull').onclick = () => pull('ff');
$('gitRefresh').onclick = () => gitRefresh(true);
$('gitSyncBtn').onclick = () => sync('ff');
$('gitConnectBtn').onclick = connect;
$('gitRemoteUrl').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); connect(); } });
$('gitInitBtn').onclick = initRepo;
// Fetch in the background every few minutes, so "behind" counts are current without pressing anything.
let lastFetch = 0;
setInterval(async () => {
  if (document.hidden || !app.git?.remotes?.length || Date.now() - lastFetch < 5 * 60 * 1000) return;
  lastFetch = Date.now();
  renderGit(await post('/api/git/fetch'));
}, 60 * 1000);
$('gitMsg').addEventListener('keydown', e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); gitCommit(); } });
setInterval(() => { if (!document.hidden) gitRefresh(false); }, 10000);

Object.assign(app, { gitRefresh, gitSoon, gitOut });
