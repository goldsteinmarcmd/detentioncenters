# US Immigration Detention Facilities Map

An interactive, facility-level map combining the Deportation Data Project's facility
registry and detention stays with ICE's detention-management statistics. Every public
population and demographic section identifies its source and date.

## Privacy and provenance

The repository publishes aggregate facility data only. Individual-level detention
stays, downloaded spreadsheets, and intermediate rollups remain under
`pipeline/data/raw/` and `pipeline/data/interim/`; both directories are ignored by Git.
Demographic cells below five are suppressed before any file reaches the frontend.

The tracked public data consists of:

- `web/public/data/facilities.geojson`
- `web/public/data/facilities-unplaced.json`
- `web/public/data/build-report.json`

## Local development

Python 3.12 and Node 22 are the supported runtimes.

```bash
scripts/bootstrap-worker.sh
.venv/bin/python -m pipeline.refresh --skip-fetch --force
npm --prefix web run dev
```

Use `python -m pipeline.fetch` to retrieve current upstream files. A complete refresh
downloads roughly 110 MB when the DDP stays source is not already cached.

## Publishing

GitHub Pages deploys `web/dist` through `.github/workflows/pages.yml` whenever `main`
changes. The Vite base path is derived from `GITHUB_REPOSITORY`, so a normal project
URL such as `https://OWNER.github.io/REPOSITORY/` works automatically.

Live site: <https://goldsteinmarcmd.github.io/detentioncenters/>

Repository: <https://github.com/goldsteinmarcmd/detentioncenters>

For a custom domain, create a repository Actions variable named `VITE_BASE_PATH` with
the value `/`.

## Daily worker

Use a dedicated clone so an automated refresh never encounters development edits.

```bash
git clone https://github.com/goldsteinmarcmd/detentioncenters.git /absolute/path/to/detentioncenters-worker
cd /absolute/path/to/detentioncenters-worker
scripts/bootstrap-worker.sh
.venv/bin/python ops/install_launch_agent.py "$PWD"
```

The LaunchAgent checks upstream sources daily at 3:17 AM local time and once when it is
loaded. It rebuilds only when source fingerprints change. Publication follows this
order:

1. Download each source to a temporary file and validate its format and schema.
2. Aggregate the individual-level stays locally with small-cell suppression.
3. Build the public files in a staging directory.
4. Run strict source-fidelity and privacy checks against the staged output.
5. Build the production frontend against the staged data.
6. Promote, commit, and push only the three aggregate public files.

If any step fails, no commit is made and the last successful Pages deployment remains
live. Logs are written to `~/Library/Logs/detentioncenters/`.

Manual worker commands:

```bash
# Run the same guarded publication immediately
scripts/refresh-and-publish.sh

# Inspect scheduler state
launchctl print gui/$(id -u)/io.github.goldsteinmarcmd.detentioncenters.refresh

# Trigger the installed job immediately
launchctl kickstart -k gui/$(id -u)/io.github.goldsteinmarcmd.detentioncenters.refresh
```

Source definitions, methodology, known limitations, and integrity rules are documented
in `CLAUDE.md`.
