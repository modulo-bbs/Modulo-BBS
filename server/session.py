"""
Session management for BBS connections.
Each telnet connection gets a Session object that tracks state,
user info, and node assignment.
"""

from __future__ import annotations

import asyncio
import time
from enum import Enum, auto
from dataclasses import dataclass, field


class SessionState(Enum):
    """States a connection goes through."""
    CONNECTED = auto()      # TCP connected, pre-login
    NEGOTIATING = auto()    # Telnet negotiation in progress
    LOGIN = auto()          # Awaiting credentials
    MAIN_MENU = auto()      # At the main menu
    READING = auto()        # Reading messages
    WRITING = auto()        # Composing a message
    FILE_AREA = auto()      # In file section
    DOOR = auto()           # Running a door game
    DISCONNECTED = auto()   # Connection closed


@dataclass
class Session:
    """Represents a single BBS connection session."""
    
    session_id: str
    node_id: int
    address: tuple[str, int]  # (host, port)
    connected_at: float = field(default_factory=time.time)
    
    # State
    state: SessionState = SessionState.CONNECTED
    authenticated: bool = False
    username: str = ""
    # The authenticated User (set by the login/auth plugin on success).
    user: User | None = None
    
    # Terminal info
    terminal_width: int = 80
    terminal_height: int = 24
    terminal_type: str = "UNKNOWN"
    # Wire character codec for this session ("cp437" | "utf-8" | "ascii").
    # Default matches ANSI-BBS/Syncterm; login flow refines it via
    # detection (shared.codecs.detect_codec) and user preference.
    codec: str = "cp437"
    
    # Transport references (set when connection is fully established)
    reader: asyncio.StreamReader | None = None
    writer: asyncio.StreamWriter | None = None
    # Telnet negotiation state for this session (telnet transport sets this;
    # SSH leaves it None so input passes straight through as clean text).
    negotiator: object | None = None
    
    # Activity tracking
    last_activity: float = field(default_factory=time.time)
    bytes_sent: int = 0
    bytes_received: int = 0
    commands_issued: int = 0
    
    @property
    def is_active(self) -> bool:
        return self.state != SessionState.DISCONNECTED
    
    @property
    def idle_seconds(self) -> float:
        return time.time() - self.last_activity
    
    @property
    def session_duration(self) -> float:
        return time.time() - self.connected_at
    
    def touch(self):
        """Update last activity timestamp."""
        self.last_activity = time.time()
    
    def to_dict(self) -> dict:
        """Serialize session info for display/admin."""
        return {
            "session_id": self.session_id,
            "node": self.node_id,
            "address": f"{self.address[0]}:{self.address[1]}",
            "state": self.state.name,
            "authenticated": self.authenticated,
            "username": self.username or "(anonymous)",
            "terminal": f"{self.terminal_type} ({self.terminal_width}x{self.terminal_height})",
            "connected": f"{self.session_duration:.0f}s ago",
            "idle": f"{self.idle_seconds:.0f}s",
            "bytes_sent": self.bytes_sent,
            "bytes_received": self.bytes_received,
        }


class SessionManager:
    """Manages all active sessions and node assignment."""
    
    def __init__(self, max_nodes: int = 8):
        self.max_nodes = max_nodes
        self.sessions: dict[str, Session] = {}  # session_id -> Session
        self._next_node = 1
        self._lock = asyncio.Lock()
    
    async def create_session(self, session_id: str, address: tuple[str, int],
                             reader: asyncio.StreamReader,
                             writer: asyncio.StreamWriter) -> Session:
        """Create a new session and assign a node."""
        async with self._lock:
            node_id = self._assign_node()
            session = Session(
                session_id=session_id,
                node_id=node_id,
                address=address,
                reader=reader,
                writer=writer,
            )
            self.sessions[session_id] = session
            return session
    
    async def remove_session(self, session_id: str):
        """Remove a session and free its node."""
        async with self._lock:
            if session_id in self.sessions:
                session = self.sessions[session_id]
                session.state = SessionState.DISCONNECTED
                session.reader = None
                session.writer = None
                del self.sessions[session_id]
    
    def get_session(self, session_id: str) -> Session | None:
        return self.sessions.get(session_id)
    
    @property
    def active_sessions(self) -> list[Session]:
        return [s for s in self.sessions.values() if s.is_active]
    
    @property
    def active_count(self) -> int:
        return len(self.active_sessions)
    
    @property
    def available_nodes(self) -> int:
        return self.max_nodes - self.active_count
    
    def get_all_sessions(self) -> list[dict]:
        """Return info for all active sessions."""
        return [s.to_dict() for s in self.active_sessions]
    
    def _assign_node(self) -> int:
        """Find the lowest available node number."""
        used_nodes = {s.node_id for s in self.sessions.values()}
        for i in range(1, self.max_nodes + 1):
            if i not in used_nodes:
                return i
        raise RuntimeError("No available nodes")
