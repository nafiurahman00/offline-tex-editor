// The PDF preview: rendering with a text layer, find in PDF, SyncTeX both ways, page number, zoom, splitters.
import * as pdfjsLib from '../vendor/pdfjs/pdf.min.mjs';
import { $, el, app, state, store, api, setStatus, debounce } from './util.js';
import { normalize, indexText, findAll, matchSpans } from './textindex.js';

pdfjsLib.GlobalWorkerOptions.workerSrc = new URL('../vendor/pdfjs/pdf.worker.min.mjs', import.meta.url).href;
// isEvalSupported: false keeps pdf.js from compiling fonts to code, a defence against malicious PDFs (such as one
// pulled in by \includegraphics). The page's Content-Security-Policy forbids eval anyway.
const OPEN = { isEvalSupported: false };

const pdf = { mtime: null, rendering: false, failures: 0 };
app.pdf = pdf;
let zoom = store.get('zoom', 1.0);

// The PDF on screen: pages, their elements and scales, and per page the draw promise, the indexed text and the
// text layer's spans. A recompile builds a new view, so work for an old one checks `v === view` and stops.
let view = null;
function newView(doc, pages, els, scales) {
  const v = { doc, pages, els, scales, drawn: new Map(), text: new Map(), textReady: [], textDivs: [], painted: [] };
  v.draw = i => { if (!v.drawn.has(i)) v.drawn.set(i, drawPage(v, i).catch(() => {})); return v.drawn.get(i); };
  return v;
}

async function drawPage(v, i) {
  const page = v.pages[i], scale = v.scales[i], vp = page.getViewport({ scale }), dpr = window.devicePixelRatio || 1;
  const c = el('canvas');
  c.width = vp.width * dpr; c.height = vp.height * dpr; c.style.width = vp.width + 'px'; c.style.height = vp.height + 'px';
  await page.render({ canvasContext: c.getContext('2d'), viewport: page.getViewport({ scale: scale * dpr }) }).promise;
  // Text layer: transparent text over the image, so the PDF's text can be selected, copied and searched.
  const t = await pageText(v, i), tl = el('div', { className: 'textLayer' });
  tl.style.setProperty('--scale-factor', scale);
  const layer = new pdfjsLib.TextLayer({ textContentSource: t.content, container: tl, viewport: vp });
  await layer.render();
  v.els[i].replaceChildren(c, tl, ...v.els[i].querySelectorAll('.mark'));
  v.textDivs[i] = layer.textDivs;   // textDivs[k] is the span for t.content.items[k]
  paintFind(v, i);
}

function pageText(v, i) {
  if (!v.text.has(i)) v.text.set(i, v.pages[i].getTextContent().then(content => {
    const t = { content, ...indexText(content.items) }; v.textReady[i] = t; return t;
  }));
  return v.text.get(i);
}

export async function loadPdf() {
  if (pdf.rendering) return;
  pdf.rendering = true;
  const wrap = $('pdfWrap'), keepTop = wrap.scrollTop / Math.max(1, wrap.scrollHeight);
  try {
    const res = await fetch('/pdf?t=' + Date.now());
    if (!res.ok) { pdf.rendering = false; if (res.status === 404 && !view) $('pdfEmpty').textContent = 'No PDF yet — press Recompile.'; return; }
    const data = await res.arrayBuffer();
    const doc = await pdfjsLib.getDocument({ data, ...OPEN }).promise;
    const box = $('pdf'), frag = document.createDocumentFragment(), els = [], scales = [], pages = [];
    const avail = wrap.clientWidth - 28;
    // 1) lay out every page (cheap), 2) draw the pages near the old scroll position before swapping,
    // 3) swap, 4) draw the rest in the background.
    for (let i = 1; i <= doc.numPages; i++) {
      const page = await doc.getPage(i), base = page.getViewport({ scale: 1 });
      const scale = (avail / base.width) * zoom, vp = page.getViewport({ scale });
      const div = el('div', { className: 'page', dataset: { page: i } });
      div.style.width = vp.width + 'px'; div.style.height = vp.height + 'px';
      frag.appendChild(div); els.push(div); scales.push(scale); pages.push(page);
    }
    const v = newView(doc, pages, els, scales);
    const totalH = scales.reduce((a, s, i) => a + pages[i].getViewport({ scale: s }).height + 12, 12);
    let y = 12, first = 0; const target = keepTop * totalH;
    for (let i = 0; i < pages.length; i++) { const h = pages[i].getViewport({ scale: scales[i] }).height + 12; if (y + h > target) { first = i; break; } y += h; }
    const near = [first, first + 1, first + 2].filter(i => i < pages.length);
    for (const i of near) await v.draw(i);
    const old = view;
    box.replaceChildren(frag); view = v;
    wrap.scrollTop = keepTop * wrap.scrollHeight;
    $('pageCount').textContent = doc.numPages; updatePageNum();
    const s = await api('/api/status'); pdf.mtime = s.pdf_mtime; pdf.failures = 0;
    pdf.rendering = false;
    old?.doc.destroy();   // free the previous document in the pdf.js worker
    if (find.query) runFind(false);   // same search on the new PDF, without moving the view
    for (let i = 0; i < pages.length; i++) if (view === v) await v.draw(i);
  } catch (e) {
    // A PDF being replaced at this moment can fail to parse; the next poll retries. Report repeated failures only.
    if (++pdf.failures >= 3) { console.error('PDF render failed', e); setStatus('PDF render failed: ' + e.message, 'err'); }
  }
  pdf.rendering = false;
}

// The main document changed: forget the old PDF.
export function resetPdf() {
  view?.doc.destroy(); view = null; pdf.mtime = null;
  $('pdf').replaceChildren(el('div', { id: 'pdfEmpty' }, 'No PDF yet.'));
  $('pageCount').textContent = '–'; $('pageNum').value = '–';
}

// First page of a PDF file, for the file preview.
export async function renderPdfPage(url) {
  const doc = await pdfjsLib.getDocument({ url, ...OPEN }).promise;
  try {
    const page = await doc.getPage(1), base = page.getViewport({ scale: 1 });
    const scale = Math.min(2, (Math.min(900, $('previewBody').clientWidth - 32)) / base.width), dpr = window.devicePixelRatio || 1;
    const vp = page.getViewport({ scale: scale * dpr }), c = el('canvas');
    c.width = vp.width; c.height = vp.height; c.style.width = vp.width / dpr + 'px';
    await page.render({ canvasContext: c.getContext('2d'), viewport: vp }).promise;
    return c;
  } finally { setTimeout(() => doc.destroy(), 0); }
}

$('zoomIn').onclick = () => { zoom = Math.min(4, zoom * 1.15); store.set('zoom', zoom); loadPdf(); };
$('zoomOut').onclick = () => { zoom = Math.max(0.3, zoom / 1.15); store.set('zoom', zoom); loadPdf(); };
const refitPdf = debounce(() => {
  if (pdf.rendering) refitPdf(); else loadPdf();
}, 300);
window.addEventListener('resize', refitPdf);

// ---------- page number ----------
const pageEls = () => view ? view.els : [];
function updatePageNum() {
  const els = pageEls();
  if (!els.length || document.activeElement === $('pageNum')) return;
  const wrap = $('pdfWrap'), mid = wrap.scrollTop + wrap.clientHeight / 2;
  let cur = 1;
  for (let i = 0; i < els.length; i++) { if (els[i].offsetTop <= mid) cur = i + 1; else break; }
  $('pageNum').value = cur;
}
let pageT; $('pdfWrap').addEventListener('scroll', () => { if (!pageT) pageT = requestAnimationFrame(() => { pageT = 0; updatePageNum(); }); });
$('pageNum').addEventListener('keydown', ev => {
  if (ev.key === 'Enter') {
    const n = parseInt($('pageNum').value, 10), els = pageEls();
    if (n >= 1 && n <= els.length) $('pdfWrap').scrollTop = els[n - 1].offsetTop - 12;
    $('pageNum').blur();
  } else if (ev.key === 'Escape') $('pageNum').blur();
});
$('pageNum').addEventListener('focus', () => $('pageNum').select());
$('pageNum').addEventListener('blur', updatePageNum);

// ---------- SyncTeX: PDF -> source (double-click), source -> PDF (button / Ctrl+J) ----------
$('pdf').addEventListener('dblclick', async ev => {
  const div = ev.target.closest('.page'); if (!div || !view) return;
  window.getSelection().removeAllRanges();   // the double-click also selected a word in the text layer
  const i = +div.dataset.page, rect = div.getBoundingClientRect(), s = view.scales[i - 1];
  const x = (ev.clientX - rect.left) / s, y = (ev.clientY - rect.top) / s;
  const r = await api(`/api/synctex/edit?page=${i}&x=${x.toFixed(1)}&y=${y.toFixed(1)}`);
  if (r.ok && r.path) app.openFile(r.path, r.line, { flash: true }); else setStatus(r.error || 'no source for that spot', 'err');
});
export async function syncToPdf() {
  if (!state.current || !view) return;
  const line = app.cm.getCursor().line + 1;
  const r = await api(`/api/synctex/view?path=${encodeURIComponent(state.current)}&line=${line}`);
  if (!r.ok) return setStatus(r.error || 'no PDF location (compile first?)', 'err');
  const div = view.els[r.page - 1]; if (!div) return;
  const y = r.y * view.scales[r.page - 1];
  $('pdfWrap').scrollTop = div.offsetTop + y - $('pdfWrap').clientHeight / 3;
  const m = el('div', { className: 'mark' }); m.style.top = (y - 14) + 'px'; div.appendChild(m);
  setTimeout(() => { m.style.opacity = 0; }, 900); setTimeout(() => m.remove(), 2200);
}
$('syncBtn').onclick = syncToPdf;

// ---------- splitters ----------
(() => {
  const main = document.querySelector('main'), side = $('side'), sideSplit = $('sideSplit');
  const editor = $('editorWrap'), split = $('split');
  const minSide = 200, savedSide = store.get('sidebarWidth', 264);
  let preferredSide = Number.isFinite(savedSide) ? savedSide : 264;
  function setSideWidth(width) {
    const max = Math.max(minSide, Math.floor(main.clientWidth * .45));
    const clamped = Math.min(max, Math.max(minSide, Math.round(width)));
    main.style.setProperty('--side-width', `${clamped}px`);
    sideSplit.setAttribute('aria-valuemin', minSide);
    sideSplit.setAttribute('aria-valuemax', max);
    sideSplit.setAttribute('aria-valuenow', clamped);
    return clamped;
  }
  setSideWidth(preferredSide);
  window.addEventListener('resize', () => setSideWidth(preferredSide));

  function resizeSide(width) {
    preferredSide = setSideWidth(width);
    store.set('sidebarWidth', preferredSide);
  }
  function setEditorShare(value) {
    const share = Math.min(.8, Math.max(.2, value));
    main.style.setProperty('--editor-fr', `${share}fr`);
    main.style.setProperty('--pdf-fr', `${1 - share}fr`);
    return share;
  }
  const savedSplit = store.get('split', .5);
  setEditorShare(Number.isFinite(savedSplit) ? savedSplit : .5);

  let activePointer = null;
  function dragDivider(handle, resize) {
    let offset;
    handle.onpointerdown = e => {
      if (!e.isPrimary || e.button !== 0 || activePointer !== null) return;
      offset = e.clientX - handle.getBoundingClientRect().left;
      handle.setPointerCapture(e.pointerId);
      activePointer = e.pointerId;
      document.body.style.userSelect = 'none';
    };
    handle.onpointermove = e => {
      if (e.pointerId !== activePointer) return;
      resize(e.clientX - offset);
      app.cm.refresh();
    };
    handle.onlostpointercapture = e => {
      if (e.pointerId !== activePointer) return;
      activePointer = null;
      document.body.style.userSelect = '';
      refitPdf();
    };
  }
  dragDivider(sideSplit, x => resizeSide(x - main.getBoundingClientRect().left));
  dragDivider(split, x => {
    const left = editor.getBoundingClientRect().left;
    const available = main.getBoundingClientRect().right - left - split.offsetWidth;
    store.set('split', setEditorShare((x - left) / available));
  });
  sideSplit.onkeydown = e => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault();
    resizeSide(side.getBoundingClientRect().width + (e.key === 'ArrowLeft' ? -16 : 16));
    app.cm.refresh();
    refitPdf();
  };
})();

// ---------- find in PDF ----------
const find = { query: '', matches: [], cur: -1, view: null, run: 0 };

// Wrap this page's matches in highlight spans inside the text layer, after undoing the previous paint.
function paintFind(v, i) {
  const divs = v.textDivs[i], t = v.textReady[i]; if (!divs || !t) return;
  for (const k of v.painted[i] || []) divs[k].textContent = t.content.items[k].str;
  const ranges = new Map();
  if (find.view === v) find.matches.forEach((m, n) => {
    if (m.page !== i) return;
    for (const [k, [a, b]] of matchSpans(t.map, m.start, m.end)) { if (!ranges.has(k)) ranges.set(k, []); ranges.get(k).push([a, b, n === find.cur]); }
  });
  for (const [k, rs] of ranges) {
    const s = t.content.items[k].str, parts = []; let pos = 0;
    rs.sort((x, y) => x[0] - y[0]);
    for (const [a, b, cur] of rs) {
      if (a < pos) continue;
      if (a > pos) parts.push(document.createTextNode(s.slice(pos, a)));
      parts.push(el('span', { className: cur ? 'hl cur' : 'hl' }, s.slice(a, b))); pos = b;
    }
    if (pos < s.length) parts.push(document.createTextNode(s.slice(pos)));
    divs[k].replaceChildren(...parts);
  }
  v.painted[i] = [...ranges.keys()];
}
function showFindCount() {
  const n = find.matches.length, c = $('findCount');
  c.textContent = !find.query ? '' : n ? `${find.cur + 1} of ${n}` : 'No results';
  c.className = find.query && !n ? 'none' : '';
}
// Search every page. jump: the query changed, so select the first match from the visible page on and scroll to
// it; otherwise (a recompile) keep the current match number and leave the view where it is.
async function runFind(jump) {
  const v = view, q = normalize($('findInput').value).trim(), run = ++find.run, prev = find.cur;
  const prevView = find.view, prevPages = new Set(find.matches.map(m => m.page));
  const matches = [];
  if (v && q) for (let i = 0; i < v.pages.length; i++) {
    const t = await pageText(v, i);
    if (run !== find.run || v !== view) return;   // a newer search or a new PDF took over
    for (const m of findAll(t.text, q)) matches.push({ page: i, ...m });
  }
  Object.assign(find, { query: q, matches, view: v });
  const visible = (parseInt($('pageNum').value, 10) || 1) - 1;
  find.cur = !matches.length ? -1 : jump ? Math.max(0, matches.findIndex(m => m.page >= visible)) : Math.min(Math.max(prev, 0), matches.length - 1);
  if (prevView === v) prevPages.forEach(i => paintFind(v, i));   // clear the old highlights
  if (v) new Set(matches.map(m => m.page)).forEach(i => paintFind(v, i));
  showFindCount();
  if (jump && matches.length) gotoMatch(find.cur);
}
async function gotoMatch(n) {
  const v = find.view, total = find.matches.length; if (!v || v !== view || !total) return;
  const old = find.matches[find.cur]; find.cur = (n + total) % total;
  const m = find.matches[find.cur]; showFindCount();
  if (old && old.page !== m.page) paintFind(v, old.page);
  await v.draw(m.page); if (v !== view) return;
  paintFind(v, m.page);
  const e = v.els[m.page].querySelector('.hl.cur'), wrap = $('pdfWrap'); if (!e) return;
  const r = e.getBoundingClientRect(), w = wrap.getBoundingClientRect();
  if (r.top < w.top + 50 || r.bottom > w.bottom - 20) wrap.scrollTop += r.top - w.top - wrap.clientHeight / 3;
  if (r.left < w.left || r.right > w.right) wrap.scrollLeft += r.left - w.left - wrap.clientWidth / 3;
}
export function openFind() {
  $('pdfFind').hidden = false; $('findInput').focus(); $('findInput').select();
  if ($('findInput').value.trim() && !find.query) runFind(true);
}
function closeFind() {
  $('pdfFind').hidden = true; find.run++;
  const v = find.view, pages = new Set(find.matches.map(m => m.page));
  Object.assign(find, { query: '', matches: [], cur: -1 });
  if (v) pages.forEach(i => paintFind(v, i));
  showFindCount();
}
let findT;
$('findInput').addEventListener('input', () => { clearTimeout(findT); findT = setTimeout(() => runFind(true), 200); });
$('findInput').addEventListener('keydown', e => {
  if (e.key === 'Enter') {
    e.preventDefault(); clearTimeout(findT);
    if (normalize($('findInput').value).trim() !== find.query || find.view !== view) return runFind(true);
    gotoMatch(find.cur + (e.shiftKey ? -1 : 1));
  } else if (e.key === 'Escape') { e.preventDefault(); closeFind(); }
});
$('findNext').onclick = () => gotoMatch(find.cur + 1);
$('findPrev').onclick = () => gotoMatch(find.cur - 1);
$('findClose').onclick = closeFind;
$('findBtn').onclick = openFind;

Object.assign(app, { loadPdf, resetPdf, renderPdfPage, syncToPdf, openFind });
