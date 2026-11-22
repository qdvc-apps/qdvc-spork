"""Lane layout for the commit graph drawn beside the commit list.

Commits arrive in `git log --topo-order` order (children before parents).
Each commit occupies one lane; lanes carry the hash of the commit they are
waiting for. A row records the lines to draw in its top half (into the node)
and bottom half (out of the node), so rows can be drawn independently.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Commit


@dataclass
class GraphRow:
    node: int                    # lane of this commit's dot
    color: int                   # colour index of the node
    # (from_lane, to_lane, colour) segments
    top: list[tuple[int, int, int]] = field(default_factory=list)
    bottom: list[tuple[int, int, int]] = field(default_factory=list)
    width: int = 1               # lanes needed to draw this row


class GraphBuilder:
    """Incremental: call `add` with each page of commits, in order."""

    def __init__(self) -> None:
        self.lanes: list[str | None] = []
        self.colors: list[int] = []
        self._next_color = 0

    def _new_color(self) -> int:
        c = self._next_color
        self._next_color += 1
        return c

    def _free_lane(self) -> int:
        for i, v in enumerate(self.lanes):
            if v is None:
                return i
        self.lanes.append(None)
        self.colors.append(0)
        return len(self.lanes) - 1

    def add(self, commits: list[Commit]) -> list[GraphRow]:
        return [self._add_one(c) for c in commits]

    def _add_one(self, c: Commit) -> GraphRow:
        lanes = self.lanes
        incoming = [i for i, v in enumerate(lanes) if v == c.sha]
        if incoming:
            node = incoming[0]
        else:
            node = self._free_lane()
            self.colors[node] = self._new_color()
        color = self.colors[node]
        before = list(lanes)
        before_colors = list(self.colors)

        row = GraphRow(node=node, color=color)
        for i, v in enumerate(before):
            if v is None:
                continue
            if v == c.sha:
                row.top.append((i, node, before_colors[i]))
            else:
                row.top.append((i, i, before_colors[i]))

        for i in incoming:
            lanes[i] = None
        lanes[node] = None

        for pi, p in enumerate(c.parents):
            existing = next((i for i, v in enumerate(lanes) if v == p), None)
            if pi == 0 and existing is not None and existing > node:
                # Pull the waiting lane left into ours so history hugs the left.
                lanes[existing] = None
                lanes[node] = p
                self.colors[node] = color
                row.bottom.append((node, node, color))
                row.bottom.append((existing, node, self.colors[existing]))
                continue
            if existing is not None:
                row.bottom.append((node, existing, self.colors[existing]))
                continue
            if pi == 0:
                lane = node
                self.colors[lane] = color
            else:
                lane = self._free_lane()
                self.colors[lane] = self._new_color()
            lanes[lane] = p
            row.bottom.append((node, lane, self.colors[lane]))

        for i, v in enumerate(before):
            if v is not None and v != c.sha and i < len(lanes) and lanes[i] == v:
                row.bottom.append((i, i, self.colors[i]))

        while lanes and lanes[-1] is None:
            lanes.pop()
            self.colors.pop()

        used = [row.node] + [a for a, b, _ in row.top + row.bottom] + \
               [b for a, b, _ in row.top + row.bottom]
        row.width = max(used) + 1
        return row


def layout(commits: list[Commit]) -> list[GraphRow]:
    return GraphBuilder().add(commits)
