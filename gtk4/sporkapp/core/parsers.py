"""Parsers for git's machine-readable output formats.

Each function takes the raw text git printed and returns model objects. They
have no side effects, so they're easy to test with captured output.
"""

from __future__ import annotations

import re

from .models import (
    BlameLine, Branch, BranchHeader, Commit, DiffLine, FileChange, FileDiff,
    Hunk, RefKind, RefLabel, Remote, StatusEntry, TreeEntry, WorkingStatus,
)

US = "\x1f"   # unit separator between fields
RS = "\x1e"   # record separator between commits

# Field order must match LOG_FORMAT.
LOG_FORMAT = US.join(["%H", "%P", "%an", "%ae", "%at", "%cn", "%ce", "%ct", "%D", "%s"]) + RS
DETAIL_FORMAT = US.join(["%H", "%P", "%an", "%ae", "%at", "%cn", "%ce", "%ct", "%D", "%s", "%b"]) + RS


# -- log ---------------------------------------------------------------------

def parse_log(text: str, with_body: bool = False) -> list[Commit]:
    commits: list[Commit] = []
    for record in text.split(RS):
        record = record.lstrip("\n")
        if not record.strip():
            continue
        f = record.split(US)
        if len(f) < 10:
            continue
        body = f[10].strip() if with_body and len(f) > 10 else ""
        commits.append(Commit(
            sha=f[0], parents=f[1].split() if f[1] else [],
            author_name=f[2], author_email=f[3], author_time=_int(f[4]),
            committer_name=f[5], committer_email=f[6], committer_time=_int(f[7]),
            refs=[r.strip() for r in f[8].split(",") if r.strip()],
            subject=f[9], body=body,
        ))
    return commits


def parse_refs(decorations: list[str]) -> list[RefLabel]:
    """Turn `%D` entries produced with `--decorate=full` into labels."""
    labels: list[RefLabel] = []
    for d in decorations:
        if d.startswith("HEAD -> "):
            labels.append(RefLabel(_strip_ref(d[8:]), RefKind.HEAD))
        elif d == "HEAD":
            labels.append(RefLabel("HEAD", RefKind.DETACHED))
        elif d.startswith("tag: "):
            labels.append(RefLabel(_strip_ref(d[5:]), RefKind.TAG))
        elif d.startswith("refs/remotes/"):
            name = d[len("refs/remotes/"):]
            if name.endswith("/HEAD"):
                continue  # origin/HEAD is noise in a commit list
            labels.append(RefLabel(name, RefKind.REMOTE))
        elif d.startswith("refs/heads/"):
            labels.append(RefLabel(d[len("refs/heads/"):], RefKind.BRANCH))
        elif d.startswith("refs/stash"):
            continue
        else:
            labels.append(RefLabel(_strip_ref(d), RefKind.BRANCH))
    return labels


def _strip_ref(name: str) -> str:
    for prefix in ("refs/heads/", "refs/tags/", "refs/remotes/"):
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


def _int(s: str) -> int:
    try:
        return int(s)
    except ValueError:
        return 0


# -- status ------------------------------------------------------------------

def parse_status_v2(data: str) -> WorkingStatus:
    """Parse `git status --porcelain=v2 --branch -z`."""
    header = BranchHeader()
    entries: list[StatusEntry] = []
    tokens = data.split("\0")
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        i += 1
        if not tok:
            continue
        if tok.startswith("# "):
            key, _, value = tok[2:].partition(" ")
            if key == "branch.oid":
                header.oid = None if value == "(initial)" else value
            elif key == "branch.head":
                header.head = None if value == "(detached)" else value
            elif key == "branch.upstream":
                header.upstream = value
            elif key == "branch.ab":
                m = re.match(r"\+(\d+) -(\d+)", value)
                if m:
                    header.ahead, header.behind = int(m.group(1)), int(m.group(2))
            continue
        kind = tok[0]
        if kind == "1":
            parts = tok.split(" ", 8)
            xy = parts[1]
            entries.append(StatusEntry(path=parts[8], index=xy[0], worktree=xy[1]))
        elif kind == "2":
            parts = tok.split(" ", 9)
            xy = parts[1]
            orig = tokens[i] if i < len(tokens) else None
            i += 1
            entries.append(StatusEntry(path=parts[9], index=xy[0], worktree=xy[1],
                                       old_path=orig))
        elif kind == "u":
            parts = tok.split(" ", 10)
            xy = parts[1]
            entries.append(StatusEntry(path=parts[10], index=xy[0], worktree=xy[1],
                                       conflicted=True))
        elif kind == "?":
            entries.append(StatusEntry(path=tok[2:], index="?", worktree="?",
                                       untracked=True))
        # '!' (ignored) entries are not requested
    return WorkingStatus(branch=header, entries=entries)


# -- changed files -----------------------------------------------------------

def parse_name_status(data: str) -> list[FileChange]:
    """Parse `--name-status -z` output."""
    out: list[FileChange] = []
    tokens = data.split("\0")
    i = 0
    while i < len(tokens):
        code = tokens[i]
        i += 1
        if not code:
            continue
        letter = code[0]
        if letter in "RC":
            old, new = tokens[i], tokens[i + 1]
            i += 2
            out.append(FileChange(path=new, status=letter, old_path=old))
        else:
            path = tokens[i]
            i += 1
            out.append(FileChange(path=path, status=letter))
    return out


def parse_numstat(data: str) -> dict[str, tuple[int | None, int | None]]:
    """Parse `--numstat -z`; returns {new_path: (additions, deletions)}."""
    out: dict[str, tuple[int | None, int | None]] = {}
    tokens = data.split("\0")
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        i += 1
        if not tok:
            continue
        parts = tok.split("\t")
        if len(parts) < 3:
            continue
        add = None if parts[0] == "-" else _int(parts[0])
        dele = None if parts[1] == "-" else _int(parts[1])
        if parts[2] == "":
            # rename/copy: the next two tokens are old and new path
            new = tokens[i + 1] if i + 1 < len(tokens) else ""
            i += 2
            out[new] = (add, dele)
        else:
            out[parts[2]] = (add, dele)
    return out


def merge_numstat(changes: list[FileChange],
                  stats: dict[str, tuple[int | None, int | None]]) -> list[FileChange]:
    for c in changes:
        if c.path in stats:
            c.additions, c.deletions = stats[c.path]
    return changes


# -- unified diff ------------------------------------------------------------

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def parse_diff(text: str) -> list[FileDiff]:
    files: list[FileDiff] = []
    cur: FileDiff | None = None
    hunk: Hunk | None = None
    old_no = new_no = 0
    for raw in text.split("\n"):
        if raw.startswith("diff --git ") or (raw.startswith("diff --cc ")):
            cur = FileDiff(header_lines=[raw], old_path=None, new_path=None, hunks=[])
            files.append(cur)
            hunk = None
            continue
        if cur is None:
            continue
        if hunk is None:
            m = _HUNK_RE.match(raw)
            if m:
                hunk = _new_hunk(raw, m)
                cur.hunks.append(hunk)
                old_no, new_no = hunk.old_start, hunk.new_start
                continue
            if raw.startswith("Binary files ") or raw.startswith("GIT binary patch"):
                cur.binary = True
            if raw.startswith("--- "):
                cur.old_path = _diff_path(raw[4:])
            elif raw.startswith("+++ "):
                cur.new_path = _diff_path(raw[4:])
            if raw:
                cur.header_lines.append(raw)
            continue
        m = _HUNK_RE.match(raw)
        if m:
            hunk = _new_hunk(raw, m)
            cur.hunks.append(hunk)
            old_no, new_no = hunk.old_start, hunk.new_start
            continue
        if not raw:
            # A blank line can only be the trailing newline of the output; a
            # blank context line inside a hunk is " " (one space).
            continue
        tag, body = raw[0], raw[1:]
        if tag == " ":
            hunk.lines.append(DiffLine(" ", body, old_no, new_no))
            old_no += 1
            new_no += 1
        elif tag == "-":
            hunk.lines.append(DiffLine("-", body, old_no, None))
            old_no += 1
        elif tag == "+":
            hunk.lines.append(DiffLine("+", body, None, new_no))
            new_no += 1
        elif tag == "\\":
            hunk.lines.append(DiffLine("\\", body, None, None))
    for f in files:
        if f.old_path is None and f.new_path is None:
            a, b = _paths_from_git_header(f.header_lines[0])
            f.old_path, f.new_path = a, b
    return files


def _new_hunk(raw: str, m: re.Match) -> Hunk:
    return Hunk(header=raw,
                old_start=int(m.group(1)), old_count=int(m.group(2) or 1),
                new_start=int(m.group(3)), new_count=int(m.group(4) or 1))


def _diff_path(p: str) -> str | None:
    p = p.split("\t")[0]
    if p == "/dev/null":
        return None
    if p.startswith('"') and p.endswith('"'):
        p = _unquote_c(p[1:-1])
    if p.startswith("a/") or p.startswith("b/"):
        return p[2:]
    return p


def _paths_from_git_header(line: str) -> tuple[str | None, str | None]:
    m = re.match(r"diff --git a/(.*) b/(.*)$", line)
    if m:
        return m.group(1), m.group(2)
    return None, None


def _unquote_c(s: str) -> str:
    """Undo git's C-style path quoting (\\t, \\n, \\", \\\\, octal bytes)."""
    out = bytearray()
    i = 0
    raw = s.encode("utf-8", errors="surrogateescape")
    while i < len(raw):
        c = raw[i]
        if c == 0x5C and i + 1 < len(raw):  # backslash
            n = raw[i + 1]
            simple = {ord("n"): 10, ord("t"): 9, ord('"'): 34, ord("\\"): 92,
                      ord("a"): 7, ord("b"): 8, ord("f"): 12, ord("r"): 13, ord("v"): 11}
            if n in simple:
                out.append(simple[n])
                i += 2
                continue
            if 0x30 <= n <= 0x37 and i + 3 < len(raw) + 1:
                out.append(int(raw[i + 1:i + 4].decode(), 8))
                i += 4
                continue
        out.append(c)
        i += 1
    return out.decode("utf-8", errors="replace")


# -- refs, remotes -------------------------------------------------------------

BRANCH_FORMAT = "%(refname:short)%00%(objectname)%00%(upstream:short)%00%(HEAD)%00%(upstream:track)"


def parse_branches(text: str) -> list[Branch]:
    out: list[Branch] = []
    for line in text.splitlines():
        if not line:
            continue
        f = line.split("\0")
        if len(f) < 5:
            continue
        out.append(Branch(name=f[0], sha=f[1], upstream=f[2] or None,
                          is_head=f[3] == "*", track=f[4]))
    return out


def parse_remotes(text: str) -> list[Remote]:
    """Parse `git remote -v`."""
    fetch: dict[str, str] = {}
    push: dict[str, str] = {}
    order: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^(\S+)\s+(.*?)\s+\((fetch|push)\)$", line)
        if not m:
            continue
        name, url, kind = m.groups()
        if name not in order:
            order.append(name)
        (fetch if kind == "fetch" else push)[name] = url
    return [Remote(n, fetch.get(n, push.get(n, "")), push.get(n, fetch.get(n, "")))
            for n in order]


# -- trees, blame ------------------------------------------------------------

def parse_ls_tree(data: str) -> list[TreeEntry]:
    """Parse `git ls-tree -r -z --long`."""
    out: list[TreeEntry] = []
    for rec in data.split("\0"):
        if not rec:
            continue
        meta, _, path = rec.partition("\t")
        parts = meta.split()
        if len(parts) < 3:
            continue
        size = None
        if len(parts) >= 4 and parts[3] != "-":
            size = _int(parts[3])
        out.append(TreeEntry(path=path, mode=parts[0], kind=parts[1], sha=parts[2], size=size))
    return out


def parse_blame_porcelain(text: str) -> list[BlameLine]:
    lines: list[BlameLine] = []
    info: dict[str, dict[str, str]] = {}
    cur_sha = None
    cur_final = 0
    for raw in text.split("\n"):
        if raw.startswith("\t"):
            meta = info.get(cur_sha or "", {})
            lines.append(BlameLine(
                sha=cur_sha or "", author=meta.get("author", ""),
                author_time=_int(meta.get("author-time", "0")),
                summary=meta.get("summary", ""), line_no=cur_final, text=raw[1:]))
            continue
        m = re.match(r"^([0-9a-f]{40,64}) (\d+) (\d+)", raw)
        if m:
            cur_sha = m.group(1)
            cur_final = int(m.group(3))
            info.setdefault(cur_sha, {})
            continue
        if cur_sha and " " in raw:
            key, _, value = raw.partition(" ")
            info[cur_sha].setdefault(key, value)
    return lines


# -- helpers -----------------------------------------------------------------

def is_probably_binary(data: bytes) -> bool:
    return b"\0" in data[:8000]
