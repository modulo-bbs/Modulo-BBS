#!/usr/bin/env python3
"""
Modulo BBS - Entry Point
Run the server: python run_server.py
Run with options: python run_server.py --host 0.0.0.0 --port 6400 --ssh-port 6422 --nodes 16

Configuration is read from config.yaml (if present) using PyYAML when
available, otherwise a tiny built-in fallback parser. Supplied CLI flags
override the corresponding config values.
"""

import asyncio
import logging
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.app import BBSApp
from core.loader import PluginLoader
from server.server import BBSServer

CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def _parse_scalar(v: str):
    """Parse a scalar YAML-ish value (no quoting/type errors allowed)."""
    v = v.strip()
    if not v:
        return None
    low = v.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "none", "~"):
        return None
    if (v[:1] == '"' and v[-1:] == '"') or (v[:1] == "'" and v[-1:] == "'"):
        return v[1:-1]
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return v


def _load_config_fallback(path: Path) -> dict:
    """Tiny YAML-subset parser for config.yaml when PyYAML is unavailable.

    Handles indentation-based ``key: value`` scalars, ``key:`` blocks holding
    nested scalars, and a ``key:`` block consisting of ``- item`` list lines --
    exactly the shape used by config.yaml. Comments (``#``) are stripped.
    """
    toks = []  # (indent, stripped, is_dash)
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        toks.append((len(line) - len(line.lstrip()), stripped, stripped.startswith("-")))

    cfg = {}
    stack = [(0, cfg)]   # (indent, dict) ancestry
    list_target = None    # the list currently receiving dash items
    i = 0
    while i < len(toks):
        indent, stripped, is_dash = toks[i]
        if is_dash:
            if list_target is not None:
                list_target.append(_parse_scalar(stripped[1:].strip()))
            i += 1
            continue
        key, sep, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()
        if not sep:
            i += 1
            continue
        while len(stack) > 1 and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1]
        if value:
            parent[key] = _parse_scalar(value)
            list_target = None
            i += 1
        else:
            next_is_list = (i + 1 < len(toks) and toks[i + 1][1].startswith("-"))
            if next_is_list:
                lst = []
                parent[key] = lst
                list_target = lst
                i += 1
            else:
                child = {}
                parent[key] = child
                stack.append((indent, child))
                list_target = None
                i += 1
    return cfg


def load_config(path: Path = CONFIG_PATH) -> dict:
    """Load config.yaml into a dict, or {} when absent. PyYAML preferred."""
    if not path.is_file():
        return {}
    try:
        import yaml  # noqa: PLC0415 -- optional dependency
        data = yaml.safe_load(path.read_text()) or {}
        return data if isinstance(data, dict) else {}
    except ImportError:  # pragma: no cover - PyYAML absent
        logging.getLogger("run_server").warning(
            "PyYAML not available; using fallback config parser."
        )
        data = _load_config_fallback(path)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 -- malformed YAML must not kill startup
        logging.getLogger("run_server").exception(
            "Failed to parse %s; using defaults.", path
        )
        return {}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    config = load_config()
    server_cfg = config.get("server", {}) if isinstance(config.get("server"), dict) else {}

    host = server_cfg.get("host", "127.0.0.1")
    port = server_cfg.get("telnet_port", 6400)
    ssh_port = server_cfg.get("ssh_port", 6422)
    max_nodes = server_cfg.get("max_nodes", 8)
    plain_text = False
    enable_ssh = False

    # CLI flags override config.
    if '--port' in sys.argv:
        idx = sys.argv.index('--port')
        if idx + 1 < len(sys.argv):
            port = int(sys.argv[idx + 1])

    if '--host' in sys.argv:
        idx = sys.argv.index('--host')
        if idx + 1 < len(sys.argv):
            host = sys.argv[idx + 1]

    if '--nodes' in sys.argv:
        idx = sys.argv.index('--nodes')
        if idx + 1 < len(sys.argv):
            max_nodes = int(sys.argv[idx + 1])

    if '--plain' in sys.argv:
        plain_text = True

    if '--ssh' in sys.argv:
        enable_ssh = True

    if '--ssh-port' in sys.argv:
        idx = sys.argv.index('--ssh-port')
        if idx + 1 < len(sys.argv):
            ssh_port = int(sys.argv[idx + 1])
            enable_ssh = True

    # Build the core application object (config passed to plugins, e.g. the
    # logon sequencer reads logon_sequence), load plugins, then wire servers.
    bbs = BBSApp(max_nodes=max_nodes, config=config)
    bbs.plugins = await PluginLoader().load(bbs)
    # One-time migration: legacy messageboard/chat → unified conversations
    # (idempotent; no-op when index already populated).
    try:
        await bbs.conversations.migrate_legacy()
    except Exception:  # noqa: BLE001 -- migration must not block boot
        logging.getLogger("run_server").exception("conversations migration failed")

    server = BBSServer(bbs=bbs, host=host, port=port, plain_text=plain_text)

    tasks = [server.start()]
    if enable_ssh:
        from server.ssh_server import start_ssh_server
        # SSH shares the same BBSApp (the logon sequencer drives auth and the
        # menu identically over telnet and SSH).
        tasks.append(start_ssh_server(bbs, host=host, port=ssh_port))

    await asyncio.gather(*tasks)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except asyncio.CancelledError:
        # Graceful API-triggered shutdown — stop() cancels serve_forever().
        pass