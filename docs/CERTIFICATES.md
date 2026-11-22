# Self-signed certificates for local git servers

The motivating setup is Forgejo in Podman as described in
[linux-install-notes: forgejo-podman.md](https://github.com/blairwang-online/linux-install-notes/blob/main/tasks/forgejo-podman.md):
Forgejo's own `forgejo cert --host myserver.local,myserver` writes a self-signed
`cert.pem`, and HTTPS is served on port 3000.

## What Spork does

1. You add the server's `cert.pem` (Preferences/Settings › Servers). Spork refuses a
   `key.pem`.
2. Spork reads the certificate itself (a small DER reader, no crypto library): the names it
   covers, its expiry date and its SHA-256 fingerprint.
3. Spork suggests the URL prefixes to trust by matching the certificate's names against the
   HTTPS remotes of your bookmarked repositories, for example
   `https://myserver.local:3000/` used by `dotfiles: nas` and `notes: nas`. You can tick
   several (a server reachable as `myserver.local` and `myserver` needs both) or type one.
4. It copies the file into its own data folder and, from then on, passes
   `-c http.<url>.sslCAInfo=<copy>` on **every** git command it runs.

That is the whole mechanism. Spork does not change `~/.gitconfig`, the macOS keychain or
the Linux trust store, so git in a terminal is unaffected. To make terminal git trust the
server too, use the guide's own command:

```
git config --global http.https://myserver.local:3000/.sslCAInfo ~/certs/myserver.pem
```

## Things to know

- **Include the port.** git matches `http.<url>` by scheme, host and port exactly, then by
  path prefix. `https://myserver.local/` does not cover `https://myserver.local:3000/`.
- **Use a name the certificate covers.** A remote using the server's IP address fails
  unless the certificate lists that IP.
- `sslCAInfo` *replaces* the CA bundle for that URL; it doesn't add to it. That's what we
  want for a self-signed server.
- **Test** (beside each trusted server) runs `git ls-remote` against a bookmarked remote on
  that server, which exercises exactly the path fetch uses.
- **Expiry**: the Dashboard warns 30 days before a certificate expires. When you renew it
  on the server, add the new `cert.pem` again; it replaces the old copy.
- Server reachability checks are plain TCP connections, so they work before any
  certificate is trusted. Spork itself never makes TLS connections with the
  certificate; only git does, with its own TLS library, so the platform's TLS policies
  for its own networking APIs don't come into it.
- SSH remotes on the same server (Forgejo's port 222) don't involve certificates at all.
