"""Opt-in local integration with the separately maintained grammar checker."""

import importlib.util
import sys
from contextlib import ExitStack, contextmanager
from pathlib import Path


@contextmanager
def integration():
    """Load the installed checker, or the sibling checkout for local testing."""
    from . import server

    with ExitStack() as stack:
        if importlib.util.find_spec('offline_tex_grammar') is None:
            checkout = Path(__file__).resolve().parents[2] / 'offline-tex-grammar'
            if not (checkout / 'offline_tex_grammar' / '__init__.py').is_file():
                raise SystemExit('--grammar needs the offline-tex-grammar package. Install the private checkout '
                                 'with pip install -e /path/to/offline-tex-grammar, or place it next to this editor checkout.')
            location = str(checkout)
            sys.path.insert(0, location)
            stack.callback(sys.path.remove, location)

        from offline_tex_grammar.editor import integration as extend_editor
        with extend_editor(server):
            yield
