# Settings file

One JSON file per user. Both editions read and write the same format; keys an edition
doesn't know are kept when it saves.

| Edition | Location |
|---|---|
| macOS | `~/Library/Application Support/QDVC Spork/settings.json` |
| GTK, from a checkout | `~/.config/qdvc-spork/settings.json` |
| GTK, Flatpak | `~/.var/app/qdvc.spork.gtk4app/config/qdvc-spork/settings.json` |

Copied certificates live beside it in `certs/` (macOS) or in the data folder
(`~/.local/share/qdvc-spork/certs/`, or `~/.var/app/qdvc.spork.gtk4app/data/qdvc-spork/certs/`
in the Flatpak).

```json
{
  "version": 1,
  "bookmarks": [
    { "path": "/home/blair/src/dotfiles" },
    { "path": "/home/blair/src/notes", "name": "Notes" }
  ],
  "servers": [
    {
      "url": "https://myserver.local:3000/",
      "certificate": "/home/blair/.local/share/qdvc-spork/certs/myserver.local-3000.pem",
      "label": "Forgejo on the NAS"
    }
  ],
  "editor": "",
  "terminal": "",
  "fetchTimeout": 180,
  "showAllBranches": false
}
```

- `bookmarks[].path`: absolute, symlinks resolved, the repository's top level. `name` is
  optional.
- `servers[].url`: normalised `scheme://host[:port]/path/` (lower-case host, trailing
  slash). git matches `http.<url>.*` exactly on scheme, host and port, so the port matters.
- `servers[].certificate`: absolute path of Spork's own copy of the PEM file.
- `editor`, `terminal`: macOS, an app path or bundle identifier; GTK, a command (`{dir}` is
  replaced by the repository folder for terminals). Empty means the default.
- `fetchTimeout`: seconds before a fetch, pull or push is abandoned.

A file that can't be parsed is copied to `settings.json.broken` and Spork starts fresh.
