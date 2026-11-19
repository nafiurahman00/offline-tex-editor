// Compiling: run, queue, stop; the log panel with errors, warnings and hints; polling for compiles and PDFs that
// happen elsewhere.
import { $, el, app, state, api, post, setStatus } from './util.js';

let queued = false;   // a save arrived while a compile was running: compile once more when it ends

function running(on) {
  $('stopBtn').hidden = !on;
  $('compileBtn').disabled = on;
  $('compileBtn').textContent = on ? 'Compiling…' : 'Recompile';
}

export async function compile() {
  if (!(await app.saveAll())) return;
  if (state.compiling) { queued = true; setStatus('compiling… (will compile again for the latest changes)', 'run'); return; }
  state.compiling = true; running(true); setStatus('compiling…', 'run');
  const r = await post('/api/compile');
  state.compiling = false; running(false);
  if (r.status === 409) { queued = true; setStatus('compiling… (started elsewhere; will compile again)', 'run'); return; }
  if (!r.ok) setStatus('compile failed: ' + (r.error || r.status), 'err');
  else { showResult(r); await app.loadPdf(); }
  if (queued) { queued = false; compile(); }
}

async function stop() {
  setStatus('stopping…', 'run');
  await post('/api/compile/stop');
}

const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
const where = e => e.file ? `${e.file}${e.line ? ':' + e.line : ''}` : '';

export function showResult(r) {
  state.lastResult = r;
  const errors = r.errors || [], warnings = r.warnings || [];
  $('nErr').textContent = plural(errors.length, 'error'); $('nWarn').textContent = plural(warnings.length, 'warning');
  const engine = r.engine ? `${r.engine}${r.engine_source && r.engine_source !== 'default' ? ` (from ${r.engine_source})` : ''}`
                          : 'engine chosen by latexmkrc';
  if (r.stopped) setStatus('compile stopped', 'err');
  else if (r.success) setStatus(`compiled in ${r.seconds}s${r.engine && r.engine !== 'pdflatex' ? ' · ' + r.engine : ''}`, 'ok', `Compiled with ${engine}`);
  else if (errors.length) setStatus(`${plural(errors.length, 'error')}: ${errors[0].message}${errors[0].file ? ` (${where(errors[0])})` : ''}`, 'err', `Compiled with ${engine}`);
  else setStatus(`latexmk failed (${r.seconds}s): see the log`, 'err');

  const item = (cls, x) => {
    const link = x.file && x.line && !x.external && app.project.files.includes(x.file);
    return el('div', { className: `item ${cls}${link ? ' link' : ''}`, title: link ? 'Open this line' : '',
                       onclick: link ? () => app.openFile(x.file, x.line, { flash: true }) : null },
      where(x) ? el('span', { className: 'where' }, where(x)) : null,
      el('span', { className: 'msg' }, x.message),
      x.context ? el('span', { className: 'ctx' }, x.context) : null,
      x.hint ? el('span', { className: 'hint' }, x.hint) : null);
  };
  $('log').replaceChildren(...errors.map(e => item('e', e)), ...warnings.map(w => item('w', w)),
                           el('pre', {}, r.log_tail || ''));
  if (errors.length || (!r.success && !r.stopped)) openLog(true);
  app.markErrors();
  app.refreshChangesIfOpen?.();
}

function openLog(open) {
  $('log').classList.toggle('open', open);
  $('logHint').textContent = open ? 'click to collapse' : 'click to expand';
}
$('logBar').onclick = () => openLog(!$('log').classList.contains('open'));
$('compileBtn').onclick = () => compile();
$('stopBtn').onclick = stop;
$('saveBtn').onclick = () => app.save();

// Compiles started elsewhere (the start-up compile, another tab, latexmk -pvc in a terminal) and edits made by other
// programs. The PDF is loaded only when the server says it is complete, never half-written.
async function poll() {
  app.checkExternalChange();
  const s = await api('/api/status');
  if (!s.ok) { if (s.status === 0) setStatus('the editor server is not running', 'err'); return; }
  if (s.running) { if (!state.compiling) { running(true); setStatus('compiling…', 'run'); } return; }
  if (!state.compiling) running(false);
  if (queued && !state.compiling) { queued = false; compile(); return; }
  if (s.finished && s.finished !== state.lastResult?.finished && !state.compiling) showResult(s);
  if (s.pdf_complete && s.pdf_mtime && s.pdf_mtime !== app.pdf.mtime && !app.pdf.rendering) app.loadPdf();
}
setInterval(poll, 3000);

Object.assign(app, { compile, showResult });
