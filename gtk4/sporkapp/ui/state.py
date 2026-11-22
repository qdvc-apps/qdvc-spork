"""Application-wide state: settings, the git runner and repository summaries."""

from __future__ import annotations

import os
import stat
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from ..core import census
from ..core.gitrunner import GitError, GitRunner
from ..core.models import Level, RepoSummary
from ..core.repo import Repository
from ..core.settings import Settings
from .util import IN_FLATPAK, run_async

ENTRY_SCRIPT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "qdvc_spork.py"))


def askpass_path() -> str:
    """An executable that git and ssh can call to ask for a password."""
    if IN_FLATPAK and os.path.exists("/app/bin/spork-askpass"):
        return "/app/bin/spork-askpass"
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    folder = os.path.join(base, "qdvc-spork")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "spork-askpass")
    script = (
        "#!/bin/sh\n"
        "# Written by QDVC Spork; asks for git/ssh passwords in a dialog.\n"
        f"exec '{sys.executable}' '{ENTRY_SCRIPT}' --askpass \"$@\"\n"
    )
    try:
        with open(path, "r", encoding="utf-8") as fh:
            current = fh.read()
    except OSError:
        current = ""
    if current != script:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(script)
    os.chmod(path, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
    return path


class FetchReport:
    def __init__(self):
        self.servers: dict[str, census.ServerStatus] = {}
        self.errors: dict[tuple[str, str], str] = {}     # (repo, remote) -> stderr
        self.fetched: list[tuple[str, str]] = []
        self.index: dict[tuple[str, str], str] = {}      # (repo, remote) -> server key
        self.finished_at: float = 0.0


class AppState:
    def __init__(self) -> None:
        self.settings = Settings.load()
        self.runner = GitRunner()
        try:
            self.runner.askpass = askpass_path()
        except OSError:
            self.runner.askpass = None
        self.apply_settings()
        self.summaries: dict[str, RepoSummary] = {}
        self.last_fetch: FetchReport | None = None
        self.fetching = False
        self._listeners: list[Callable[[str], None]] = []

    # -- observers --------------------------------------------------------------

    def connect(self, fn: Callable[[str], None]) -> None:
        """fn(topic) with topic in {'bookmarks', 'summaries', 'servers', 'fetch'}."""
        self._listeners.append(fn)

    def emit(self, topic: str) -> None:
        for fn in list(self._listeners):
            fn(topic)

    # -- settings ---------------------------------------------------------------

    def apply_settings(self) -> None:
        self.runner.servers = self.settings.trusted_servers()
        self.runner.network_timeout = float(self.settings.fetch_timeout)

    def save(self, topic: str = "bookmarks") -> None:
        self.settings.save()
        self.apply_settings()
        self.emit(topic)

    def add_bookmark(self, path: str) -> str:
        b = self.settings.add_bookmark(path)
        self.save("bookmarks")
        self.refresh_summary(b.path)
        return b.path

    def remove_bookmark(self, path: str) -> None:
        self.settings.remove_bookmark(path)
        self.summaries.pop(path, None)
        self.save("bookmarks")

    # -- summaries --------------------------------------------------------------

    def refresh_summary(self, path: str, done: Callable[[], None] | None = None) -> None:
        report = self.last_fetch

        def work():
            errs, off = _fetch_context(report, path)
            return census.summarize(path, self.runner, fetch_errors=errs, offline=off)

        def finish(summary: RepoSummary):
            self.summaries[path] = summary
            self.emit("summaries")
            if done:
                done()

        run_async(work, finish)

    def refresh_all_summaries(self) -> None:
        paths = [b.path for b in self.settings.bookmarks]
        report = self.last_fetch

        def work():
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(
                    lambda p: census.summarize(p, self.runner,
                                               fetch_errors=_fetch_context(report, p)[0],
                                               offline=_fetch_context(report, p)[1]),
                    paths))
            return results

        def finish(results: list[RepoSummary]):
            for s in results:
                self.summaries[s.path] = s
            self.emit("summaries")

        run_async(work, finish)

    # -- fetching ---------------------------------------------------------------

    def fetch(self, paths: list[str] | None = None,
              done: Callable[[FetchReport], None] | None = None) -> None:
        """Census-style fetch: probe each server once, fetch reachable remotes."""
        if self.fetching:
            return
        self.fetching = True
        self.emit("fetch")
        paths = paths if paths is not None else [b.path for b in self.settings.bookmarks]
        runner = self.runner

        def work() -> tuple[FetchReport, list[RepoSummary]]:
            report = FetchReport()
            remotes = {}
            for p in paths:
                if not os.path.isdir(p):
                    continue
                try:
                    remotes[p] = Repository(runner, p).remotes()
                except (GitError, OSError):
                    continue
            servers, index = census.group_servers(remotes)
            census.probe_all(servers)
            report.servers = servers
            failed_hosts: set[str] = set()
            jobs = [(p, r.name, index[(p, r.name)]) for p, rs in remotes.items() for r in rs]

            def do_fetch(job):
                p, name, key = job
                srv = servers[key]
                if not srv.online or key in failed_hosts:
                    return
                try:
                    Repository(runner, p).fetch(name)
                    report.fetched.append((p, name))
                except GitError as e:
                    report.errors[(p, name)] = e.stderr or str(e)
                    hint = (e.hint or "").lower()
                    if "didn't answer" in hint or "couldn't be resolved" in hint:
                        failed_hosts.add(key)
                except OSError as e:
                    report.errors[(p, name)] = str(e)

            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(do_fetch, jobs))
            report.finished_at = time.time()
            summaries = []
            for p in paths:
                errs, off = _fetch_context(report, p, servers_index=index)
                summaries.append(census.summarize(p, runner, fetch_errors=errs, offline=off))
            report.index = index
            return report, summaries

        def finish(result):
            report, summaries = result
            if self.last_fetch and paths != [b.path for b in self.settings.bookmarks]:
                # partial fetch: merge server info into the last full report
                self.last_fetch.servers.update(report.servers)
                self.last_fetch.errors = {k: v for k, v in self.last_fetch.errors.items()
                                          if k[0] not in paths}
                self.last_fetch.errors.update(report.errors)
                self.last_fetch.index.update(report.index)
            else:
                self.last_fetch = report
            for s in summaries:
                self.summaries[s.path] = s
            self.fetching = False
            self.emit("fetch")
            self.emit("summaries")
            if done:
                done(report)

        def fail(exc):
            self.fetching = False
            self.emit("fetch")

        run_async(work, finish, fail)

    # -- helpers ------------------------------------------------------------------

    def counts(self) -> dict[str, int]:
        c = {"total": 0, "ok": 0, "attention": 0, "unknown": 0, "missing": 0}
        for b in self.settings.bookmarks:
            c["total"] += 1
            s = self.summaries.get(b.path)
            if s is None:
                continue
            c[{Level.OK: "ok", Level.ATTENTION: "attention",
               Level.UNKNOWN: "unknown", Level.MISSING: "missing"}[s.level]] += 1
        return c

    def remote_urls_by_label(self) -> dict[str, str]:
        """{'repo: remote': url} across all bookmarks (for certificate matching)."""
        out = {}
        for b in self.settings.bookmarks:
            if not os.path.isdir(b.path):
                continue
            try:
                for r in Repository(self.runner, b.path).remotes():
                    out[f"{b.display_name}: {r.name}"] = r.fetch_url
            except (GitError, OSError):
                continue
        return out


def _fetch_context(report: FetchReport | None, path: str, servers_index=None
                   ) -> tuple[dict[str, str], list[str]]:
    if report is None:
        return {}, []
    errs = {remote: msg for (p, remote), msg in report.errors.items() if p == path}
    index = servers_index if servers_index is not None else report.index
    offline = [remote for (p, remote), key in index.items()
               if p == path and key in report.servers and report.servers[key].online is False]
    return errs, offline
