"""
Plugin base class — the interface contract every Modulo BBS plugin implements.

A plugin is a self-contained component (message board, file area, bulletin, auth
flow, door game, ...) that registers with the core. This class defines the
shape a plugin must present; subclasses override the class attributes and
lifecycle hooks as needed. The class is intentionally dependency-free so it
can be imported standalone by the plugin loader.

Contract
--------
Attributes
    Every subclass MUST define these metadata attributes, which the core
    reads to register and display the plugin::

        name         unique, stable identifier, e.g. "messageboard"
        version      semantic version, e.g. "1.0.0"
        description  one-line human-readable summary
        menu_label   display text shown in the main menu ("[M] Message Board")
        menu_key     single-character hotkey ("M")
        menu_order   sort position in the main menu (lower = higher)

    ``menu_label`` and ``menu_order`` default to sensible values if a plugin
    overrides them; ``name`` and ``version`` must be provided.

    Optional home-strip attributes (used by the shipped mainmenu chrome)::

        home_label   tab text when this plugin is listed in mainmenu's home file
        render_home_pane / handle_home_key / home_digest
                     see the method docs below; defaults are no-ops

Lifecycle
    The core drives a plugin through its lifecycle:
        on_load           called once at startup; register events/handlers
        on_unload         called when the plugin is being removed/shutdown
        on_session_start  called when a user enters the plugin
        on_session_end    called when a user leaves the plugin
        handle_command    called for each command while the plugin is active;
                          return True to stay active, False to return to the menu

    All lifecycle hooks are optional — the defaults are safe no-ops, so a
    plugin only overrides the ones it needs.

Async/Sync
    Every hook may be written as either ``def`` or ``async def``. The core
    awaits any coroutine a hook returns (the "await-if-coroutine" rule) at
    every call site: plugin loading, session start/end, the command loop,
    and event-bus handlers. Write ``async def`` when you need to ``await``
    something (bbs.send, bbs.users.*, storage I/O); write plain ``def``
    otherwise. Never block inside a hook (no time.sleep, no synchronous
    disk/network I/O beyond trivial file reads) — that stalls every node,
    not just yours. CPU-heavy work (bcrypt, large parsing) must go through
    ``asyncio.to_thread``.
"""

from typing import Any, Awaitable


class Plugin:
    """Base class for all Modulo BBS plugins.

    Every hook below may be overridden as ``def`` or ``async def``; core
    awaits coroutines at every call site ("await-if-coroutine"). The
    annotations express that: e.g. ``handle_command`` returns ``bool`` or
    an awaitable resolving to ``bool``.
    """

    # Metadata (subclasses must set name and version)
    name: str = ""             # Unique identifier ("messageboard")
    version: str = "0.0.0"     # Semver ("1.0.0")
    description: str = ""      # Human-readable description
    menu_label: str = ""       # Display text ("[M] Message Board")
    menu_key: str = ""         # Hotkey ("M")
    menu_order: int = 100      # Sort order in main menu (lower = higher)
    # Group gate for appearing in menus at all (evaluated with
    # user.can_access(); empty/None = visible to everyone).
    menu_requires: list[str] | None = None
    # Tab text when listed in plugins/mainmenu/data/home. Empty = use ``name``.
    home_label: str = ""

    def on_load(self, bbs: Any) -> "None | Awaitable[None]":
        """Called once at startup. Register event handlers and resources.

        May be defined as sync or async.

        Args:
            bbs: The core BBS server object (event bus, storage, etc.).
        """

    def on_unload(self) -> "None | Awaitable[None]":
        """Called when the plugin is being removed or the server shuts down.
        Release any resources the plugin acquired during :meth:`on_load`."""

    def on_session_start(self, session: Any) -> "None | Awaitable[Any]":
        """Called when a user connects / enters this plugin.

        Args:
            session: The active BBS session.
        """

    def on_session_end(self, session: Any) -> "None | Awaitable[None]":
        """Called when a user disconnects / leaves this plugin.

        Args:
            session: The session that is ending.
        """

    def handle_command(
        self, session: Any, command: str
    ) -> "bool | Awaitable[bool]":
        """Handle a command while this plugin is active.

        May be defined as sync or async; return (or resolve to) True to
        stay in the plugin, False to return to the menu.

        Args:
            session: The active BBS session.
            command: The raw command line entered by the user.

        Returns:
            True to stay in the plugin, False to return to the menu.
        """
        return False

    def render_home_pane(self, session: Any) -> "str | Awaitable[str]":
        """Middle pane for the shipped mainmenu chrome (no tab row, no prompt).

        Return the pane text, or ``""`` if this plugin has no home view.
        """
        return ""

    def handle_home_key(
        self, session: Any, key: str
    ) -> "bool | Awaitable[bool]":
        """Handle a key while this plugin's home tab is active.

        Tab switch, ``/``, and ``Q`` never reach here. Return True if the
        key was consumed.
        """
        return False

    def home_digest(self, session: Any) -> Any:
        """Optional dashboard rows: ``(text, jump_id)`` or a list of those.

        ``jump_id`` is a plugin name the dashboard Enter key should open.
        Return None if this plugin has nothing to summarize.
        """
        return None