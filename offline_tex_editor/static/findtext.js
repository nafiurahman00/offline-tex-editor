// Find and replace: building the pattern and expanding replacements. Pure functions, tested with `node --test`.
// The server's project-wide replace (project.py) follows the same rules.

// A RegExp for the query, or { error } for an invalid regular expression, or null for an empty query.
// wholeWord: letters, digits and _ must not touch the match on either side.
export function buildRegExp(text, { regex = false, caseSensitive = false, wholeWord = false } = {}) {
  if (!text) return null;
  let src = regex ? text : text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  let flags = 'gm' + (caseSensitive ? '' : 'i');
  if (wholeWord) { src = `(?<![\\p{L}\\p{N}_])(?:${src})(?![\\p{L}\\p{N}_])`; flags += 'u'; }
  try { return new RegExp(src, flags); } catch (e) { return { error: e.message.replace(/^Invalid regular expression: /, '') }; }
}

// Every non-empty match on one line: [{ ch, len, m }] (m is the match array, for $1 in replacements).
export function lineMatches(rx, line) {
  const out = [];
  rx.lastIndex = 0;
  for (let m = rx.exec(line); m; m = rx.exec(line)) {
    if (m[0].length) out.push({ ch: m.index, len: m[0].length, m });
    else rx.lastIndex = m.index + 1;   // skip empty matches (^, a*) instead of looping on them
    if (rx.lastIndex > line.length) break;
  }
  return out;
}

// The replacement for one regular-expression match. As in JavaScript and most editors: $1…$99 insert a group,
// $& the whole match, $<name> a named group, $$ a dollar sign. Everything else, backslashes included, is literal,
// so "\textbf{$1}" works as written.
export function expandReplacement(template, m) {
  return template.replace(/\$(\$|&|\d{1,2}|<([^>]*)>)/g, (all, tok, name) => {
    if (tok === '$') return '$';
    if (tok === '&') return m[0];
    if (name !== undefined) return m.groups && name in m.groups ? (m.groups[name] ?? '') : all;
    const n = parseInt(tok, 10);
    if (n >= 1 && n < m.length) return m[n] ?? '';
    const one = parseInt(tok[0], 10);   // "$10" with fewer than 10 groups: group 1, then "0"
    if (tok.length === 2 && one >= 1 && one < m.length) return (m[one] ?? '') + tok[1];
    return all;
  });
}
