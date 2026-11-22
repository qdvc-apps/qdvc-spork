# Spork design

Both editions follow this document. Platform idioms win where they differ (for example,
the macOS edition puts search in the window toolbar; the GTK edition uses a search bar).

## Window

```
┌──────────────┬──────────────────────┬───────────────────────────────────┐
│ Repositories │ Commits              │ [ Commit | Changes | Browse ]     │
│              │ remotes: origin ✓    │                                   │
│ Dashboard    │  nas ↑2  backup ↓1   │                                   │
│ ● dotfiles 2 │ ┄ Uncommitted (3)    │                                   │
│ ● notes      │ ● Fix parser    main │                                   │
│ ● spork  ↑1  │ ● Add lexer          │                                   │
└──────────────┴──────────────────────┴───────────────────────────────────┘
```

- **Sidebar**: *Dashboard*, then bookmarks in the user's order. Each row has a status dot
  (green ok, orange attention, grey unknown, red missing), the current branch and a badge:
  `↑n` (most commits ahead of any remote), `↓n` (most behind), `●n` (changed files), `?`
  (a remote couldn't be checked), `!` (folder missing). Context menu: Fetch, Show in
  Finder/Files, Terminal, Copy Path, Move Up/Down, Remove.
- **Commit list**: topo-ordered history of HEAD (optionally all branches), 300 at a time,
  loading more on scroll. Ref pills: current branch (filled accent), branches, remote
  branches, tags. The graph is hidden while searching. *Uncommitted Changes* is the first
  row whenever the working copy is dirty.
- **Remote strip**: one chip per remote comparing the current branch with
  `<remote>/<branch>` from the last fetch: ✓ in sync, ↑ahead, ↓behind, "no main", or
  offline. Click for Fetch / Pull / Push / Copy URL.
- **Detail tabs**: Commit, Changes, Browse. Selecting another commit keeps the tab.
  - Uncommitted Changes + Commit tab = the composer. Commit is enabled when a summary is
    present and something is staged (or Amend is on); ⌘↩ / Ctrl+Enter commits. Summaries
    over 72 characters are flagged.
  - Changes for the working copy: *Conflicts*, *Staged* and *Changes* sections with
    checkboxes and Stage All / Unstage All. Hunk buttons: Stage Hunk, Discard Hunk
    (confirmed) in the unstaged view; Unstage Hunk in the staged view. Untracked files are
    staged whole. Discarding an untracked file moves it to the Trash (macOS) or deletes it
    (GTK).
  - Changes for a commit compare against the first parent (merges say so).
  - Browse for the working copy shows HEAD.

## Remotes

- **Fetch** (toolbar) = census-style fetch of every remote of this repository: probe each
  server once (TCP connect; local paths must exist), fetch only reachable ones, skip later
  fetches to a host after a network failure. Its menu fetches one remote.
- **Pull** uses the upstream's remote (or the first remote) with `--ff-only`. If the
  branches have diverged, Spork asks: Merge or Rebase.
- **Push** pushes the current branch to the upstream's remote; on a branch with no
  upstream it sets one. The menu offers each remote and **Push to All Remotes**.
- Fetch never prunes, runs with `maintenance.auto=false` and `gc.auto=0`, and does not
  recurse into submodules.

## Dashboard

Tiles: Repositories, Clean & in sync, Need attention, Servers offline. Then a card per
repository, worst first: level icon, name, branch pill, path, a row per remote, the
census reasons, and the last commit. Then the servers checked by the last Fetch All, with a
lock where a trusted certificate applies. Banners for offline servers and certificates that
are missing, expired or expiring within 30 days.

Census reasons (any of these makes a repository "needs attention"): operation in progress
(merge, rebase, cherry-pick, revert, am, bisect), detached HEAD, conflicted / staged /
modified / untracked files, stashes, commits on no remote, `<remote> has no <branch>`,
`n to push to <remote>`, `n to pull from <remote>`, no upstream branch. "Unknown" means
nothing was flagged but a remote was offline or failed to fetch.

## Passwords

Git and ssh call Spork's askpass helper (`GIT_ASKPASS`, `SSH_ASKPASS` with
`SSH_ASKPASS_REQUIRE=force`). Prompts containing "(yes/no" get Yes/No buttons, prompts
containing "Username" a plain field, everything else a password field. Cancel exits 1.
`GIT_TERMINAL_PROMPT=0` makes git fail rather than wait on a terminal nobody can see.

## Keyboard

| Action | macOS | GTK |
|---|---|---|
| Add repository | ⌘O | Ctrl+O |
| Dashboard / repository n | ⌘0 / ⌘1…⌘9 | Ctrl+0 / Ctrl+1…9 |
| Commit / Changes / Browse tab | ⌥⌘1/2/3 | Alt+1/2/3 |
| Fetch all remotes | ⇧⌘T | Ctrl+Shift+T |
| Fetch all repositories | ⌥⌘T | Ctrl+Alt+T |
| Pull | ⇧⌘P | Ctrl+Shift+P |
| Push | ⌘P | Ctrl+P |
| New branch | ⇧⌘N | Ctrl+Shift+N |
| Refresh | ⌘R | Ctrl+R, F5 |
| Search commits | toolbar field | Ctrl+F |
| Commit | ⌘↩ | Ctrl+Enter |
| Preferences | ⌘, | Ctrl+, |
