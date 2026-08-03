# Install Humanize for Claude Code

## Prerequisites

- [codex](https://github.com/openai/codex) -- OpenAI Codex CLI (for review). Verify with `codex --version`.
- `jq` -- JSON processor. Verify with `jq --version`.
- `git` -- Git version control. Verify with `git --version`.

## Option 1: Git Marketplace (Recommended)

Start Claude Code and run:

```bash
# Add the marketplace
/plugin marketplace add git@github.com:PolyArch/humanize.git

# Install the plugin
/plugin install humanize@PolyArch
```

## Option 2: Local Development

If you have the plugin cloned locally:

```bash
claude --plugin-dir /path/to/humanize
```

### Option 2b: Persistent checkout install (edit = live)

`--plugin-dir` lasts one session. For a permanent one, replace the installed copy with a symlink to
the clone. A normal plugin install copies the repo to
`~/.claude/plugins/cache/PolyArch/humanize/<version>/`, and `${CLAUDE_PLUGIN_ROOT}` resolves to that
copy — so a clone that is ahead of it simply does not run.

```bash
CACHE=~/.claude/plugins/cache/PolyArch/humanize
VERSION=$(ls "$CACHE")                                # the installed version directory
mv "$CACHE/$VERSION" "$CACHE/$VERSION.bak-orig"       # restore point — do not delete it
ln -s /path/to/humanize "$CACHE/$VERSION"
```

Verify: `readlink -f "$CACHE/$VERSION"` should print your clone, and
`ls "$CACHE/$VERSION/hooks/hooks.json"` should succeed.

Trade-offs:

- Everything the clone is ahead by goes live at once — check `git log` against the installed version first.
- `/plugin update humanize@PolyArch` **reinstalls** the plugin: it removes the path and writes a fresh
  downloaded directory, which discards the symlink and returns you to the published version. Upgrade
  with `git pull` in the clone instead; if you do want a plugin update, restore `<version>.bak-orig` first.
- `installed_plugins.json` keeps recording the old version string. It is only a label; the code that
  runs is whatever the symlink points at.

### Option 2c: Hooks without the plugin

For a project or machine that should get the hooks but not the whole plugin:

```bash
scripts/install-hooks.sh /path/to/project              # --dry-run to preview, --hooks a,b to select
scripts/install-hooks.sh /path/to/project --uninstall
```

It symlinks each hook into `<project>/.claude/hooks/`, registers it in `.claude/settings.json`, and
records what it created in `<project>/.humanize/installed-hooks.txt`. Never copies a hook (copies
drift), never overwrites a file it did not create, and warns if the plugin already registers the same
hooks for every project.

## Option 3: Try Experimental Features (dev branch)

The `dev` branch contains experimental features that are not yet released to `main`. To try them locally:

```bash
git clone https://github.com/PolyArch/humanize.git
cd humanize
git checkout dev
```

Then start Claude Code with the local plugin directory:

```bash
claude --plugin-dir /path/to/humanize
```

Note: The `dev` branch may contain unstable or incomplete features. For production use, stick with Option 1 (Git Marketplace) which tracks the stable `main` branch.

## Verify Installation

After installing, you should see Humanize commands available:

```
/humanize:start-rlcr-loop
/humanize:gen-plan
/humanize:refine-plan
/humanize:ask-codex
```

## Monitor Setup (Optional)

Add the monitoring helper to your shell for real-time progress tracking:

```bash
# Add to your .bashrc or .zshrc
source ~/.claude/plugins/cache/PolyArch/humanize/<LATEST.VERSION>/scripts/humanize.sh
```

Then use:

```bash
humanize monitor rlcr   # Monitor RLCR loop
```

## Other Install Guides

- [Install for Codex](install-for-codex.md)
- [Install for Kimi](install-for-kimi.md)

## Next Steps

See the [Usage Guide](usage.md) for detailed command reference and configuration options.
