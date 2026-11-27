// Wrapping the selection in a command such as \textbf{...}, or unwrapping it. Pure functions, tested with
// `node --test tests/`.

// Whether the braces in `s` pair up (escaped \{ and \} do not count).
function balanced(s) {
  let depth = 0;
  for (let i = 0; i < s.length; i++) {
    if (s[i] === '\\') i++;
    else if (s[i] === '{') depth++;
    else if (s[i] === '}' && --depth < 0) return false;
  }
  return depth === 0;
}

// The edit that toggles \cmd{...} around a selection. before: the text of the selection's first line before it;
// selected: the selected text; after: the text of its last line after it.
// Returns { cutBefore, cutAfter, text, select }: replace the cutBefore characters before the selection, the selection
// and the cutAfter characters after it with `text`, then select text.slice(select[0], select[1]).
// - "\cmd{" before and "}" after the selection, or the selection is "\cmd{...}" itself: unwrap it
// - otherwise wrap it, keeping the same text selected (with nothing selected, the cursor goes between the braces)
export function toggleWrap(cmd, before, selected, after) {
  const open = `\\${cmd}{`;
  if (before.endsWith(open) && after.startsWith('}') && balanced(selected))
    return { cutBefore: open.length, cutAfter: 1, text: selected, select: [0, selected.length] };
  const inner = selected.slice(open.length, -1);
  // balanced(selected) too: its last "}" might be an escaped \}, which does not close the group
  if (selected.startsWith(open) && selected.endsWith('}') && balanced(inner) && balanced(selected))
    return { cutBefore: 0, cutAfter: 0, text: inner, select: [0, inner.length] };
  return { cutBefore: 0, cutAfter: 0, text: open + selected + '}', select: [open.length, open.length + selected.length] };
}
