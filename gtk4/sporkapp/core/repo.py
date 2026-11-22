"""High-level operations on one repository.

All methods block; the UI calls them from worker threads.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from . import parsers
from .gitrunner import GitError, GitRunner
from .models import (
    BlameLine, Branch, Commit, FileChange, FileDiff, Remote, RemoteBranchState,
    TreeEntry, WorkingStatus,
)

MAX_BLOB_BYTES = 4 * 1024 * 1024   # don't load files bigger than this into a text view


class SearchField:
    ANY = "any"
    MESSAGE = "message"
    AUTHOR = "author"
    HASH = "hash"
    PATH = "path"


@dataclass
class LogQuery:
    all_branches: bool = False
    text: str = ""
    field: str = SearchField.ANY
    limit: int = 400
    skip: int = 0
    path: str | None = None      # file history


def find_toplevel(runner: GitRunner, path: str) -> str | None:
    """The repository root containing `path`, or None if it isn't in one."""
    if not os.path.isdir(path):
        path = os.path.dirname(path) or "."
    try:
        res = runner.run(["rev-parse", "--show-toplevel"], cwd=path)
    except (GitError, OSError):
        return None
    top = res.text.strip()
    return top or None


class Repository:
    def __init__(self, runner: GitRunner, path: str):
        self.runner = runner
        self.path = path

    @property
    def name(self) -> str:
        return os.path.basename(self.path.rstrip("/")) or self.path

    def git(self, *args: str, **kw):
        return self.runner.run(list(args), cwd=self.path, **kw)

    # -- state ----------------------------------------------------------------

    def git_dir(self) -> str:
        d = self.git("rev-parse", "--absolute-git-dir").text.strip()
        return d

    def operation_in_progress(self) -> str | None:
        """Name of an unfinished merge/rebase/... or None."""
        try:
            gd = self.git_dir()
        except GitError:
            return None
        checks = [
            ("rebase-merge", "rebase"), ("rebase-apply/applying", "am"),
            ("rebase-apply", "rebase"), ("MERGE_HEAD", "merge"),
            ("CHERRY_PICK_HEAD", "cherry-pick"), ("REVERT_HEAD", "revert"),
            ("BISECT_LOG", "bisect"),
        ]
        for name, label in checks:
            if os.path.exists(os.path.join(gd, name)):
                return label
        return None

    def status(self) -> WorkingStatus:
        res = self.git("status", "--porcelain=v2", "--branch", "-z",
                       "--untracked-files=all")
        return parsers.parse_status_v2(res.text)

    def stash_count(self) -> int:
        try:
            res = self.git("rev-list", "--walk-reflogs", "--count", "refs/stash",
                           check=False)
        except OSError:
            return 0
        return int(res.text.strip() or 0) if res.code == 0 else 0

    def head_sha(self) -> str | None:
        res = self.git("rev-parse", "--verify", "-q", "HEAD", check=False)
        return res.text.strip() or None if res.code == 0 else None

    def current_branch(self) -> str | None:
        res = self.git("symbolic-ref", "-q", "--short", "HEAD", check=False)
        return res.text.strip() or None if res.code == 0 else None

    # -- history ----------------------------------------------------------------

    def log(self, q: LogQuery) -> list[Commit]:
        text = q.text.strip()
        if text and q.field == SearchField.HASH:
            return self._log_by_hash(text)
        if text and q.field == SearchField.ANY:
            # git ANDs --grep with --author, so "any" runs both and merges.
            seen: set[str] = set()
            merged: list[Commit] = []
            for fld in (SearchField.MESSAGE, SearchField.AUTHOR):
                sub = LogQuery(q.all_branches, text, fld, q.limit, 0, q.path)
                for c in self.log(sub):
                    if c.sha not in seen:
                        seen.add(c.sha)
                        merged.append(c)
            merged.sort(key=lambda c: c.committer_time, reverse=True)
            if _looks_like_hash(text):
                merged = [c for c in self._log_by_hash(text) if c.sha not in seen] + merged
            return merged[:q.limit]

        args = ["log", "--topo-order", "--decorate=full",
                f"--format={parsers.LOG_FORMAT}", f"--max-count={q.limit}"]
        if q.skip:
            args.append(f"--skip={q.skip}")
        path = q.path
        if text:
            if q.field == SearchField.AUTHOR:
                args += ["-i", "--fixed-strings", f"--author={text}"]
            elif q.field == SearchField.MESSAGE:
                args += ["-i", "--fixed-strings", f"--grep={text}"]
            elif q.field == SearchField.PATH:
                path = text
        if q.all_branches:
            args += ["--branches", "--remotes", "--tags", "HEAD"]
        elif self.head_sha() is None:
            return []
        if path:
            args += ["--", path]
        res = self.git(*args, check=False)
        if res.code != 0:
            if self.head_sha() is None:
                return []
            raise GitError(args, res.code, res.err)
        return parsers.parse_log(res.text)

    def _log_by_hash(self, text: str) -> list[Commit]:
        res = self.git("rev-parse", "--verify", "-q", f"{text}^{{commit}}", check=False)
        if res.code != 0:
            return []
        sha = res.text.strip()
        out = self.git("log", "--decorate=full", f"--format={parsers.LOG_FORMAT}",
                       "-n", "1", sha)
        return parsers.parse_log(out.text)

    def commit_details(self, sha: str) -> Commit:
        res = self.git("show", "-s", "--decorate=full",
                       f"--format={parsers.DETAIL_FORMAT}", sha)
        commits = parsers.parse_log(res.text, with_body=True)
        if not commits:
            raise GitError(["show", sha], 1, f"Commit {sha} not found")
        return commits[0]

    def commit_changes(self, sha: str) -> list[FileChange]:
        """Files changed by a commit, against its first parent."""
        base = ["diff-tree", "-r", "-z", "-M", "--root", "--no-commit-id",
                "--diff-merges=first-parent"]
        ns = self.git(*base, "--name-status", sha).text
        st = self.git(*base, "--numstat", sha).text
        return parsers.merge_numstat(parsers.parse_name_status(ns), parsers.parse_numstat(st))

    def commit_diff(self, sha: str, change: FileChange) -> list[FileDiff]:
        paths = [change.path]
        if change.old_path and change.old_path != change.path:
            paths.insert(0, change.old_path)
        res = self.git("diff-tree", "-p", "-M", "--root", "--no-commit-id",
                       "--diff-merges=first-parent", sha, "--", *paths)
        return parsers.parse_diff(res.text)

    def file_history(self, path: str, rev: str = "HEAD", limit: int = 300) -> list[Commit]:
        res = self.git("log", "--follow", "--decorate=full",
                       f"--format={parsers.LOG_FORMAT}", f"--max-count={limit}",
                       rev, "--", path)
        return parsers.parse_log(res.text)

    def blame(self, path: str, rev: str) -> list[BlameLine]:
        res = self.git("blame", "--porcelain", rev, "--", path)
        return parsers.parse_blame_porcelain(res.text)

    # -- trees ----------------------------------------------------------------

    def tree(self, rev: str) -> list[TreeEntry]:
        res = self.git("ls-tree", "-r", "-z", "--long", rev)
        return parsers.parse_ls_tree(res.text)

    def blob(self, rev: str, path: str) -> bytes:
        res = self.git("cat-file", "blob", f"{rev}:{path}")
        return res.stdout

    def blob_size(self, rev: str, path: str) -> int:
        res = self.git("cat-file", "-s", f"{rev}:{path}")
        return int(res.text.strip() or 0)

    # -- working copy ---------------------------------------------------------

    def unstaged_diff(self, path: str, untracked: bool = False) -> list[FileDiff]:
        if untracked:
            res = self.git("diff", "--no-index", "--", "/dev/null", path,
                           ok_codes=(0, 1))
        else:
            res = self.git("diff", "--", path)
        return parsers.parse_diff(res.text)

    def staged_diff(self, path: str, old_path: str | None = None) -> list[FileDiff]:
        paths = [p for p in (old_path, path) if p]
        res = self.git("diff", "--cached", "-M", "--", *paths)
        return parsers.parse_diff(res.text)

    def stage(self, paths: list[str]) -> None:
        if paths:
            self.git("add", "-A", "--", *paths)

    def unstage(self, paths: list[str]) -> None:
        if not paths:
            return
        if self.head_sha() is None:
            self.git("rm", "--cached", "-r", "-q", "--", *paths)
        else:
            self.git("restore", "--staged", "--", *paths)

    def discard(self, tracked: list[str], untracked: list[str]) -> None:
        """Throw away work-tree changes. Untracked files are deleted."""
        if tracked:
            self.git("restore", "--worktree", "--", *tracked)
        for p in untracked:
            full = os.path.join(self.path, p)
            if os.path.isfile(full) or os.path.islink(full):
                os.remove(full)

    def apply_patch(self, patch: str, *, cached: bool, reverse: bool) -> None:
        args = ["apply", "--whitespace=nowarn", "--recount"]
        if cached:
            args.append("--cached")
        if reverse:
            args.append("--reverse")
        args.append("-")
        self.git(*args, input=patch.encode("utf-8"))

    def stage_hunk(self, fd: FileDiff, index: int) -> None:
        self.apply_patch(fd.patch_for_hunk(index), cached=True, reverse=False)

    def unstage_hunk(self, fd: FileDiff, index: int) -> None:
        self.apply_patch(fd.patch_for_hunk(index), cached=True, reverse=True)

    def discard_hunk(self, fd: FileDiff, index: int) -> None:
        self.apply_patch(fd.patch_for_hunk(index), cached=False, reverse=True)

    def commit(self, message: str, amend: bool = False) -> str:
        args = ["commit", "-q", "--cleanup=strip", "-F", "-"]
        if amend:
            args.append("--amend")
        self.git(*args, input=message.encode("utf-8"))
        return self.head_sha() or ""

    def last_commit_message(self) -> str:
        res = self.git("log", "-1", "--format=%B", check=False)
        return res.text.strip() if res.code == 0 else ""

    # -- branches ---------------------------------------------------------------

    def branches(self) -> list[Branch]:
        res = self.git("for-each-ref", f"--format={parsers.BRANCH_FORMAT}",
                       "--sort=-committerdate", "refs/heads")
        return parsers.parse_branches(res.text)

    def switch(self, name: str) -> None:
        self.git("switch", "--no-guess", name)

    def create_branch(self, name: str, start: str | None = None, switch: bool = True) -> None:
        self.git("check-ref-format", "--branch", name)
        if switch:
            self.git("switch", "-c", name, *( [start] if start else [] ))
        else:
            self.git("branch", name, *( [start] if start else [] ))

    def delete_branch(self, name: str, force: bool = False) -> None:
        self.git("branch", "-D" if force else "-d", name)

    # -- remotes ----------------------------------------------------------------

    def remotes(self) -> list[Remote]:
        return parsers.parse_remotes(self.git("remote", "-v").text)

    def remote_states(self, branch: str | None, remotes: list[Remote] | None = None
                      ) -> list[RemoteBranchState]:
        """Ahead/behind of `branch` against `<remote>/<branch>` for every remote,
        using whatever remote-tracking refs the last fetch left."""
        if remotes is None:
            remotes = self.remotes()
        states: list[RemoteBranchState] = []
        for r in remotes:
            if not branch:
                states.append(RemoteBranchState(r.name, exists=False))
                continue
            ref = f"refs/remotes/{r.name}/{branch}"
            ok = self.git("rev-parse", "--verify", "-q", ref, check=False).code == 0
            if not ok:
                states.append(RemoteBranchState(r.name, exists=False))
                continue
            res = self.git("rev-list", "--left-right", "--count",
                           f"refs/heads/{branch}...{ref}", check=False)
            ahead = behind = 0
            if res.code == 0:
                parts = res.text.split()
                if len(parts) == 2:
                    ahead, behind = int(parts[0]), int(parts[1])
            states.append(RemoteBranchState(r.name, True, ahead, behind))
        return states

    def unpushed_anywhere(self) -> int:
        """Commits reachable from HEAD that no remote-tracking ref contains."""
        if self.head_sha() is None:
            return 0
        res = self.git("rev-list", "--count", "HEAD", "--not", "--remotes", check=False)
        return int(res.text.strip() or 0) if res.code == 0 else 0

    def fetch(self, remote: str, prune: bool = False) -> str:
        args = ["-c", "maintenance.auto=false", "-c", "gc.auto=0",
                "fetch", "--recurse-submodules=no", "--no-write-fetch-head"]
        if prune:
            args.append("--prune")
        args.append(remote)
        res = self.runner.run(args, cwd=self.path, network=True)
        return res.err.strip()

    def pull(self, remote: str, branch: str, mode: str = "ff-only") -> str:
        """mode: 'ff-only', 'merge' or 'rebase'."""
        flag = {"ff-only": "--ff-only", "merge": "--no-rebase", "rebase": "--rebase"}[mode]
        args = ["-c", "maintenance.auto=false", "-c", "gc.auto=0",
                "pull", "--recurse-submodules=no", flag, remote, branch]
        res = self.runner.run(args, cwd=self.path, network=True)
        return (res.text + res.err).strip()

    def push(self, remote: str, branch: str, set_upstream: bool = False) -> str:
        args = ["push", "--recurse-submodules=no", "--porcelain"]
        if set_upstream:
            args.append("--set-upstream")
        args += [remote, f"refs/heads/{branch}:refs/heads/{branch}"]
        res = self.runner.run(args, cwd=self.path, network=True)
        return (res.text + res.err).strip()

    def ls_remote_check(self, remote_or_url: str) -> None:
        """Talks to a server the same way fetch does; raises GitError on failure."""
        self.runner.run(["ls-remote", "--heads", remote_or_url], cwd=self.path,
                        network=True, timeout=30)


def _looks_like_hash(text: str) -> bool:
    return 4 <= len(text) <= 64 and all(c in "0123456789abcdefABCDEF" for c in text)
