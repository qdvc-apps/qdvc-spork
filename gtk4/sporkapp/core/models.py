"""Plain data types shared by the core and the UI."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


WORKING_COPY = "WORKING"  # pseudo-hash for the "Uncommitted changes" row


@dataclass
class Commit:
    sha: str
    parents: list[str]
    author_name: str
    author_email: str
    author_time: int
    committer_name: str
    committer_email: str
    committer_time: int
    refs: list[str]
    subject: str
    body: str = ""

    @property
    def short_sha(self) -> str:
        return self.sha[:8]

    @property
    def is_merge(self) -> bool:
        return len(self.parents) > 1


class RefKind(Enum):
    HEAD = "head"          # "HEAD -> main" (the checked-out branch)
    BRANCH = "branch"
    REMOTE = "remote"
    TAG = "tag"
    DETACHED = "detached"  # bare "HEAD"


@dataclass
class RefLabel:
    name: str
    kind: RefKind


@dataclass
class FileChange:
    """One file changed by a commit (or between index and worktree)."""
    path: str
    status: str               # A, M, D, R, C, T, U, ?
    old_path: str | None = None
    additions: int | None = None   # None for binary files
    deletions: int | None = None

    @property
    def display_path(self) -> str:
        if self.old_path and self.old_path != self.path:
            return f"{self.old_path} → {self.path}"
        return self.path


@dataclass
class StatusEntry:
    """One path from `git status --porcelain=v2`."""
    path: str
    index: str      # X: status in the index ('.' = unchanged)
    worktree: str   # Y: status in the work tree ('.' = unchanged)
    old_path: str | None = None
    conflicted: bool = False
    untracked: bool = False

    @property
    def staged(self) -> bool:
        return not self.untracked and not self.conflicted and self.index != "."

    @property
    def unstaged(self) -> bool:
        return self.untracked or self.conflicted or self.worktree != "."


@dataclass
class BranchHeader:
    oid: str | None = None          # None on an unborn branch
    head: str | None = None         # None when detached
    upstream: str | None = None
    ahead: int = 0
    behind: int = 0


@dataclass
class WorkingStatus:
    branch: BranchHeader
    entries: list[StatusEntry]

    @property
    def staged(self) -> list[StatusEntry]:
        return [e for e in self.entries if e.staged]

    @property
    def unstaged(self) -> list[StatusEntry]:
        return [e for e in self.entries if e.unstaged]

    @property
    def conflicted(self) -> list[StatusEntry]:
        return [e for e in self.entries if e.conflicted]

    @property
    def untracked(self) -> list[StatusEntry]:
        return [e for e in self.entries if e.untracked]

    @property
    def is_clean(self) -> bool:
        return not self.entries


@dataclass
class Remote:
    name: str
    fetch_url: str
    push_url: str


@dataclass
class RemoteBranchState:
    """How the current branch compares with `<remote>/<branch>`."""
    remote: str
    exists: bool            # does refs/remotes/<remote>/<branch> exist?
    ahead: int = 0          # local commits the remote lacks
    behind: int = 0         # remote commits we lack


@dataclass
class Branch:
    name: str
    sha: str
    upstream: str | None
    is_head: bool
    track: str = ""         # e.g. "[ahead 1, behind 2]"


@dataclass
class TreeEntry:
    path: str
    mode: str
    kind: str      # blob, tree, commit (submodule)
    sha: str
    size: int | None = None


@dataclass
class BlameLine:
    sha: str
    author: str
    author_time: int
    summary: str
    line_no: int
    text: str


@dataclass
class DiffLine:
    kind: str           # ' ', '+', '-', '\\'
    text: str
    old_no: int | None
    new_no: int | None


@dataclass
class Hunk:
    header: str         # the full "@@ -a,b +c,d @@ ctx" line
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[DiffLine] = field(default_factory=list)

    def patch_text(self) -> str:
        out = [self.header]
        for ln in self.lines:
            out.append(ln.kind + ln.text)
        return "\n".join(out) + "\n"


@dataclass
class FileDiff:
    header_lines: list[str]   # "diff --git", "index", "---", "+++" ...
    old_path: str | None
    new_path: str | None
    hunks: list[Hunk]
    binary: bool = False

    def patch_for_hunk(self, index: int) -> str:
        """A patch containing only one hunk, suitable for `git apply`."""
        head = "\n".join(self.header_lines) + "\n"
        return head + self.hunks[index].patch_text()

    @property
    def additions(self) -> int:
        return sum(1 for h in self.hunks for l in h.lines if l.kind == "+")

    @property
    def deletions(self) -> int:
        return sum(1 for h in self.hunks for l in h.lines if l.kind == "-")


class Level(Enum):
    """Census-style verdict for a repository."""
    OK = "ok"              # clean and in sync with every reachable remote
    ATTENTION = "attention"
    UNKNOWN = "unknown"    # nothing flagged, but a remote couldn't be checked
    MISSING = "missing"    # bookmarked folder is gone / not a repository


@dataclass
class RepoSummary:
    path: str
    name: str
    level: Level
    branch: str | None = None
    detached: bool = False
    operation: str | None = None          # "merge", "rebase", ...
    staged: int = 0
    modified: int = 0
    untracked: int = 0
    conflicted: int = 0
    stashes: int = 0
    unpushed_anywhere: int = 0            # commits on no remote at all
    remotes: list[RemoteBranchState] = field(default_factory=list)
    upstream: str | None = None
    last_commit_time: int | None = None
    last_commit_subject: str | None = None
    fetch_errors: dict[str, str] = field(default_factory=dict)
    offline_remotes: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def total_ahead(self) -> int:
        return max((r.ahead for r in self.remotes if r.exists), default=0)

    @property
    def total_behind(self) -> int:
        return max((r.behind for r in self.remotes if r.exists), default=0)

    @property
    def dirty_count(self) -> int:
        return self.staged + self.modified + self.untracked + self.conflicted
