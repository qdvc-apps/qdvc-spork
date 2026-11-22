# Maintaining the GTK edition

## Structure

- `sporkapp/core` has no GTK imports and is covered by `tests/` against real temporary
  repositories (`python3 -m unittest discover -s tests`). Put logic there. It mirrors the
  macOS edition's `SporkCore`; keep behaviour identical and update `docs/DESIGN.md`.
- `sporkapp/ui/repo_view.py` owns one `Repository` and every git action; the pages
  (`changes.py`, `browse.py`, `commit_info.py`) call back into it.
- Blocking work runs through `util.run_async`, whose idle callbacks return `False`
  (an idle callback that returns a truthy value runs forever).
- Results carry a token per kind of load (`RepoView._bg`), so a slow answer for a commit
  you've already left is dropped.
- Rows that show user text (`Adw.ActionRow`, banners, toasts) disable or escape Pango
  markup: commit subjects and `Name <email>` would otherwise be parsed as markup.
- Colours in `style.css` use libadwaita's named colours (`@success_color` and friends),
  which work from libadwaita 1.5 (GNOME 46) up; CSS `var()` needs GTK 4.16.

## Flatpak

- The manifest is YAML (`flatpak/qdvc.spork.gtk4app.yml`) so a `*.json` ignore rule can't
  hide it.
- **git** is built from the `v2.54.1` tag. From 2.55, git builds its Rust code by default;
  moving to 2.55+ means adding the `org.freedesktop.Sdk.Extension.rust-stable` SDK
  extension (and `append-path: /usr/lib/sdk/rust-stable/bin` in the module's build
  options) or the Makefile switch that turns Rust off, whichever upstream documents for
  that release.
- **openssh** is built from the `V_10_6_P1` tag (client only). Release tags carry a
  generated `configure`; the build runs `autoreconf` if one is missing.
- Git sources are pinned by tag only. Add `commit:` hashes for reproducible builds.
- Inside the sandbox `XDG_CONFIG_HOME` is the app's private folder, so the runner sets
  `XDG_CONFIG_HOME` (to `HOST_XDG_CONFIG_HOME` or `~/.config`) for git processes only.
- The `.desktop` file is named after the app ID, which GNOME on Wayland uses to match
  windows to launchers; `GLib.set_prgname` runs before any window exists.

## Not yet verified

This edition was written in a sandbox without GTK 4, libadwaita or flatpak-builder. The
core tests pass there; the UI and the Flatpak build need a first run on a real desktop.
`tests/test_ui_smoke.py` builds the real window around a temporary repository when GTK is
available.
