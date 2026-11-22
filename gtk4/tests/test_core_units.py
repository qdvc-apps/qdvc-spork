import datetime as dt
import os
import shutil
import subprocess
import tempfile
import unittest

import helpers  # noqa: F401  (sets sys.path)

from sporkapp.core import certs, parsers
from sporkapp.core.graph import layout
from sporkapp.core.models import Commit, RefKind
from sporkapp.core.settings import Settings
from sporkapp.core.urls import normalise_server_url, parse_remote_url


def C(sha, *parents):
    return Commit(sha, list(parents), "a", "a@x", 0, "a", "a@x", 0, [], sha)


class ParserTests(unittest.TestCase):
    def test_status_v2(self):
        data = "\0".join([
            "# branch.oid 1234567890abcdef1234567890abcdef12345678",
            "# branch.head main",
            "# branch.upstream origin/main",
            "# branch.ab +2 -1",
            "1 M. N... 100644 100644 100644 aaa bbb staged.txt",
            "1 .M N... 100644 100644 100644 aaa bbb dir/with space.txt",
            "2 R. N... 100644 100644 100644 aaa bbb R100 new.txt", "old.txt",
            "u UU N... 100644 100644 100644 100644 a b c conflict.txt",
            "? untracked.txt",
            "",
        ])
        st = parsers.parse_status_v2(data)
        self.assertEqual(st.branch.head, "main")
        self.assertEqual((st.branch.ahead, st.branch.behind), (2, 1))
        self.assertEqual([e.path for e in st.staged], ["staged.txt", "new.txt"])
        self.assertEqual([e.path for e in st.unstaged],
                         ["dir/with space.txt", "conflict.txt", "untracked.txt"])
        self.assertEqual(st.entries[2].old_path, "old.txt")

    def test_detached_and_initial(self):
        st = parsers.parse_status_v2("# branch.oid (initial)\0# branch.head (detached)\0")
        self.assertIsNone(st.branch.oid)
        self.assertIsNone(st.branch.head)

    def test_name_status_and_numstat(self):
        ns = "M\0a.txt\0R087\0old.c\0new.c\0D\0gone\0"
        changes = parsers.parse_name_status(ns)
        self.assertEqual([(c.status, c.path, c.old_path) for c in changes],
                         [("M", "a.txt", None), ("R", "new.c", "old.c"), ("D", "gone", None)])
        num = "3\t1\ta.txt\0" + "5\t2\t\0old.c\0new.c\0" + "-\t-\tgone\0"
        stats = parsers.parse_numstat(num)
        self.assertEqual(stats, {"a.txt": (3, 1), "new.c": (5, 2), "gone": (None, None)})

    def test_refs(self):
        labels = parsers.parse_refs(["HEAD -> refs/heads/main", "tag: refs/tags/v1",
                                     "refs/remotes/origin/main", "refs/remotes/origin/HEAD",
                                     "refs/heads/feat"])
        self.assertEqual([(l.name, l.kind) for l in labels], [
            ("main", RefKind.HEAD), ("v1", RefKind.TAG),
            ("origin/main", RefKind.REMOTE), ("feat", RefKind.BRANCH)])

    def test_diff_with_no_newline_and_binary(self):
        text = "\n".join([
            "diff --git a/x.txt b/x.txt",
            "index 1..2 100644",
            "--- a/x.txt",
            "+++ b/x.txt",
            "@@ -1,2 +1,2 @@",
            " keep",
            "-old",
            "\\ No newline at end of file",
            "+new",
            "\\ No newline at end of file",
            "diff --git a/img.png b/img.png",
            "index 3..4 100644",
            "Binary files a/img.png and b/img.png differ",
            "",
        ])
        files = parsers.parse_diff(text)
        self.assertEqual(len(files), 2)
        h = files[0].hunks[0]
        self.assertEqual([(l.kind, l.old_no, l.new_no) for l in h.lines],
                         [(" ", 1, 1), ("-", 2, None), ("\\", None, None),
                          ("+", None, 2), ("\\", None, None)])
        self.assertTrue(files[1].binary)
        self.assertEqual(files[1].new_path, "img.png")

    def test_remotes(self):
        text = ("origin\thttps://a/x.git (fetch)\norigin\thttps://a/x.git (push)\n"
                "nas\tssh://git@nas:222/x.git (fetch)\nnas\tssh://git@nas:222/x.git (push)\n")
        rs = parsers.parse_remotes(text)
        self.assertEqual([r.name for r in rs], ["origin", "nas"])

    def test_unquote(self):
        self.assertEqual(parsers._unquote_c(r"caf\303\251 \"x\"\tz"), 'café "x"\tz')


class GraphTests(unittest.TestCase):
    def test_linear(self):
        rows = layout([C("c", "b"), C("b", "a"), C("a")])
        self.assertEqual([r.node for r in rows], [0, 0, 0])
        self.assertEqual(rows[2].bottom, [])

    def test_merge_and_branch(self):
        # m merges f into main; history:  m -> (b, f); f -> a; b -> a
        rows = layout([C("m", "b", "f"), C("f", "a"), C("b", "a"), C("a")])
        self.assertEqual(rows[0].node, 0)
        self.assertEqual(sorted(b for _, b, _ in rows[0].bottom), [0, 1])
        self.assertEqual(rows[1].node, 1)
        self.assertEqual(rows[2].node, 0)
        # f's lane is pulled left into b's lane on the way down to a
        self.assertIn((1, 0), [(a, b) for a, b, _ in rows[2].bottom])
        self.assertEqual(rows[3].node, 0)
        self.assertEqual([a for a, _, _ in rows[3].top], [0])
        self.assertEqual(max(r.width for r in rows), 2)


class UrlTests(unittest.TestCase):
    def test_parse(self):
        e = parse_remote_url("https://myserver.local:3000/blair/x.git")
        self.assertEqual((e.scheme, e.host, e.port, e.https_base),
                         ("https", "myserver.local", 3000, "https://myserver.local:3000/"))
        e = parse_remote_url("git@github.com:qdvc-apps/x.git")
        self.assertEqual((e.scheme, e.host, e.user, e.effective_port), ("ssh", "github.com", "git", 22))
        e = parse_remote_url("ssh://git@myserver.local:222/blair/x.git")
        self.assertEqual(e.server_key, "myserver.local:222")
        self.assertTrue(parse_remote_url("/srv/git/x.git").is_local)
        self.assertTrue(parse_remote_url("../x.git").is_local)

    def test_normalise(self):
        self.assertEqual(normalise_server_url("MyServer.local:3000"), "https://myserver.local:3000/")
        self.assertEqual(normalise_server_url("https://h/sub"), "https://h/sub/")


@unittest.skipUnless(shutil.which("openssl"), "openssl not installed")
class CertificateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="spork-cert-")
        cls.pem = os.path.join(cls.dir, "cert.pem")
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1825",
            "-keyout", os.path.join(cls.dir, "key.pem"), "-out", cls.pem,
            "-subj", "/CN=myserver.local",
            "-addext", "subjectAltName=DNS:myserver.local,DNS:myserver,IP:192.168.1.20",
            "-addext", "basicConstraints=critical,CA:FALSE",
        ], check=True, capture_output=True)

    def test_parse(self):
        with open(self.pem, "rb") as fh:
            info = certs.load_certificates(fh.read())[0]
        self.assertEqual(info.subject_cn, "myserver.local")
        self.assertEqual(info.dns_names, ["myserver.local", "myserver"])
        self.assertEqual(info.ip_addresses, ["192.168.1.20"])
        self.assertFalse(info.is_ca)
        self.assertTrue(1820 <= info.days_left() <= 1825)
        self.assertTrue(info.covers("MyServer.local"))
        self.assertFalse(info.covers("other.local"))
        fp = subprocess.run(["openssl", "x509", "-noout", "-fingerprint", "-sha256", "-in", self.pem],
                            capture_output=True, text=True).stdout.strip().split("=", 1)[1]
        self.assertEqual(info.sha256, fp)
        end = subprocess.run(["openssl", "x509", "-noout", "-enddate", "-in", self.pem],
                             capture_output=True, text=True).stdout.strip().split("=", 1)[1]
        self.assertEqual(info.not_after,
                         dt.datetime.strptime(end, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=dt.timezone.utc))

    def test_key_file_rejected(self):
        with open(os.path.join(self.dir, "key.pem"), "rb") as fh:
            with self.assertRaises(certs.CertificateError):
                certs.load_certificates(fh.read())

    def test_suggestions(self):
        with open(self.pem, "rb") as fh:
            info = certs.load_certificates(fh.read())[0]
        sugg = certs.suggest_server_urls(info, {
            "dotfiles: nas": "https://myserver.local:3000/blair/dotfiles.git",
            "notes: nas": "https://myserver.local:3000/blair/notes.git",
            "notes: github": "https://github.com/blair/notes.git",
            "x: ssh": "ssh://git@myserver.local:222/blair/x.git",
        })
        self.assertEqual([s.url for s in sugg], ["https://myserver.local:3000/"])
        self.assertEqual(len(sugg[0].remotes), 2)
        bare = certs.suggest_server_urls(info, {})
        self.assertIn("https://myserver/", [s.url for s in bare])

    def test_server_for_url(self):
        servers = [("https://myserver.local:3000/", "/c.pem")]
        self.assertEqual(certs.server_for_url("https://myserver.local:3000/a/b.git", servers)[1], "/c.pem")
        self.assertIsNone(certs.server_for_url("https://myserver.local/a/b.git", servers))


class SettingsTests(unittest.TestCase):
    def test_roundtrip_preserves_unknown_keys(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "settings.json")
        with open(p, "w") as fh:
            fh.write('{"version": 1, "bookmarks": [{"path": "/x"}], "macOnly": {"a": 1}}')
        s = Settings.load(p)
        self.assertEqual(s.bookmarks[0].display_name, "x")
        cert = os.path.join(d, "c.pem")
        with open(cert, "w") as fh:
            fh.write("dummy")
        s.add_server("myserver.local:3000", cert, certs_dir=os.path.join(d, "certs"))
        s.save()
        again = Settings.load(p)
        self.assertEqual(again.servers[0].url, "https://myserver.local:3000/")
        self.assertTrue(again.servers[0].certificate.endswith("myserver.local-3000.pem"))
        self.assertEqual(again._extra, {"macOnly": {"a": 1}})
        self.assertEqual(oct(os.stat(p).st_mode & 0o777), "0o644")


if __name__ == "__main__":
    unittest.main()
