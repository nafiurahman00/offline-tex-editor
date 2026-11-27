// Where a cross-reference command wants a label. Pure functions, tested with `node --test tests/`.

// \ref, \eqref, \pageref, \autoref, \nameref, \cref, \Cref, \vref, \labelcref, ... (any command ending in "ref"),
// but not \href{URL} or \hyperref{URL}{...}, whose braces hold a web address.
const REF = /\\(?!h(?:yper)?ref\b)[a-zA-Z]*ref\*?\{([^{}]*)$/;
// \hyperref[label]{text}: the label is in square brackets
const HYPERREF = /\\hyperref\[([^\]]*)$/;

// For the text of a line up to the cursor: { typed, close } when the cursor is in a label argument (typed: what is
// already there, close: the character that ends the argument), or null.
export function refArgument(before) {
  let m;
  if ((m = before.match(REF))) return { typed: m[1], close: '}' };
  if ((m = before.match(HYPERREF))) return { typed: m[1], close: ']' };
  return null;
}
