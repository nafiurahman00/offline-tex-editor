// Selecting several files and folders in the file list. Pure functions, tested with `node --test tests/`.

// Shift+click: the paths from `a` to `b` (both included) in the order the list shows them. Without `a` (or when it
// is no longer shown), just `b`.
export function rangeBetween(order, a, b) {
  const i = order.indexOf(a), j = order.indexOf(b);
  if (j < 0) return [];
  if (i < 0) return [b];
  return order.slice(Math.min(i, j), Math.max(i, j) + 1);
}

// The selected paths without those inside a selected folder, which go with their folder (deleting the folder and then
// a file in it would fail, and a .zip would hold the file twice).
export function topmost(paths) {
  const all = new Set(paths);
  return paths.filter(p => !p.split('/').slice(0, -1).some((_, i, parts) => all.has(parts.slice(0, i + 1).join('/'))));
}
