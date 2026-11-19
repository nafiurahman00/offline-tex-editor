// Searchable text of a PDF page, from pdf.js text items. Pure functions, tested with `node --test tests/`.
//
// The text is lower-cased, compatibility characters are expanded (NFKC, so "ﬁ" and math italic letters match plain
// text), runs of white space become one space, and line ends are joined, dropping the hyphen of a word broken
// across lines, so "exam-" + "ple" matches "example". map[n] = [item, from, to] gives the characters of text item
// `item` that produced text[n]; null for a space inserted at a line end.

export const normalize = s => s.normalize('NFKC').toLowerCase().replace(/\s+/g, ' ');

export function indexText(items) {
  let text = '';
  const map = [];
  const add = (ch, src) => {
    if (/\s/.test(ch)) { if (!text || text.endsWith(' ')) return; ch = ' '; }
    text += ch; map.push(src);
  };
  items.forEach((it, k) => {
    let o = 0;
    for (const cp of it.str || '') { for (const ch of normalize(cp)) add(ch, [k, o, o + cp.length]); o += cp.length; }
    if (it.hasEOL) {
      if (text.endsWith('-') && /\p{L}/u.test(text.slice(-2, -1))) { text = text.slice(0, -1); map.pop(); }
      else add(' ', null);
    }
  });
  return { text, map };
}

// Every match of an already-normalized query: [{ start, end }] offsets into `text`.
export function findAll(text, q) {
  const out = [];
  if (!q) return out;
  for (let at = text.indexOf(q); at !== -1; at = text.indexOf(q, at + q.length)) out.push({ start: at, end: at + q.length });
  return out;
}

// The characters of each text item that the match [start, end) covers: Map item -> [from, to].
export function matchSpans(map, start, end) {
  const out = new Map();
  for (let n = start; n < end; n++) {
    const src = map[n]; if (!src) continue;
    const [k, a, b] = src, r = out.get(k);
    if (r) { r[0] = Math.min(r[0], a); r[1] = Math.max(r[1], b); } else out.set(k, [a, b]);
  }
  return out;
}
