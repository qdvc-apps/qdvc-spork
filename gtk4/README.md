# QDVC Spork for GNOME

The GTK 4 / libadwaita edition of [QDVC Spork](../README.md), packaged as a local
Flatpak with the ID `qdvc.spork.gtk4app` (not published on Flathub).

## Build and install

One-time setup:

```
sudo apt install flatpak flatpak-builder      # Debian / Ubuntu
sudo dnf install flatpak flatpak-builder      # Fedora / AlmaLinux
flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak install --user flathub org.gnome.Platform//50 org.gnome.Sdk//50
```

Then, from the `gtk4/` folder:

```
scripts/build-app.sh --install    # check, test, build the Flatpak and install it for your user
scripts/build-app.sh --run        # build and start it without installing
scripts/build-app.sh --bundle     # also write build/qdvc-spork.flatpak
scripts/build-app.sh --uninstall  # remove it (settings and repositories are untouched)
scripts/build-app.sh --no-tests   # skip the unit tests
```

`--install` also writes `~/.local/bin/spork`, so `spork .` opens the repository you're in.
The first build compiles git and an ssh client into the Flatpak and takes a few minutes.
Re-running `--install` asks a running copy to quit and starts the new one.

## The sandbox

| Permission | Why |
|---|---|
| `--share=network` | fetch, pull and push |
| `--filesystem=home` | your repositories, `~/.gitconfig`, `~/.config/git`, `~/.ssh`, `~/.local/bin/spork` |
| `--socket=ssh-auth` | your desktop's ssh-agent |
| `--talk-name=org.freedesktop.secrets` | `git-credential-libsecret` saves HTTPS passwords in the keyring |
| Wayland/X11, GPU | the window |

Repositories outside your home folder aren't visible to the Flatpak. Git inside the
sandbox is Spork's own build (2.54), configured with `credential.helper=libsecret` at the
system level; your `~/.gitconfig` (and `~/.config/git/config`, which Spork points git at
explicitly) still applies. Commit signing with gpg won't work inside the sandbox. Check
permissions with Flatseal or `flatpak info --show-permissions qdvc.spork.gtk4app`.

Settings live in `~/.var/app/qdvc.spork.gtk4app/` inside the Flatpak, not `~/.config`.

## Running from the checkout

Needs Python 3, PyGObject, GTK 4.14+ and libadwaita 1.5+ (GNOME 46, Ubuntu 24.04,
Fedora 40). GtkSourceView 5 adds syntax highlighting in Browse if installed.

```
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-gtksource-5
python3 qdvc_spork.py [path]                 # run from the checkout
python3 -m unittest discover -s tests        # tests (add xvfb-run -a for the window test)
```

## Layout

```
qdvc_spork.py         entry point (also the --askpass dialog)
sporkapp/core/        git runner, parsers, graph, census, certificates, settings (no GTK)
sporkapp/ui/          window, panes, dashboard, preferences, style.css
data/                 .desktop, metainfo, icons (from ../tools/make_icons.py)
flatpak/              qdvc.spork.gtk4app.yml and launchers
scripts/build-app.sh
tests/
docs/MAINTENANCE.md
```
