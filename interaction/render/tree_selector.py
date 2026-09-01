from typing import Optional

def _passes_filter(entry: dict, filter_mode: str) -> bool:
    if filter_mode in ("default", "all"):
        return True
    role = entry.get("role")
    etype = entry.get("type")
    if filter_mode == "no-tools":
        return role != "tool"
    if filter_mode == "user-only":
        return role == "user"
    if filter_mode == "labeled-only":
        return etype in ("label", "label_fact")
    return False

def _entry_has_content(entry: dict) -> bool:
    """True if the entry's preview is non-empty after stripping whitespace."""
    return bool((entry.get("preview") or "").strip())


def _is_collapsible_empty(node: dict, filter_mode: str) -> bool:
    if not _passes_filter(node, filter_mode):
        return False
    if node.get("role") != "assistant":
        return False
    return not _entry_has_content(node)

def build_tree_lines(
    entries: list[dict],
    active_leaf_id: Optional[str] = None,
    filter_mode: str = "default",
) -> tuple[list[str], list[Optional[str]]]:
    lines: list[str] = []
    ids: list[Optional[str]] = []

    # Header — always rendered so the user sees the filter mode and count.
    total = len(entries)
    substantive = sum(1 for e in entries if _entry_has_content(e))
    empty_total = total - substantive
    lines.append(
        f"Branch tree ({total} entries, {substantive} substantive, "
        f"{empty_total} empty, filter: {filter_mode}):"
    )
    ids.append(None)

    if not entries:
        return lines, ids

    # Build parent_id -> children lookup, preserving seq order (entries
    # arrive sorted by seq ascending).
    children_of: dict[Optional[str], list[dict]] = {}
    for e in entries:
        parent = e.get("parent_id")
        children_of.setdefault(parent, []).append(e)

    roots = children_of.get(None, [])

    display_nodes: list[tuple[dict, str]] = []

    stack: list[tuple[dict, list[bool]]] = [
        (root, []) for root in reversed(roots)
    ]
    while stack:
        node, ancestors = stack.pop()
        parent_id = node.get("parent_id")
        siblings = children_of.get(parent_id, [])
        # Find this node's position among siblings.
        try:
            idx_in_siblings = siblings.index(node)
        except ValueError:
            idx_in_siblings = 0
        has_more_siblings = idx_in_siblings < len(siblings) - 1

        # Build the connector prefix from ancestors (these are the parents
        # of this node, NOT including the root — see comment above).
        prefix_parts: list[str] = []
        for anc_has_more in ancestors:
            prefix_parts.append("│ " if anc_has_more else "    ")
        # Append this node's own connector.
        if parent_id is None:
            own_connector = ""  # root — no connector
        elif has_more_siblings:
            own_connector = "├── "
        else:
            own_connector = "└── "
        prefix_parts.append(own_connector)
        connector_prefix = "".join(prefix_parts)

        display_nodes.append((node, connector_prefix))

        node_children = children_of.get(node.get("id"), [])

        child_ancestors = ancestors + [has_more_siblings]
        for child in reversed(node_children):
            stack.append((child, child_ancestors))

    counter = 0  # displayed-number counter, only counts visible entries
    i = 0
    while i < len(display_nodes):
        node, prefix = display_nodes[i]

        if _is_collapsible_empty(node, filter_mode):
            # Find the run of consecutive collapsible empties.
            run_start = i
            while i < len(display_nodes) and _is_collapsible_empty(
                display_nodes[i][0], filter_mode
            ):
                i += 1
            run_count = i - run_start
            first_node, first_prefix = display_nodes[run_start]
            counter += 1

            run_ids = {display_nodes[j][0].get("id") for j in range(run_start, i)}
            marker = "● " if active_leaf_id in run_ids else "  "

            if run_count == 1:
                body = "assistant \"(empty)\""
            else:
                body = f"assistant \"({run_count} empty)\""
            lines.append(f"{first_prefix}{marker}{counter}.  {body}")
            ids.append(first_node.get("id"))
            continue

        # Non-collapsible entry.
        if _passes_filter(node, filter_mode):
            counter += 1
            marker = "● " if node.get("id") == active_leaf_id else "  "
            index_str = f"{counter}."
            role = node.get("role") or node.get("type") or "?"
            preview = node.get("preview") or ""
            lines.append(f"{prefix}{marker}{index_str}  {role} \"{preview}\"")
            ids.append(node.get("id"))
        else:
            # Filtered out — still draw the connector so the tree shape
            # stays legible, but no number.
            marker = "  "
            role = node.get("role") or node.get("type") or "?"
            preview = node.get("preview") or ""
            lines.append(f"{prefix}{marker}    {role} \"{preview}\"")
            ids.append(None)
        i += 1

    return lines, ids

def build_colored_tree_lines(
    entries: list[dict],
    active_leaf_id: Optional[str] = None,
    filter_mode: str = "default",
) -> tuple[list[str], list[Optional[str]]]:
    raw_lines, ids = build_tree_lines(entries, active_leaf_id, filter_mode)
    from interaction.render.theme import DEFAULT_THEME

    def _to_hex(color) -> str:
        # rich's ColorType.TRUECOLOR exposes .triplet (r,g,b each 0-255).
        try:
            t = color.triplet
            return f"#{t.red:02x}{t.green:02x}{t.blue:02x}"
        except Exception:
            # Fall back to named/system colors.
            return str(color.name) if color.name else "white"

    muted_hex = _to_hex(DEFAULT_THEME.muted)
    divider_hex = _to_hex(DEFAULT_THEME.divider)

    def _style_connector(line: str) -> str:
        # Identify connector chars ─ │ ├ └ ─ (any of ├ ─ │ └).
        i = 0
        out: list[str] = []
        while i < len(line):
            ch = line[i]
            if ch in ("├", "─", "│", "└"):
                # Take the whole connector run.
                j = i
                while j < len(line) and line[j] in ("├", "─", "│", "└", " "):
                    j += 1
                connector = line[i:j]
                out.append(f"[{muted_hex}]{connector}[/]")
                i = j
            elif ch == "●":
                out.append(f"[{divider_hex}]●[/]")
                i += 1
            else:
                out.append(ch)
                i += 1
        return "".join(out)

    return [_style_connector(line) for line in raw_lines], ids
