// File names as the main document sees them. Pure functions, tested with `node --test tests/`.
//
// LaTeX runs in the main document's folder, so \input, \includegraphics and \bibliography name project files
// relative to it: with paper/main.tex, paper/figures/x.png is written figures/x.png, and notes.tex at the top of the
// project ../notes.tex.

// `path` (relative to the project folder) as seen from the folder of `main` (also relative to the project folder).
export function fromMain(path, main) {
  const base = main.split('/').slice(0, -1), parts = path.split('/');
  let same = 0;   // folders shared by both; the last part of `path` is its file name, never a folder
  while (same < base.length && same < parts.length - 1 && base[same] === parts[same]) same++;
  return [...Array(base.length - same).fill('..'), ...parts.slice(same)].join('/');
}
