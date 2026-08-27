"""
Tests for the Plugin base class in plugins/base.py.
"""

import unittest

from plugins.base import Plugin


class _ConcretePlugin(Plugin):
    """A minimal plugin subclass exercising the required metadata."""

    name = "testplugin"
    version = "1.2.3"
    description = "A test plugin"
    menu_label = "[T] Test"
    menu_key = "T"
    menu_order = 5


class PluginBaseTest(unittest.TestCase):
    def test_import_and_subclass(self):
        self.assertTrue(issubclass(_ConcretePlugin, Plugin))

    def test_metadata_attributes_present_and_typed(self):
        p = _ConcretePlugin()
        self.assertEqual(p.name, "testplugin")
        self.assertEqual(p.version, "1.2.3")
        self.assertEqual(p.description, "A test plugin")
        self.assertEqual(p.menu_label, "[T] Test")
        self.assertEqual(p.menu_key, "T")
        self.assertEqual(p.menu_order, 5)
        # Menu ordering is numeric so it can be sorted directly.
        self.assertIsInstance(p.menu_order, int)

    def test_defaults_when_not_overridden(self):
        p = Plugin()
        self.assertEqual(p.name, "")
        self.assertEqual(p.version, "0.0.0")
        self.assertTrue(callable(p.on_load))
        self.assertTrue(callable(p.on_unload))
        self.assertTrue(callable(p.on_session_start))
        self.assertTrue(callable(p.on_session_end))
        self.assertTrue(callable(p.handle_command))
        self.assertTrue(callable(p.render_home_pane))
        self.assertTrue(callable(p.handle_home_key))
        self.assertTrue(callable(p.home_digest))

    def test_lifecycle_hooks_are_no_ops_by_default(self):
        p = _ConcretePlugin()
        # None of the lifecycle hooks should raise with a bare call.
        p.on_load(None)
        p.on_unload()
        p.on_session_start(None)
        p.on_session_end(None)
        # handle_command defaults to False (leave / pass control back).
        self.assertFalse(p.handle_command(None, "anything"))
        self.assertEqual(p.render_home_pane(None), "")
        self.assertFalse(p.handle_home_key(None, "ENTER"))
        self.assertIsNone(p.home_digest(None))
        self.assertIsNone(p.on_session_start(None))

    def test_lifecycle_hooks_are_overrideable(self):
        events = []

        class SpyPlugin(_ConcretePlugin):
            def on_load(self, bbs):
                events.append(("load", bbs))

            def on_session_start(self, session):
                events.append(("start", session))

            def on_session_end(self, session):
                events.append(("end", session))

            def handle_command(self, session, command):
                events.append(("cmd", command))
                return True

        p = SpyPlugin()
        p.on_load("bbs")
        p.on_session_start("s1")
        self.assertTrue(p.handle_command("s1", "ls"))
        p.on_session_end("s1")
        self.assertEqual(
            events,
            [
                ("load", "bbs"),
                ("start", "s1"),
                ("cmd", "ls"),
                ("end", "s1"),
            ],
        )


if __name__ == "__main__":
    unittest.main()