from __future__ import annotations

from typing import Any

from rich.text import Text

from interaction.render.theme import DEFAULT_THEME, style


# Keep the rendered footer ≤ MAX_LINE_WIDTH chars so it fits an 80-100 col
# terminal. Field-level caps come below.
MAX_LINE_WIDTH: int = 100
MAX_MODEL_WIDTH: int = 12
MAX_LANE_WIDTH: int = 12
MAX_LEAF_WIDTH: int = 12
ELLIPSIS = "..."

def format_token_count(n: int) -> str:
    """Format `n` for compact display.

    Thresholds:
    n < 1000 → "0".."999" (int, no separator)
    1k..999k → "1.0k".."999k" (one decimal; drop trailing .0)
    1M.. → "1.0M".."999M" (same rule)
    >= 1B → "1.0B"+ (we don't expect this in practice)
    """
    n = int(n or 0)
    if n < 0:
        n = 0
    if n < 1000:
        return str(n)
    if n < 1000000:
        v = n / 1000
        s = f"{v:.1f}k"
        # 1.0k → 1k ; 12.3k stays as-is.
        if s.endswith(".0k"):
            s = s[:-3] + "k"
        return s
    if n < 1000000000:
        v = n / 1000000
        s = f"{v:.1f}M"
        if s.endswith(".0M"):
            s = s[:-3] + "M"
        return s
    v = n / 1000000000
    s = f"{v:.1f}B"
    if s.endswith(".0B"):
        s = s[:-3] + "B"
    return s


def _truncate(s: str, max_len: int) -> str:
    if s is None:
        return ""
    s = str(s)
    if len(s) <= max_len:
        return s
    if max_len <= 3:
        return s[:max_len]
    return s[: max_len - len(ELLIPSIS)] + ELLIPSIS


class StatusFooter:

    def __init__(
        self,
        model_id: str | None = None,
        lane: str | None = None,
        leaf_id: str | None = None,
        breadcrumb: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cached_tokens: int = 0,
        cost: float = 0.0,
    ) -> None:
        self.model_id: str | None = model_id
        self.lane: str | None = lane
        self.leaf_id: str | None = leaf_id
        self.breadcrumb: str | None = breadcrumb
        self.input_tokens: int = max(0, int(input_tokens or 0))
        self.output_tokens: int = max(0, int(output_tokens or 0))
        self.cached_tokens: int = max(0, int(cached_tokens or 0))
        self.cost: float = float(cost or 0.0)

    def update_model(self, model_id: str | None) -> None:
        """Set the active model id."""
        if model_id is None:
            return
        self.model_id = str(model_id)

    def update_branch(
        self,
        lane: str | None = None,
        leaf_id: str | None = None,
        breadcrumb: str | None = None,
    ) -> None:
        
        if lane is not None:
            self.lane = lane or None
        if leaf_id is not None:
            self.leaf_id = leaf_id or None
        if breadcrumb is not None:
            self.breadcrumb = breadcrumb or None

    def update_tokens(
        self,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cached_tokens: int | None = None,
        cost: float | None = None,
    ) -> None:
        
        if input_tokens is not None:
            self.input_tokens = max(0, int(input_tokens))
        if output_tokens is not None:
            self.output_tokens = max(0, int(output_tokens))
        if cached_tokens is not None:
            self.cached_tokens = max(0, int(cached_tokens))
        if cost is not None:
            try:
                self.cost = float(cost)
            except (TypeError, ValueError):
                pass

    def update_from_event(self, event: Any) -> None:
        if event is None:
            return
        if not isinstance(event, dict):
            return
        t = event.get("type")
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        msg = event.get("message") if isinstance(event.get("message"), dict) else {}

        if t == "message_start":
            # The assistant message_start may carry a `model` field.
            model = msg.get("model") if msg else None
            if model is None:
                model = event.get("model")
            if model:
                self.update_model(model)

        elif t in ("message_end", "tool_execution_end", "turn_end"):
            # Some events put usage under "usage" or directly inside `data`.
            usage = event.get("usage")
            if not isinstance(usage, dict):
                usage = {}

            def _pick(*names: str) -> Any:
                for src in (usage, data):
                    if not isinstance(src, dict):
                        continue
                    for n in names:
                        if n in src and src[n] is not None:
                            return src[n]
                return None

            in_tok = _pick("input_tokens", "prompt_tokens")
            out_tok = _pick("output_tokens", "completion_tokens")
            cache_tok = _pick("cached_tokens", "cache_read_input_tokens")
            cost = _pick("cost", "total_cost", "cost_usd")

            # Only update when something was actually present (don't clobber).
            updates: dict[str, Any] = {}
            if in_tok is not None:
                updates["input_tokens"] = in_tok
            if out_tok is not None:
                updates["output_tokens"] = out_tok
            if cache_tok is not None:
                updates["cached_tokens"] = cache_tok
            if cost is not None:
                updates["cost"] = cost
            if updates:
                self.update_tokens(**updates)

    def render(self) -> Text:

        label_style = style(DEFAULT_THEME.muted)
        value_style = style(DEFAULT_THEME.tool_title)

        # lane + leaf + breadcrumb
        lane_str = _truncate(self.lane or "main", MAX_LANE_WIDTH)
        leaf_str = _truncate(self.leaf_id or "-", MAX_LEAF_WIDTH)
        if self.breadcrumb:
            path_str = _truncate(self.breadcrumb, MAX_LINE_WIDTH // 2)
        else:
            # Fall back to "lane > leaf" if no explicit breadcrumb.
            path_str = f"{lane_str} > {leaf_str}"

        # Build a single Text; label style for labels, value style for values.
        text = Text()
        # lane
        text.append("lane:", label_style)
        text.append(" ")
        text.append(path_str, value_style)

        # Hard-truncate just in case labels + values still overflow.
        plain = text.plain
        if len(plain) > MAX_LINE_WIDTH:
            # Rebuild with a tighter suffix rather than dropping the trailing
            # fields silently — easier for callers to notice.
            text = Text()
            text.append(plain[: MAX_LINE_WIDTH - 3] + ELLIPSIS, value_style)
        return text