"""
Telnet protocol constants and negotiation helpers.
RFC 854/855 compliant with IAC (Interpret As Command) handling.
"""

# Telnet Commands
IAC = 255   # Interpret As Command
DONT = 254
DO = 253
WONT = 252
WILL = 251
SB = 250    # Sub-negotiation Begin
SE = 240    # Sub-negotiation End
NOP = 241
GA = 249    # Go Ahead

# Network Virtual Terminal options
OPT_ECHO = 1
OPT_SUPPRESS_GO_AHEAD = 3
OPT_TERMINAL_TYPE = 24
OPT_WINDOW_SIZE = 31
OPT_TERMINAL_SPEED = 32
OPT_LINEMODE = 34

# NAWS (Negotiate About Window Size) sub-option
NAWS = 1


class ANSI:
    """ANSI escape codes as class constants."""
    
    # Reset
    RESET = "\033[0m"
    
    # Regular colors
    BLACK = "\033[30m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"
    
    # Bright/bold colors
    BRIGHT_BLACK = "\033[90m"
    BRIGHT_RED = "\033[91m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_BLUE = "\033[94m"
    BRIGHT_MAGENTA = "\033[95m"
    BRIGHT_CYAN = "\033[96m"
    BRIGHT_WHITE = "\033[97m"
    
    # Background colors
    BG_BLACK = "\033[40m"
    BG_RED = "\033[41m"
    BG_GREEN = "\033[42m"
    BG_YELLOW = "\033[43m"
    BG_BLUE = "\033[44m"
    BG_MAGENTA = "\033[45m"
    BG_CYAN = "\033[46m"
    BG_WHITE = "\033[47m"
    
    # Text attributes
    BOLD = "\033[1m"
    DIM = "\033[2m"
    UNDERLINE = "\033[4m"
    BLINK = "\033[5m"
    REVERSE = "\033[7m"
    
    # Cursor / screen control (static methods for dynamic args)
    CLEAR_SCREEN = "\033[2J"
    CLEAR_LINE = "\033[2K"
    SAVE_CURSOR = "\033[s"
    RESTORE_CURSOR = "\033[u"
    
    @staticmethod
    def move_to(row: int, col: int) -> str:
        return f"\033[{row};{col}H"
    
    @staticmethod
    def cursor_up(n: int = 1) -> str:
        return f"\033[{n}A"
    
    @staticmethod
    def cursor_down(n: int = 1) -> str:
        return f"\033[{n}B"
    
    @staticmethod
    def cursor_forward(n: int = 1) -> str:
        return f"\033[{n}C"
    
    @staticmethod
    def cursor_back(n: int = 1) -> str:
        return f"\033[{n}D"


class TelnetNegotiator:
    """Handles IAC negotiation for a single connection."""
    
    def __init__(self):
        self.remote_options = {}  # What the remote side supports
        self.local_options = {}   # What we support
        self.window_size = (80, 24)  # Default terminal size
        self.terminal_type = "UNKNOWN"
        self._buffer = bytearray()
        self._in_subneg = False
        self._subneg_option = None
        self._subneg_data = bytearray()
        self.negotiation_complete = False
    
    def process_data(self, data: bytes) -> tuple[bytes, list[bytes]]:
        """
        Process incoming data, extract telnet commands, return clean data
        and any response bytes we need to send.
        """
        self._buffer.extend(data)
        clean = bytearray()
        responses = []
        
        i = 0
        while i < len(self._buffer):
            if self._in_subneg:
                if i + 1 < len(self._buffer) and self._buffer[i] == IAC and self._buffer[i+1] == SE:
                    # End of subnegotiation
                    self._handle_subneg(self._subneg_option, bytes(self._subneg_data))
                    self._in_subneg = False
                    self._subneg_option = None
                    self._subneg_data.clear()
                    i += 2
                else:
                    self._subneg_data.append(self._buffer[i])
                    i += 1
                continue
            
            byte = self._buffer[i]
            
            if byte == IAC:
                if i + 1 >= len(self._buffer):
                    break  # Wait for more data
                
                cmd = self._buffer[i + 1]
                
                if cmd == IAC:
                    # Escaped IAC (literal 0xFF)
                    clean.append(IAC)
                    i += 2
                elif cmd in (DO, DONT, WILL, WONT):
                    if i + 2 >= len(self._buffer):
                        break  # Wait for more data
                    option = self._buffer[i + 2]
                    resp = self._handle_negotiation(cmd, option)
                    if resp:
                        responses.append(resp)
                    i += 3
                elif cmd == SB:
                    self._in_subneg = True
                    self._subneg_option = None
                    self._subneg_data.clear()
                    i += 2
                    if i < len(self._buffer) and self._buffer[i] != IAC:
                        self._subneg_option = self._buffer[i]
                        i += 1
                elif cmd == NOP:
                    i += 2
                elif cmd == GA:
                    i += 2
                else:
                    # Unknown command, skip
                    i += 2
            else:
                clean.append(byte)
                i += 1
        
        # Remove consumed bytes
        self._buffer = self._buffer[i:]
        
        return bytes(clean), responses
    
    def _handle_negotiation(self, cmd: int, option: int) -> bytes | None:
        """Handle WILL/WONT/DO/DONT negotiations."""
        if cmd == WILL:
            # Remote wants to enable an option
            if option in self._supported_will():
                self.remote_options[option] = True
                return bytes([IAC, DO, option])
            else:
                return bytes([IAC, DONT, option])
        
        elif cmd == WONT:
            self.remote_options[option] = False
            return None
        
        elif cmd == DO:
            # Remote wants us to enable an option
            if option in self._supported_do():
                self.local_options[option] = True
                return bytes([IAC, WILL, option])
            else:
                return bytes([IAC, WONT, option])
        
        elif cmd == DONT:
            self.local_options[option] = False
            return None
        
        return None
    
    def _handle_subneg(self, option: int, data: bytes):
        """Handle sub-negotiation data."""
        if option == OPT_TERMINAL_TYPE:
            # Terminal type sub-negotiation: first byte is command (1=SEND, 0=IS)
            if len(data) >= 2 and data[0] == 0:  # IS
                self.terminal_type = data[1:].decode('ascii', errors='replace')
        
        elif option == OPT_WINDOW_SIZE:
            # NAWS: 2 bytes width, 2 bytes height (network byte order)
            if len(data) == 4:
                width = (data[0] << 8) | data[1]
                height = (data[2] << 8) | data[3]
                self.window_size = (width, height)
    
    def _supported_will(self) -> set[int]:
        """Options we can WILL (offer to do).

        ECHO is deliberately excluded: we never echo server-side (telnet
        clients do local echo), so we decline any request to take over echo.
        """
        return {
            OPT_SUPPRESS_GO_AHEAD,
            OPT_TERMINAL_TYPE,
            OPT_TERMINAL_SPEED,
        }

    def _supported_do(self) -> set[int]:
        """Options we can DO (allow remote to do). ECHO excluded (see above)."""
        return {
            OPT_SUPPRESS_GO_AHEAD,
            OPT_TERMINAL_TYPE,
            OPT_WINDOW_SIZE,
        }
    
    def request_window_size(self) -> bytes:
        """Send DO NAWS to request window size."""
        return bytes([IAC, DO, OPT_WINDOW_SIZE])
    
    def request_terminal_type(self) -> bytes:
        """Send DO TERMINAL-TYPE to request terminal type."""
        return bytes([IAC, DO, OPT_TERMINAL_TYPE])
    
    def suppress_go_ahead(self) -> bytes:
        """Send WILL SUPPRESS-GO-AHEAD."""
        return bytes([IAC, WILL, OPT_SUPPRESS_GO_AHEAD])
    
    def initial_negotiation(self) -> bytes:
        """Send our initial negotiation offers.

        Deliberately does NOT offer ECHO: telnet clients (SyncTERM) do local
        echo themselves, so the server echoing would double-print and leak
        raw IAC bytes into the display as CP437 glyphs.
        """
        return (
            self.suppress_go_ahead() +
            self.request_terminal_type() +
            self.request_window_size()
        )
