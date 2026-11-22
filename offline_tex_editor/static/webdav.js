// The WebDAV panel: connect the project to a folder on a WebDAV server (Nextcloud, ownCloud, …), see what changed on
// each side, and sync. webdav.py does the work; this shows its status, what the server has, and what a sync did.
import { $, el, app, api, post, confirmClick } from './util.js';

const INTRO = $('davIntro').textContent, TAB_TITLE = $('davBadge').parentElement.title;
let dav = null;        // the last status
let plan = null;       // what the last check found, or null before the first one
let lastCheck = 0;

function out(text, cls, ...buttons) {
  $('davOut').replaceChildren(text || '', ...(buttons.length ? [el('div', { className: 'gitBtns' }, ...buttons)] : []));
  $('davOut').className = cls || '';
}

const where = url => { try { const u = new URL(url); return u.host + decodeURIComponent(u.pathname); } catch { return url; } };
const when = t => {
  const d = new Date(t * 1000);
  return d.toDateString() === new Date().toDateString() ? 'at ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : 'on ' + d.toLocaleString();
};
const openPath = p => app.project.files.includes(p) ? app.openFile(p) : app.project.assets.includes(p) ? app.preview(p) : null;
const names = list => list.length > 6 ? `${list.length} files` : list.join(', ');
const pairs = list => list.map(([from, to]) => `${from} → ${to}`).join(', ');
const isFolder = p => p.endsWith('/');

// What the server has for us, from the last check: [{path, code, note}].
function incoming() {
  if (!plan) return [];
  return [
    ...plan.download.map(path => ({ path, code: isFolder(path) ? 'N' : 'M', note: isFolder(path) ? 'new folder on the server' : 'changed on the server' })),
    ...plan.delete_here.map(path => ({ path, code: 'D', note: isFolder(path) ? 'folder deleted on the server (deleted here once it is empty)' : 'deleted on the server' })),
    ...plan.rename_here.map(([from, to]) => ({ path: to, code: 'R', note: `renamed on the server, from ${from}` })),
    ...plan.conflicts.map(path => ({ path, code: 'C', note: 'changed here and on the server: merged when you sync' })),
    ...plan.compare.map(path => ({ path, code: 'C', note: 'here and on the server, never synced: compared when you sync' })),
  ];
}

function row(code, path, title, ...buttons) {
  const p = isFolder(path) ? path.slice(0, -1) : path;   // a folder: "name/" in its parent
  const name = p.slice(p.lastIndexOf('/') + 1) + (isFolder(path) ? '/' : ''), dir = p.includes('/') ? p.slice(0, p.lastIndexOf('/')) : '';
  return el('div', { className: 'gf', title: title ? `${path}: ${title}` : path, onclick: () => openPath(path) },
    el('span', { className: `st ${code}` }, code), el('span', { className: 'nm' }, name + ' ', el('small', {}, dir)), ...buttons);
}
const button = (label, title, fn, cls) => el('button', { className: cls || '', title, onclick: e => { e.stopPropagation(); fn(); } }, label);
const reviewButtons = path => [button('Review', 'Show what changes in this file', () => review(path)),
                               button('Accept', 'Download it, then sync', () => sync([path]), 'primary')];

function renderLists() {
  const rows = [], section = title => rows.push(el('div', { className: 'gsec' }, title));
  const s = dav;
  if (s.blocked.length) {
    section(`Resolve first (${s.blocked.length})`);
    s.blocked.forEach(b => rows.push(b.why === 'server copy'
      ? row('C', b.path, `the server's version is in ${b.copy}: keep what you need in this file, then delete the copy`,
            button('Copy', `Open ${b.copy}`, () => openPath(b.copy)))
      : row('C', b.path, 'edit the <<<<<<< … >>>>>>> blocks so that only the right text stays, then save')));
  }
  if (plan?.clashes.length) {
    section(`Names that differ only in case (${plan.clashes.length})`);
    plan.clashes.forEach(c => {
      // "Fig.png / fig.png  figs" (the names, then their folder), not a path split at its last slash
      const base = p => p.slice(p.lastIndexOf('/') + 1), dir = c.names[0].includes('/') ? c.names[0].slice(0, c.names[0].lastIndexOf('/')) : '';
      rows.push(el('div', { className: 'gf', title: `${c.names.join(', ')}: ${c.skipped
          ? 'this computer cannot keep both, so they are not synced: rename one of them on the server'
          : 'teammates on Windows or macOS cannot keep both: rename one of them'}` },
        el('span', { className: 'st C' }, 'C'), el('span', { className: 'nm' }, c.names.map(base).join(' / ') + ' ', el('small', {}, dir))));
    });
  }
  if (plan?.held.length) {
    section(`Not downloaded yet (${plan.held.length})`);
    plan.held.forEach(p => rows.push(row('H', p, 'runs as a program when you compile: review it, then accept it', ...reviewButtons(p))));
  }
  section(`Changed here (${s.changed_here.length})`);
  s.changed_here.forEach(f => rows.push(row({ new: 'N', changed: 'M', deleted: 'D', renamed: 'R' }[f.state], f.path,
    f.state === 'renamed' ? `renamed here, from ${f.from}` : `${f.state} here`)));
  const inc = incoming();
  section(plan ? `Changed on the server (${inc.length})` : 'Changed on the server (not checked yet)');
  inc.forEach(f => rows.push(row(f.code, f.path, f.note)));
  $('davLists').replaceChildren(...rows);
}

function render(s) {
  if (!s || !s.ok) { if (s?.error) out(s.error, 'err'); return; }
  dav = s;
  const on = s.connected;
  $('davSetup').hidden = on && !s.needs_password;
  $('davHead').hidden = $('davBtns').hidden = !on;
  $('davUrl').disabled = $('davUser').disabled = on;
  if (!on) {
    plan = null; $('davIntro').textContent = INTRO; $('davBadge').textContent = ''; $('davLists').replaceChildren();
    $('davBadge').parentElement.title = TAB_TITLE;
    return;
  }
  if (s.needs_password) {
    $('davIntro').textContent = `Enter the password for ${s.user} again: it is kept only while the editor runs.`;
    $('davUrl').value = s.url; $('davUser').value = s.user;
  }
  $('davSync').disabled = $('davCheck').disabled = s.needs_password;
  $('davWhere').textContent = (s.user ? s.user + ' @ ' : '') + where(s.url); $('davWhere').title = s.url;
  $('davWhen').textContent = s.last_sync ? 'Last synced ' + when(s.last_sync) : 'Not synced yet';
  const up = s.changed_here.length, down = incoming().length + (plan ? plan.held.length : 0);
  $('davSync').textContent = 'Sync' + (up || down ? ` ↑${up} ↓${down}` : '');
  // a dot, not a count: the tab is narrow, and the Sync button shows the counts
  $('davBadge').textContent = up + down ? '●' : '';
  $('davBadge').parentElement.title = up + down ? `WebDAV: ${up} to upload, ${down} on the server` : TAB_TITLE;
  renderLists();
}

export async function davRefresh() { render(await api('/api/webdav/status')); }
let soonT;
export const davSoon = () => { clearTimeout(soonT); soonT = setTimeout(davRefresh, 400); };

// A request that failed: when the password is missing or was refused, show the form for it.
async function failed(r) {
  if (r.needs_password) await davRefresh();
  out(r.error || 'the request failed', 'err');
}

async function check(quiet) {
  if (!dav?.connected || dav.needs_password) return;
  if (!quiet) { $('davCheck').disabled = true; out('Checking the server…'); }
  const r = await post('/api/webdav/check');
  $('davCheck').disabled = false;
  if (!r.ok) {
    if (r.status !== 409 && (!quiet || r.needs_password)) await failed(r);   // 409: a sync is running
    return;
  }
  plan = r.plan; lastCheck = Date.now(); render(r);
  if (quiet) return;
  const n = incoming().length, held = plan.held;
  out([n ? `The server has ${n} change${n > 1 ? 's' : ''}. Press Sync to get ${n > 1 ? 'them' : 'it'}.` : '',
       held.length ? `${names(held)} changed on the server: review, then accept.` : '',
       n || held.length ? '' : 'No changes on the server.'].filter(Boolean).join('\n'), 'ok');
}

function report(res) {
  const lines = [], said = (list, verb) => { if (list.length) lines.push(`${verb}: ${names(list)}.`); };
  said(res.uploaded, 'Uploaded'); said(res.downloaded, 'Downloaded'); said(res.merged, 'Merged changes from both sides in');
  said(res.deleted_there, 'Deleted on the server');
  said(res.deleted_here, 'Deleted here' + (res.deleted_here.some(p => !isFolder(p)) ? ' (the files are in build/.editor-trash/)' : ''));
  if (res.renamed_here.length) lines.push(`Renamed here, as on the server: ${pairs(res.renamed_here)}.`);
  if (res.renamed_there.length) lines.push(`Renamed on the server, as here: ${pairs(res.renamed_there)}.`);
  let problem = false;
  const warn = text => { lines.push(text); problem = true; };
  res.kept.forEach(p => lines.push(`${p} was deleted on the server, but it is this project's main document, so it was uploaded again.`));
  if (res.conflicts.length) warn(`${names(res.conflicts)}: changed in the same places here and on the server. Edit the marked blocks (<<<<<<< this computer … >>>>>>> server) so that only the right text stays, save, and sync again.`);
  res.copies.forEach(c => warn(`${c.path} changed here and on the server, and could not be merged: the server's version is in ${c.copy}. Keep what you need in ${c.path}, delete the copy, and sync again.`));
  if (res.blocked.length) warn(`Not uploaded until their conflicts are resolved: ${names(res.blocked.map(b => b.path))}.`);
  if (res.retry.length) warn(`${names(res.retry)} changed while syncing: sync again.`);
  res.skipped.forEach(s => warn(`Skipped ${s.path}: ${s.error}`));
  res.clashes.forEach(c => warn(c.skipped
    ? `Not synced: ${c.names.join(' and ')}. The server has both, and they differ only in case, which this computer cannot keep apart: rename one of them on the server.`
    : `${c.names.join(' and ')} differ only in case: teammates on Windows or macOS cannot keep both. Rename one of them.`));
  const held = res.held;
  if (held.length) warn(`${names(held)} changed on the server. ${held.length > 1 ? 'These files run' : 'It runs'} as a program when you compile, so ${held.length > 1 ? 'they are' : 'it is'} downloaded only when you accept.`);
  out(lines.join('\n') || 'Already in sync.', problem ? 'err' : 'ok', ...(held.length === 1 ? reviewButtons(held[0]) : []));
}

async function sync(accept = []) {
  if (!(await app.saveAll())) return;
  $('davSync').disabled = true; out('Syncing…');
  const r = await post('/api/webdav/sync', { accept });
  $('davSync').disabled = false;
  if (!r.ok) return failed(r);
  plan = r.plan; lastCheck = Date.now(); render(r);
  report(r.result);
  r.result.renamed_here.forEach(([from, to]) => app.renameDoc(from, to));
  r.result.deleted_here.filter(p => !isFolder(p)).forEach(p => app.dropDoc(p));
  await app.loadFiles(); await app.reloadClean(); app.filesChanged?.();
}

async function review(path) {
  const r = await post('/api/webdav/peek', { path });
  if (!r.ok) return failed(r);
  app.showDiffText(`${path}: this computer → server`, r.diff);
}

async function connect() {
  const url = $('davUrl').value.trim(), user = $('davUser').value.trim(), password = $('davPass').value;
  if (!url) return $('davUrl').focus();
  $('davConnect').disabled = true; out('Connecting…');
  const r = await post('/api/webdav/connect', { url, user, password });
  $('davConnect').disabled = false;
  if (!r.ok) return out(r.error, 'err');
  $('davPass').value = '';
  render(r);
  await check(true);
  if (!plan) return;
  const up = plan.upload.length + plan.delete_there.length, down = incoming().length - plan.conflicts.length - plan.compare.length;
  const both = plan.conflicts.length + plan.compare.length;
  out(up || down || both ? `Connected. Press Sync to upload ${up} and download ${down} file${down === 1 ? '' : 's'}${both ? `; ${both} on both sides will be compared` : ''}.`
    : 'Connected. Everything is in sync.', 'ok');
}

// The tab was opened: show the status, and look at the server unless that was done a moment ago.
export async function davOpen() {
  await davRefresh();
  if (Date.now() - lastCheck > 60 * 1000) check(true);
}

$('davConnect').onclick = connect;
$('davPass').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); connect(); } });
$('davSync').onclick = () => sync();
$('davCheck').onclick = () => check(false);
$('davDisconnect').onclick = () => confirmClick($('davDisconnect'), 'Disconnect?', async () => {
  const r = await post('/api/webdav/disconnect');
  if (!r.ok) return out(r.error, 'err');
  render(r); out('Disconnected. The files here stay as they are.', 'ok');
});
// Look at the server every few minutes, so the panel shows what teammates changed without pressing anything.
setInterval(() => {
  if (!document.hidden && dav?.connected && !dav.needs_password && Date.now() - lastCheck > 5 * 60 * 1000) check(true);
}, 60 * 1000);
setInterval(() => { if (!document.hidden && !$('davPane').hidden) davRefresh(); }, 10000);

Object.assign(app, { davRefresh, davSoon, davOpen });
