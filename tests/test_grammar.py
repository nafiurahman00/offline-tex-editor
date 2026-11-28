"""Checks for the opt-in local grammar launcher. No checker dependency by default."""

import unittest
from unittest.mock import patch

from offline_tex_editor import grammar, server


class GrammarLaunchTests(unittest.TestCase):
    def test_normal_launch_does_not_enable_grammar(self):
        with patch.object(server, 'run_editor') as run:
            server.main(['--no-initial-compile'])
            self.assertFalse(run.call_args[0][0].grammar)
            self.assertNotIn('/api/grammar/check', server.POST_ROUTES)

    def test_missing_checker_has_actionable_error(self):
        with patch.object(grammar.importlib.util, 'find_spec', return_value=None), \
                patch.object(grammar.Path, 'is_file', return_value=False):
            with self.assertRaisesRegex(SystemExit, 'pip install -e'):
                server.main(['--grammar'])

    def test_local_checker_is_active_during_launch_and_restored_on_failure(self):
        original_handler = server.Handler
        original_path = list(grammar.sys.path)

        def run(args):
            self.assertTrue(args.grammar)
            result = server.POST_ROUTES['/api/grammar/check']({'text': 'They is ready.'})
            self.assertIn('are', result['diagnostics'][0]['replacements'])
            self.assertIn('grammar-static', server.STATIC)
            self.assertIsNot(server.Handler, original_handler)
            raise RuntimeError('startup interrupted')

        # Force discovery through the neighboring private checkout, as start.sh
        # does with the system Python rather than the checker's virtualenv.
        checkout = grammar.Path(grammar.__file__).resolve().parents[2] / 'offline-tex-grammar'
        if not (checkout / 'offline_tex_grammar' / '__init__.py').is_file():
            self.skipTest('neighboring private grammar checkout is absent')
        with patch.object(grammar.importlib.util, 'find_spec', return_value=None), \
                patch.object(server, 'run_editor', side_effect=run):
            with self.assertRaisesRegex(RuntimeError, 'startup interrupted'):
                server.main(['--grammar', '--no-initial-compile'])
        self.assertIs(server.Handler, original_handler)
        self.assertNotIn('/api/grammar/check', server.POST_ROUTES)
        self.assertEqual(grammar.sys.path, original_path)


if __name__ == '__main__':
    unittest.main()
