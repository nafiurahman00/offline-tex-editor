// Shared helpers and state. Modules register their public functions on `app` (app.openFile, app.compile, …) and
// call each other through it, so there are no import cycles and the start-up order does not matter.
export const app = {};
export const state = { current: null, lastResult: null, settings: null };
export const project = { root: '', main: 'main.tex', files: [], assets: [] };

export const $ = id => document.getElementById(id);

// el('div', { className: 'x', onclick }, child, 'text', …)
export function el(tag, props, ...children) {
  const e = document.createElement(tag);
  if (props) for (const [k, v] of Object.entries(props)) {
    if (k === 'dataset') Object.assign(e.dataset, v); else if (v !== undefined && v !== null) e[k] = v;
  }
  e.append(...children.filter(c => c !== null && c !== undefined && c !== false));
  return e;
}

export const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} },
};
// Per-project settings: every project is served from the same origin, so key them by the project root.
export const pkey = k => `${k}@${project.root}`;

export async function api(url, opts) {
  try {
    const r = await fetch(url, opts);
    const j = await r.json().catch(() => ({}));
    return { ...j, ok: r.ok, status: r.status };   // the HTTP outcome wins over any "ok" in the reply
  } catch (e) {
    return { ok: false, status: 0, error: 'the editor server is not reachable' };
  }
}
// Every POST is JSON: the server refuses anything else, so another site cannot forge one without a CORS preflight.
export const post = (url, body) => api(url, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                              body: JSON.stringify(body || {}) });

export function setStatus(text, cls, title) {
  const s = $('status'); s.textContent = text; s.className = cls || ''; s.title = title || text;
}

export function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

export const dirOf = p => p.includes('/') ? p.slice(0, p.lastIndexOf('/')) : '';
export const baseName = p => p.slice(p.lastIndexOf('/') + 1);

// A button that asks for a second click before doing something destructive (instead of a blocking dialog).
export function confirmClick(button, question, action, ms = 4000) {
  if (button.dataset.armed) { delete button.dataset.armed; button.textContent = button.dataset.label; button.classList.remove('danger'); action(); return; }
  button.dataset.label = button.textContent; button.dataset.armed = '1';
  button.textContent = question; button.classList.add('danger');
  setTimeout(() => { if (button.dataset.armed) { delete button.dataset.armed; button.textContent = button.dataset.label; button.classList.remove('danger'); } }, ms);
}
