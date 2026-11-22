"""Runs the `git` command with the environment Spork needs.

Every git call Spork makes goes through `GitRunner.run`, so this is the one
place that decides:

* which per-server certificates git trusts (`-c http.<url>.sslCAInfo=...`),
* how git asks for passwords (our askpass helper, never a terminal),
* that output is stable for parsing (no colour, no pager, no quoted paths).
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
from dataclasses import dataclass, field


class GitError(Exception):
    def __init__(self, args: list[str], code: int, stderr: str, stdout: str = ""):
        self.args_ = args
        self.code = code
        self.stderr = stderr.strip()
        self.stdout = stdout
        super().__init__(self.stderr or f"git {' '.join(args)} exited with {code}")

    @property
    def hint(self) -> str | None:
        return failure_hint(self.stderr)


def failure_hint(stderr: str) -> str | None:
    """A short, human explanation for common git failures, or None."""
    s = stderr.lower()
    if "ssl certificate problem" in s or "self-signed certificate" in s or \
            "self signed certificate" in s or "unable to get local issuer" in s:
        return ("The server's HTTPS certificate isn't trusted. Add the server's "
                "cert.pem under Preferences › Servers & Certificates.")
    if "certificate subject name" in s or "no alternative certificate subject name" in s:
        return ("The certificate doesn't cover this host name. Use the exact name "
                "it was issued for (see Servers & Certificates).")
    if "authentication failed" in s or "could not read username" in s or \
            "could not read password" in s or "terminal prompts disabled" in s:
        return "Authentication failed. Check your user name, password or access token."
    if "permission denied (publickey" in s:
        return "The SSH server rejected your key. Is your ssh-agent running with the key loaded?"
    if "host key verification failed" in s:
        return "SSH couldn't verify the server's host key. Check ~/.ssh/known_hosts."
    if "could not resolve host" in s or "name or service not known" in s or \
            "nodename nor servname" in s:
        return "The server's name couldn't be resolved. Are you on the right network?"
    if "connection refused" in s or "timed out" in s or "no route to host" in s or \
            "network is unreachable" in s:
        return "The server didn't answer. It may be offline."
    if "non-fast-forward" in s or "fetch first" in s or "[rejected]" in s:
        return "The remote has commits you don't have. Pull first, then push again."
    if "not possible to fast-forward" in s or "diverging branches" in s:
        return "Your branch and the remote have diverged. Choose Merge or Rebase."
    if "please tell me who you are" in s:
        return "Git doesn't know your name and email. Set user.name and user.email."
    if "gpg failed to sign" in s or "cannot run gpg" in s:
        return "Commit signing failed. Spork can't reach gpg; commit from a terminal or disable commit.gpgsign."
    return None


@dataclass
class GitResult:
    code: int
    stdout: bytes
    stderr: bytes

    @property
    def text(self) -> str:
        return self.stdout.decode("utf-8", errors="replace")

    @property
    def err(self) -> str:
        return self.stderr.decode("utf-8", errors="replace")


@dataclass
class TrustedServer:
    url: str          # normalised, e.g. "https://myserver.local:3000/"
    cert_path: str


@dataclass
class GitRunner:
    git: str = field(default_factory=lambda: shutil.which("git") or "git")
    askpass: str | None = None
    servers: list[TrustedServer] = field(default_factory=list)
    default_timeout: float = 60.0
    network_timeout: float = 180.0

    # -- environment --------------------------------------------------------

    def environment(self, network: bool = False) -> dict[str, str]:
        env = dict(os.environ)
        env.update({
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_PAGER": "cat",
            "PAGER": "cat",
            "GIT_EDITOR": "true",
            "GIT_MERGE_AUTOEDIT": "no",
            "GIT_OPTIONAL_LOCKS": "0",
            "LC_ALL": "C.UTF-8",
            "LANGUAGE": "C",
        })
        # Inside Flatpak, XDG_CONFIG_HOME points at ~/.var/app/<id>/config, so
        # git would miss ~/.config/git/config and ~/.config/git/ignore. Point
        # git (and only git) back at the host's config folder.
        if os.environ.get("FLATPAK_ID"):
            host_cfg = os.environ.get("HOST_XDG_CONFIG_HOME") or \
                os.path.join(os.path.expanduser("~"), ".config")
            env["XDG_CONFIG_HOME"] = host_cfg
        if self.askpass:
            env["GIT_ASKPASS"] = self.askpass
            env["SSH_ASKPASS"] = self.askpass
            env["SSH_ASKPASS_REQUIRE"] = "force"
        else:
            env.pop("SSH_ASKPASS_REQUIRE", None)
        # Deliberately no GIT_SSH_COMMAND: it would override the user's own
        # core.sshCommand. Hung connections are handled by the timeout.
        return env

    def config_flags(self) -> list[str]:
        flags = [
            "-c", "core.quotepath=false",
            "-c", "color.ui=false",
            "-c", "core.pager=cat",
            "-c", "log.showSignature=false",
            "-c", "diff.noprefix=false",
            "-c", "diff.mnemonicPrefix=false",
        ]
        for srv in self.servers:
            flags += ["-c", f"http.{srv.url}.sslCAInfo={srv.cert_path}"]
        return flags

    # -- running ------------------------------------------------------------

    def run(self, args: list[str], cwd: str | None = None, *,
            input: bytes | None = None, check: bool = True,
            network: bool = False, timeout: float | None = None,
            ok_codes: tuple[int, ...] = (0,)) -> GitResult:
        cmd = [self.git] + self.config_flags() + args
        if timeout is None:
            timeout = self.network_timeout if network else self.default_timeout
        proc = subprocess.Popen(
            cmd, cwd=cwd, env=self.environment(network),
            stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True,  # so a timeout can kill ssh children too
        )
        try:
            out, err = proc.communicate(input=input, timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                proc.kill()
            out, err = proc.communicate()
            raise GitError(args, -1, f"git {args[0]} timed out after {int(timeout)} s")
        result = GitResult(proc.returncode, out, err)
        if check and proc.returncode not in ok_codes:
            raise GitError(args, proc.returncode, result.err, result.text)
        return result

    def version(self) -> str | None:
        try:
            return self.run(["--version"]).text.strip()
        except (GitError, OSError):
            return None
