"""Framework-independent status data shared by query, service and rendering."""
from dataclasses import dataclass, field


@dataclass
class TextSegment:
    text: str
    color: tuple[int, int, int] | None = None
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strikethrough: bool = False


@dataclass
class MotdLine:
    segments: list[TextSegment] = field(default_factory=list)

    def plain_text(self) -> str:
        return "".join(segment.text for segment in self.segments)


@dataclass
class PlayerInfo:
    name: str
    uuid: str | None = None


@dataclass
class ServerSnapshot:
    host: str
    server_name: str = ""
    players: list[PlayerInfo] = field(default_factory=list)
    online: int | None = None
    max: int | None = None
    version: str | None = None
    latency: float | None = None
    icon: str | None = None
    motd: list[MotdLine] = field(default_factory=list)
    error: str | None = None
    note: str | None = None
