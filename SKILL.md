---
name: hachi-report-box
description: Register report sources with category names, file source paths, and managed-folder destinations, inspecting each project's report layout on first registration and remembering its file patterns, fetch all registered report or document artifacts, place them under the configured hachi-report-box managed folder, then commit and push to a configured GitHub repository for personal static-site viewing on Vercel or similar hosts. Use when the user says hachi-report-box, report box, collect generated documents, sync report sources, register report sources, inspect report sources, delete registered sources, archive reports, organize skill outputs, commit/push reports, GitHubへpush, Vercelで見る, 文書を集める, or レポートを整理.
---

# Hachi Report Box

Use this skill to register report-producing locations with a category, a file
source, and a destination inside the skill-managed folder. Sync all registered
sources into the central `hachi-report-box` repository, then commit/push only
after every registered source has been fetched successfully.

Uploading reports means committing and pushing the report box repository
itself with plain `git commit` and `git push`. There is no separate upload
API or hosting-side deploy step: once the commit reaches GitHub, the static
site host (Vercel or similar) picks it up from the repository.

## Workflow

1. Locate the report box repository in this order:
   - A path explicitly provided by the user.
   - `HACHI_REPORT_BOX_DIR`, then `HACHI_REPORT_BOX_REPO`.
   - `~/workspace/hachi-report-box`.
   - Ask the user only if none of these exists.
2. Inspect `git status --short` in the report box repository before writing.
   Work with existing changes, and avoid overwriting unrelated user edits.
   Run the helper as `hachi-report-box` when installed from npm. If using the
   bundled file directly, run `node scripts/hachi_report_box.js`.
3. Configure the GitHub target once when needed:

   ```bash
   hachi-report-box target set \
     --box-dir /path/to/hachi-report-box \
     --remote git@github.com:owner/report-box.git \
     --branch main \
     --managed-root reports
   ```

4. When the user first points at a report source, learn what that project's
   reports look like before registering. Every project produces different
   artifacts (Markdown dailies, HTML pages, dated folders, mixed assets), so
   never assume the layout:

   1. Inspect the folder to see extensions, naming conventions, and the most
      recent files:

      ```bash
      hachi-report-box source inspect --from /path/to/project/reports
      ```

   2. Open one or two of the `recent_files` and confirm they are the actual
      report artifacts, not templates, assets, watchlists, or configuration.
   3. Propose the category, `--pattern` selection (start from
      `suggested_patterns`), and `--to` destination to the user, and confirm
      which file kinds should be synced (e.g. Markdown only, or Markdown and
      HTML).
   4. Register the source, and record what you learned with `--note` so the
      convention is remembered for future syncs:

      ```bash
      hachi-report-box source add research \
        --box-dir /path/to/hachi-report-box \
        --from /path/to/project/reports \
        --to research/daily \
        --project my-project \
        --title "Daily research reports" \
        --pattern "*.md" \
        --note "Daily reports named YYYY-MM-DD.md in the folder root; HTML copies exist but are excluded"
      ```

   On later syncs, trust the stored patterns and notes from `source list`
   instead of re-inspecting, unless a sync result looks wrong or the user says
   the project's report format changed — then re-inspect and re-register with
   `--replace`.

5. Sync all registered sources and push them to the configured GitHub target:

   ```bash
   hachi-report-box sync \
     --box-dir /path/to/hachi-report-box \
     --commit \
     --push
   ```

6. Remove a registered source when needed:

   ```bash
   hachi-report-box source remove research --box-dir /path/to/hachi-report-box
   ```

   Add `--delete-files --commit --push` only when the synced files for that
   category should also be removed from GitHub.
7. For one-off files, use `collect` directly. Add `--remote` and `--branch`
   when the target is not already configured.
8. Report synced category paths, index paths, commit hash, push target, and any
   skipped sources.

## Configuration

The helper stores local registration data in:

```text
.hachi-report-box.local.json
```

Keep this file local because it can contain absolute paths to private projects.
Commit report entries and indexes, not local source registration, unless the
user explicitly wants a shareable configuration.

## Storage Convention

The helper writes synced source files under:

```text
<managed-root>/<registered-to>/
```

Each synced category contains:

- copied source artifacts, preserving relative paths for pattern matches.
- `_manifest.json`: category, source path, destination, copied files, sizes,
  and SHA-256 hashes.

The helper regenerates:

- `reports/INDEX.md`: human-readable latest-first index.
- `reports/index.json`: static-site-friendly index data.

## Commit Discipline

When committing manually, stage only managed destination paths and regenerated
indexes. Do not stage unrelated files from the report box repository or local
registration files. Use concise messages such as:

```text
Sync reports: <yyyy-mm-dd>
```

If the report box repository has unrelated dirty files, leave them untouched
and mention them in the final response.

## Safety

Treat collected documents as personal but potentially publishable. Before
commit or push, quickly scan file names and obvious text content for secrets,
credentials, private keys, tokens, or unintended personal data. If sensitive
content is likely, stop and ask the user how to proceed.
