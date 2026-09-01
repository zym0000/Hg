from dataclasses import dataclass

from rich.color import Color
from rich.style import Style


@dataclass(frozen=True)
class Theme:

    user_message_bg: Color = Color.parse("#3a3a3a")      # neutral gray bg
    user_message_text: Color = Color.parse("#dcdcdc")    # light gray text

    # Assistant message
    assistant_text: Color = Color.parse("#dddddd")
    thinking_text: Color = Color.parse("#888888")
    thinking_style: str = "italic"

    # Tool execution backgrounds
    tool_pending_bg: Color = Color.parse("#3a3a1a")      # dim yellow bg
    tool_success_bg: Color = Color.parse("#1a3a1a")      # dim green bg
    tool_error_bg: Color = Color.parse("#3a1a1a")        # dim red bg

    # Tool content
    tool_title: Color = Color.parse("#87cefa")           # light blue
    tool_output: Color = Color.parse("#cccccc")
    muted: Color = Color.parse("#666666")
    error: Color = Color.parse("#ff6666")

    # Dividers
    divider: Color = Color.parse("#5555aa")


DEFAULT_THEME = Theme()


def style(color: Color, *, bold: bool = False, italic: bool = False) -> Style:
    return Style(color=color, bold=bold, italic=italic)


def bg_style(color: Color) -> Style:
    return Style(bgcolor=color)
