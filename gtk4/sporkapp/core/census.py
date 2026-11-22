"""Census-style health check for bookmarked repositories.

Follows qdvc-repo-census: read-only apart from `git fetch`, one reachability
check per server before fetching, and servers that don't answer are skipped
(their last-known state is shown, and the repo is marked "unknown").
"""

from __future__ import annotations

import os
import socket
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .gitrunner import GitError, GitRunner
from .models import Level, Remote, RepoSummary
from .repo import Repository
from .urls import Endpoint, parse_remote_url, resolve_ssh_alias


@dataclass
class ServerStatus:
    key: str                 # host:port, or "local"
    host: str
    port: int | None
    online: bool | None      # None = not checked
    remotes: list[str] = field(default_factory=list)   # "repo/remote" labels
    error: str | None = None


def summarize(path: str, runner: GitRunner, *, fetch_errors: dict[str, str] | None = None,
              offline: list[str] | None = None) -> RepoSummary:
    """Local-only summary (no network) using the last fetched remote refs."""
    name = os.path.basename(path.rstrip("/")) or path
    if not os.path.isdir(path):
        return RepoSummary(path, name, Level.MISSING, reasons=["Folder not found"])
    repo = Repository(runner, path)
    try:
        status = repo.status()
    except (GitError, OSError) as e:
        return RepoSummary(path, name, Level.MISSING, reasons=[f"Not a Git repository ({e})"])

    s = RepoSummary(path, name, Level.OK)
    s.branch = status.branch.head
    s.detached = status.branch.head is None and status.branch.oid is not None
    s.upstream = status.branch.upstream
    s.operation = repo.operation_in_progress()
    for e in status.entries:
        if e.conflicted:
            s.conflicted += 1
        elif e.untracked:
            s.untracked += 1
        else:
            if e.index != ".":
                s.staged += 1
            if e.worktree != ".":
                s.modified += 1
    s.stashes = repo.stash_count()
    remotes: list[Remote] = repo.remotes()
    s.remotes = repo.remote_states(s.branch, remotes)
    s.unpushed_anywhere = repo.unpushed_anywhere() if remotes else 0
    s.fetch_errors = dict(fetch_errors or {})
    s.offline_remotes = list(offline or [])
    try:
        last = repo.git("log", "-1", "--format=%ct%x1f%s", check=False)
        if last.code == 0 and last.text.strip():
            t, _, subj = last.text.strip().partition("\x1f")
            s.last_commit_time = int(t)
            s.last_commit_subject = subj
    except (GitError, OSError):
        pass
    classify(s, has_remotes=bool(remotes))
    return s


def classify(s: RepoSummary, has_remotes: bool) -> None:
    r = s.reasons
    if s.operation:
        r.append(f"{s.operation.capitalize()} in progress")
    if s.detached:
        r.append("Detached HEAD")
    if s.conflicted:
        r.append(f"{s.conflicted} conflicted")
    if s.staged:
        r.append(f"{s.staged} staged")
    if s.modified:
        r.append(f"{s.modified} modified")
    if s.untracked:
        r.append(f"{s.untracked} untracked")
    if s.stashes:
        r.append(f"{s.stashes} stash{'es' if s.stashes != 1 else ''}")
    if has_remotes and s.unpushed_anywhere:
        r.append(f"{s.unpushed_anywhere} commit{'s' if s.unpushed_anywhere != 1 else ''} on no remote")
    for st in s.remotes:
        if st.remote in s.offline_remotes or st.remote in s.fetch_errors:
            continue
        if not st.exists and s.branch:
            r.append(f"{st.remote} has no {s.branch}")
        if st.ahead:
            r.append(f"{st.ahead} to push to {st.remote}")
        if st.behind:
            r.append(f"{st.behind} to pull from {st.remote}")
    if has_remotes and s.branch and not s.upstream:
        r.append("No upstream branch")
    if r:
        s.level = Level.ATTENTION
    elif s.offline_remotes or s.fetch_errors:
        s.level = Level.UNKNOWN
    else:
        s.level = Level.OK


# -- servers -------------------------------------------------------------------

def endpoint_for(remote: Remote) -> Endpoint:
    return parse_remote_url(remote.fetch_url)


def group_servers(repos: dict[str, list[Remote]], resolve_ssh: bool = True
                  ) -> tuple[dict[str, ServerStatus], dict[tuple[str, str], str]]:
    """Group every remote by server. Returns (servers by key, (repo, remote) -> key)."""
    servers: dict[str, ServerStatus] = {}
    index: dict[tuple[str, str], str] = {}
    alias_cache: dict[str, tuple[str, int | None]] = {}
    for repo_path, remotes in repos.items():
        rname = os.path.basename(repo_path.rstrip("/"))
        for rem in remotes:
            ep = endpoint_for(rem)
            if ep.is_local:
                key = "local:" + ep.path
                srv = servers.setdefault(key, ServerStatus(key, ep.path, None, None))
            else:
                host, port = ep.host, ep.effective_port
                if ep.scheme == "ssh" and resolve_ssh:
                    if host not in alias_cache:
                        alias_cache[host] = resolve_ssh_alias(host)
                    real, real_port = alias_cache[host]
                    host = real
                    port = ep.port or real_port or port
                key = f"{host.lower()}:{port}"
                srv = servers.setdefault(key, ServerStatus(key, host, port, None))
            srv.remotes.append(f"{rname}/{rem.name}")
            index[(repo_path, rem.name)] = key
    return servers, index


def probe(server: ServerStatus, timeout: float = 4.0) -> ServerStatus:
    """Online = the port accepts a TCP connection (local: the path exists)."""
    if server.key.startswith("local:"):
        path = os.path.expanduser(server.host)
        server.online = os.path.exists(path)
        if not server.online:
            server.error = "Path not found"
        return server
    try:
        with socket.create_connection((server.host, server.port or 443), timeout=timeout):
            server.online = True
    except OSError as e:
        server.online = False
        server.error = e.strerror or str(e)
    return server


def probe_all(servers: dict[str, ServerStatus], jobs: int = 8) -> None:
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        list(pool.map(probe, servers.values()))
