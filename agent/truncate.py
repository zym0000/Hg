from dataclasses import dataclass
from typing import Literal


DEFAULT_MAX_LINES = 2000
DEFAULT_MAX_BYTES = 50 * 1024  # 50KB
GREP_MAX_LINE_LENGTH = 500  # Max chars per grep match line

def format_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes}B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f}KB"
    return f"{num_bytes / (1024 * 1024):.1f}MB"


@dataclass(frozen=True)
class TruncationResult:
    content: str
    truncated: bool
    truncated_by: Literal["lines", "bytes"] | None
    total_lines: int
    total_bytes: int
    output_lines: int
    output_bytes: int
    last_line_partial: bool
    first_line_exceeds_limit: bool
    max_lines: int
    max_bytes: int

def _utf8_byte_length(content: str) -> int:
    return len(content.encode("utf-8"))

def _split_lines_for_counting(content: str) -> list[str]:
    if not content:
        return []
    lines = content.split("\n")
    if content.endswith("\n"):
        lines.pop()
    return lines

def truncate_head(content: str, max_lines: int = DEFAULT_MAX_LINES,
                  max_bytes: int = DEFAULT_MAX_BYTES) -> TruncationResult:
    total_bytes = _utf8_byte_length(content)
    lines = _split_lines_for_counting(content)
    total_lines = len(lines)

    if total_lines <= max_lines and total_bytes <= max_bytes:
        return TruncationResult(
            content=content, truncated=False, truncated_by=None,
            total_lines=total_lines, total_bytes=total_bytes,
            output_lines=total_lines, output_bytes=total_bytes,
            last_line_partial=False, first_line_exceeds_limit=False,
            max_lines=max_lines, max_bytes=max_bytes,
        )

    # Check if first line alone exceeds byte limit
    first_line_bytes = _utf8_byte_length(lines[0]) if lines else 0
    if first_line_bytes > max_bytes:
        return TruncationResult(
            content="", truncated=True, truncated_by="bytes",
            total_lines=total_lines, total_bytes=total_bytes,
            output_lines=0, output_bytes=0,
            last_line_partial=False, first_line_exceeds_limit=True,
            max_lines=max_lines, max_bytes=max_bytes,
        )

    out_lines: list[str] = []
    out_bytes = 0
    truncated_by: Literal["lines", "bytes"] = "lines"

    for i in range(min(len(lines), max_lines)):
        line = lines[i]
        line_bytes = _utf8_byte_length(line) + (1 if i > 0 else 0)  # +1 for newline
        if out_bytes + line_bytes > max_bytes:
            truncated_by = "bytes"
            break
        out_lines.append(line)
        out_bytes += line_bytes

    if len(out_lines) >= max_lines and out_bytes <= max_bytes:
        truncated_by = "lines"

    output_content = "\n".join(out_lines)
    return TruncationResult(
        content=output_content, truncated=True, truncated_by=truncated_by,
        total_lines=total_lines, total_bytes=total_bytes,
        output_lines=len(out_lines), output_bytes=_utf8_byte_length(output_content),
        last_line_partial=False, first_line_exceeds_limit=False,
        max_lines=max_lines, max_bytes=max_bytes,
    )

def truncate_tail(content: str, max_lines: int = DEFAULT_MAX_LINES,
                  max_bytes: int = DEFAULT_MAX_BYTES) -> TruncationResult:
    total_bytes = _utf8_byte_length(content)
    lines = _split_lines_for_counting(content)
    total_lines = len(lines)

    if total_lines <= max_lines and total_bytes <= max_bytes:
        return TruncationResult(
            content=content, truncated=False, truncated_by=None,
            total_lines=total_lines, total_bytes=total_bytes,
            output_lines=total_lines, output_bytes=total_bytes,
            last_line_partial=False, first_line_exceeds_limit=False,
            max_lines=max_lines, max_bytes=max_bytes,
        )

    out_lines: list[str] = []
    out_bytes = 0
    truncated_by: Literal["lines", "bytes"] = "lines"
    last_line_partial = False

    for i in range(len(lines) - 1, -1, -1):
        if len(out_lines) >= max_lines:
            break
        line = lines[i]
        line_bytes = _utf8_byte_length(line) + (1 if out_lines else 0)
        if out_bytes + line_bytes > max_bytes:
            truncated_by = "bytes"
            if not out_lines:
                # Edge case: keep end of last line (partial).
                partial = _truncate_string_to_bytes_from_end(line, max_bytes)
                out_lines.insert(0, partial)
                out_bytes = _utf8_byte_length(partial)
                last_line_partial = True
            break
        out_lines.insert(0, line)
        out_bytes += line_bytes

    if len(out_lines) >= max_lines and out_bytes <= max_bytes:
        truncated_by = "lines"

    output_content = "\n".join(out_lines)
    return TruncationResult(
        content=output_content, truncated=True, truncated_by=truncated_by,
        total_lines=total_lines, total_bytes=total_bytes,
        output_lines=len(out_lines), output_bytes=_utf8_byte_length(output_content),
        last_line_partial=last_line_partial, first_line_exceeds_limit=False,
        max_lines=max_lines, max_bytes=max_bytes,
    )

def truncate_line(line: str, max_chars: int = GREP_MAX_LINE_LENGTH) -> tuple[str, bool]:
    if len(line) <= max_chars:
        return line, False
    return f"{line[:max_chars]}... [truncated]", True


def _truncate_string_to_bytes_from_end(s: str, max_bytes: int) -> str:
    if max_bytes <= 0:
        return ""
    encoded = s.encode("utf-8")
    if len(encoded) <= max_bytes:
        return s
    # Slice from the end. Walk back until removing one more byte would drop us
    # below max_bytes. Use errors='ignore' to handle partial multi-byte chars.
    tail = encoded[-max_bytes:]
    while True:
        try:
            return tail.decode("utf-8")
        except UnicodeDecodeError:
            tail = tail[1:]  # drop a leading partial byte sequence
            if not tail:
                return ""