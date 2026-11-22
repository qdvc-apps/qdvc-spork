"""Test helpers: throwaway repositories built with the real git command."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

ENV = dict(os.environ,
           GIT_AUTHOR_NAME="Ada Tester", GIT_AUTHOR_EMAIL="ada@example.org",
           GIT_COMMITTER_NAME="Ada Tester", GIT_COMMITTER_EMAIL="ada@example.org",
           GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
# The app's own runner must not pick up the developer's ~/.gitconfig either.
os.environ.update({k: ENV[k] for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL",
                                        "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL",
                                        "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM")})


def sh(cwd: str, *args: str, input: str | None = None) -> str:
    res = subprocess.run(["git", *args], cwd=cwd, env=ENV, capture_output=True,
                         text=True, input=input)
    if res.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {res.stderr}")
    return res.stdout


def write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


class TempRepo:
    def __init__(self, bare: bool = False, root: str | None = None, name: str = "repo"):
        self.root = root or tempfile.mkdtemp(prefix="spork-test-")
        self.path = os.path.join(self.root, name)
        if bare:
            subprocess.run(["git", "init", "-q", "--bare", "-b", "main", self.path],
                           check=True, env=ENV)
        else:
            subprocess.run(["git", "init", "-q", "-b", "main", self.path], check=True, env=ENV)

    def git(self, *args: str, input: str | None = None) -> str:
        return sh(self.path, *args, input=input)

    def write(self, rel: str, text: str) -> None:
        write(os.path.join(self.path, rel), text)

    def commit(self, rel: str, text: str, message: str) -> str:
        self.write(rel, text)
        self.git("add", rel)
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD").strip()
