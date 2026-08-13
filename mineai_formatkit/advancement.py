from __future__ import annotations

import re

from .minecraft_text import MinecraftTextComponentAdapter, _Node


_ADVANCEMENT_PATH_RE = re.compile(
    r"(^|/)data/[^/]+/advancements?/.+\.json$",
    re.IGNORECASE,
)


class MinecraftAdvancementTextAdapter(MinecraftTextComponentAdapter):
    """Add direct advancement ``display.title``/``description`` string support.

    Minecraft permits those two display fields to be literal string Components.
    The generic text-component adapter already handles nested Component objects;
    this subclass adds only the direct-string schema surface proven by real mods.
    """

    name = "minecraft-advancement-text"

    def matches(self, path: str) -> bool:
        slash = "/" + path.replace("\\", "/").lstrip("/")
        return bool(_ADVANCEMENT_PATH_RE.search(slash))

    def _collect(self, node: _Node, path: str, out: list[tuple[str, _Node, str, str]]) -> None:
        if path == "" and node.kind == "object":
            root_members = {member.key: member.value for member in node.members}
            display = root_members.get("display")
            if display and display.kind == "object":
                display_members = {member.key: member.value for member in display.members}
                for field in ("title", "description"):
                    value_node = display_members.get(field)
                    if value_node and value_node.kind == "string":
                        value = value_node.value
                        assert isinstance(value, str)
                        out.append((f"/display/{field}", value_node, value, "json-string"))

        # Preserve every previously-supported text-component context too.
        super()._collect(node, path, out)
