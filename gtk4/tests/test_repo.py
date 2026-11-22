import os
import unittest

from helpers import TempRepo, sh  # noqa: E402  (helpers sets sys.path)

from sporkapp.core.gitrunner import GitError, GitRunner, TrustedServer
from sporkapp.core.models import Level
from sporkapp.core.repo import LogQuery, Repository, SearchField, find_toplevel
from sporkapp.core import census


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.t = TempRepo()
        self.runner = GitRunner()
        self.repo = Repository(self.runner, self.t.path)

    def test_unborn_branch(self):
        self.assertEqual(self.repo.log(LogQuery()), [])
        st = self.repo.status()
        self.assertIsNone(st.branch.oid)
        self.assertEqual(st.branch.head, "main")
        self.t.write("a.txt", "hello\n")
        self.repo.stage(["a.txt"])
        self.assertEqual([e.path for e in self.repo.status().staged], ["a.txt"])
        self.repo.unstage(["a.txt"])   # no HEAD yet: uses rm --cached
        st = self.repo.status()
        self.assertEqual([e.path for e in st.untracked], ["a.txt"])

    def test_log_details_changes_and_diff(self):
        self.t.commit("a.txt", "one\ntwo\n", "First commit")
        self.t.write("a.txt", "one\n2\nthree\n")
        self.t.write("dir/b.txt", "bee\n")
        self.t.git("add", "-A")
        self.t.git("commit", "-q", "-m", "Second commit\n\nWith a body line.")
        self.t.git("tag", "v1")
        commits = self.repo.log(LogQuery())
        self.assertEqual([c.subject for c in commits], ["Second commit", "First commit"])
        self.assertIn("tag: refs/tags/v1", commits[0].refs)
        d = self.repo.commit_details(commits[0].sha)
        self.assertEqual(d.body, "With a body line.")
        changes = {c.path: c for c in self.repo.commit_changes(commits[0].sha)}
        self.assertEqual(changes["a.txt"].status, "M")
        self.assertEqual(changes["dir/b.txt"].status, "A")
        self.assertEqual((changes["a.txt"].additions, changes["a.txt"].deletions), (2, 1))
        root_changes = self.repo.commit_changes(commits[1].sha)
        self.assertEqual([c.path for c in root_changes], ["a.txt"])
        diffs = self.repo.commit_diff(commits[0].sha, changes["a.txt"])
        self.assertEqual(len(diffs), 1)
        kinds = [l.kind for h in diffs[0].hunks for l in h.lines]
        self.assertEqual(kinds.count("+"), 2)
        self.assertEqual(kinds.count("-"), 1)

    def test_search(self):
        self.t.commit("a.txt", "1", "Add parser")
        self.t.git("-c", "user.name=Grace", "commit", "-q", "--allow-empty",
                   "--author=Grace Hopper <grace@example.org>", "-m", "Tweak lexer")
        self.t.commit("b.txt", "2", "Docs")
        msg = self.repo.log(LogQuery(text="PARSER", field=SearchField.MESSAGE))
        self.assertEqual([c.subject for c in msg], ["Add parser"])
        auth = self.repo.log(LogQuery(text="grace", field=SearchField.AUTHOR))
        self.assertEqual([c.subject for c in auth], ["Tweak lexer"])
        anyq = self.repo.log(LogQuery(text="grace", field=SearchField.ANY))
        self.assertEqual([c.subject for c in anyq], ["Tweak lexer"])
        path = self.repo.log(LogQuery(text="b.txt", field=SearchField.PATH))
        self.assertEqual([c.subject for c in path], ["Docs"])
        sha = self.t.git("rev-parse", "HEAD~2").strip()
        byhash = self.repo.log(LogQuery(text=sha[:7], field=SearchField.HASH))
        self.assertEqual([c.subject for c in byhash], ["Add parser"])
        anyhash = self.repo.log(LogQuery(text=sha[:7], field=SearchField.ANY))
        self.assertEqual(anyhash[0].subject, "Add parser")

    def test_hunk_staging_and_discard(self):
        lines = [f"line {i}\n" for i in range(1, 41)]
        self.t.commit("f.txt", "".join(lines), "base")
        lines[1] = "CHANGED 2\n"
        lines[35] = "CHANGED 36\n"
        self.t.write("f.txt", "".join(lines))
        fd = self.repo.unstaged_diff("f.txt")[0]
        self.assertEqual(len(fd.hunks), 2)
        self.repo.stage_hunk(fd, 1)
        staged = self.repo.staged_diff("f.txt")[0]
        self.assertEqual(len(staged.hunks), 1)
        self.assertIn("CHANGED 36", staged.hunks[0].patch_text())
        unstaged = self.repo.unstaged_diff("f.txt")[0]
        self.assertEqual(len(unstaged.hunks), 1)
        self.assertIn("CHANGED 2", unstaged.hunks[0].patch_text())
        self.repo.discard_hunk(unstaged, 0)
        self.assertEqual(self.repo.unstaged_diff("f.txt"), [])
        self.repo.unstage_hunk(staged, 0)
        self.assertEqual(self.repo.staged_diff("f.txt"), [])
        # all changes gone? only the unstaged hunk-2 change remains
        rest = self.repo.unstaged_diff("f.txt")[0]
        self.assertIn("CHANGED 36", rest.hunks[0].patch_text())

    def test_untracked_diff_and_discard(self):
        self.t.commit("a.txt", "a\n", "base")
        self.t.write("new.txt", "brand new\n")
        fd = self.repo.unstaged_diff("new.txt", untracked=True)[0]
        self.assertEqual(fd.new_path, "new.txt")
        self.assertEqual(fd.additions, 1)
        self.repo.discard([], ["new.txt"])
        self.assertFalse(os.path.exists(os.path.join(self.t.path, "new.txt")))

    def test_commit_and_amend(self):
        self.t.commit("a.txt", "a\n", "base")
        self.t.write("a.txt", "b\n")
        self.repo.stage(["a.txt"])
        sha = self.repo.commit("Change a\n\nDetails here\n")
        self.assertEqual(self.repo.commit_details(sha).subject, "Change a")
        self.repo.commit("Change a, better", amend=True)
        self.assertEqual(self.repo.last_commit_message(), "Change a, better")
        self.assertEqual(len(self.repo.log(LogQuery())), 2)

    def test_branches(self):
        self.t.commit("a.txt", "a\n", "base")
        self.repo.create_branch("feature/x")
        self.assertEqual(self.repo.current_branch(), "feature/x")
        self.repo.switch("main")
        names = {b.name: b for b in self.repo.branches()}
        self.assertTrue(names["main"].is_head)
        self.assertIn("feature/x", names)
        with self.assertRaises(GitError):
            self.repo.create_branch("bad name")
        self.repo.delete_branch("feature/x")
        self.assertNotIn("feature/x", {b.name for b in self.repo.branches()})

    def test_tree_blob_blame_history(self):
        self.t.commit("src/main.c", "int main;\n", "add main")
        self.t.commit("src/main.c", "int main;\nreturn 0;\n", "extend main")
        self.t.commit("README", "hi\n", "readme")
        head = self.repo.head_sha()
        entries = {e.path: e for e in self.repo.tree(head)}
        self.assertEqual(set(entries), {"src/main.c", "README"})
        self.assertEqual(entries["README"].size, 3)
        self.assertEqual(self.repo.blob(head, "README"), b"hi\n")
        blame = self.repo.blame("src/main.c", head)
        self.assertEqual([b.summary for b in blame], ["add main", "extend main"])
        self.assertEqual([b.line_no for b in blame], [1, 2])
        hist = self.repo.file_history("src/main.c")
        self.assertEqual([c.subject for c in hist], ["extend main", "add main"])

    def test_find_toplevel(self):
        self.t.commit("a/b/c.txt", "x", "deep")
        top = find_toplevel(self.runner, os.path.join(self.t.path, "a", "b"))
        self.assertEqual(os.path.realpath(top), os.path.realpath(self.t.path))
        self.assertIsNone(find_toplevel(self.runner, "/"))


class MultiRemoteTests(unittest.TestCase):
    """Two bare 'servers' (alpha, bravo) and a working clone."""

    def setUp(self):
        self.work = TempRepo()
        self.alpha = TempRepo(bare=True, root=self.work.root, name="alpha.git")
        self.bravo = TempRepo(bare=True, root=self.work.root, name="bravo.git")
        self.work.commit("a.txt", "a\n", "base")
        self.work.git("remote", "add", "alpha", self.alpha.path)
        self.work.git("remote", "add", "bravo", self.bravo.path)
        self.work.git("push", "-q", "-u", "alpha", "main")
        self.work.git("push", "-q", "bravo", "main")
        self.runner = GitRunner()
        self.repo = Repository(self.runner, self.work.path)

    def test_remote_states_and_census(self):
        s = census.summarize(self.work.path, self.runner)
        self.assertEqual(s.level, Level.OK, s.reasons)
        self.work.commit("b.txt", "b\n", "local only")
        self.repo.push("alpha", "main")
        states = {r.remote: r for r in self.repo.remote_states("main")}
        self.assertEqual((states["alpha"].ahead, states["alpha"].behind), (0, 0))
        self.assertEqual((states["bravo"].ahead, states["bravo"].behind), (1, 0))
        s = census.summarize(self.work.path, self.runner)
        self.assertEqual(s.level, Level.ATTENTION)
        self.assertIn("1 to push to bravo", s.reasons)
        self.assertEqual(s.unpushed_anywhere, 0)

    def test_fetch_pull_and_divergence(self):
        other_root = TempRepo(root=self.work.root, name="unused")
        other = os.path.join(self.work.root, "other")
        sh(self.work.root, "clone", "-q", "-o", "bravo", self.bravo.path, other)
        with open(os.path.join(other, "c.txt"), "w") as fh:
            fh.write("c\n")
        sh(other, "add", "c.txt")
        sh(other, "commit", "-q", "-m", "from elsewhere")
        sh(other, "push", "-q", "bravo", "main")
        self.repo.fetch("bravo")
        states = {r.remote: r for r in self.repo.remote_states("main")}
        self.assertEqual(states["bravo"].behind, 1)
        s = census.summarize(self.work.path, self.runner)
        self.assertIn("1 to pull from bravo", s.reasons)
        self.repo.pull("bravo", "main")
        self.assertTrue(os.path.exists(os.path.join(self.work.path, "c.txt")))
        # now alpha is behind us
        states = {r.remote: r for r in self.repo.remote_states("main")}
        self.assertEqual(states["alpha"].ahead, 1)
        del other_root

    def test_offline_marks_unknown(self):
        s = census.summarize(self.work.path, self.runner, offline=["bravo"])
        self.assertEqual(s.level, Level.UNKNOWN)

    def test_server_grouping_and_probe(self):
        servers, index = census.group_servers({self.work.path: self.repo.remotes()},
                                              resolve_ssh=False)
        self.assertEqual(len(servers), 2)
        census.probe_all(servers)
        self.assertTrue(all(s.online for s in servers.values()))

    def test_missing_folder(self):
        s = census.summarize(os.path.join(self.work.root, "nope"), self.runner)
        self.assertEqual(s.level, Level.MISSING)


class CertificateFlagTests(unittest.TestCase):
    def test_git_applies_sslcainfo_by_url(self):
        runner = GitRunner(servers=[TrustedServer("https://myserver.local:3000/", "/tmp/my.pem")])
        res = runner.run(["config", "--get-urlmatch", "http.sslCAInfo",
                          "https://myserver.local:3000/blair/dotfiles.git"])
        self.assertEqual(res.text.strip(), "/tmp/my.pem")
        other = runner.run(["config", "--get-urlmatch", "http.sslCAInfo",
                            "https://myserver.local/blair/dotfiles.git"], check=False)
        self.assertNotEqual(other.text.strip(), "/tmp/my.pem")


if __name__ == "__main__":
    unittest.main()
