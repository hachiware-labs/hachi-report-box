---
name: hachi-report-box
description: Register multiple report sources, fetch generated report or document artifacts from those sources, normalize and index them into a hachi-report-box Git repository, then commit and push to a configured GitHub repository for personal static-site viewing on Vercel or similar hosts. Use when the user says hachi-report-box, report box, collect generated documents, sync report sources, register report sources, archive reports, organize skill outputs, commit/push reports, GitHubへpush, Vercelで見る, 文書を集める, or レポートを整理.
---

# Hachi Report Box

Use this skill to register report-producing locations, sync generated artifacts
from them into a central `hachi-report-box` repository, keep stable indexes, and
commit/push the update to a configured GitHub repository.

## Workflow

1. Locate the report box repository in this order:
   - A path explicitly provided by the user.
   - `HACHI_REPORT_BOX_DIR`, then `HACHI_REPORT_BOX_REPO`.
   - `~/workspace/hachi-report-box`.
   - Ask the user only if none of these exists.
2. Inspect `git status --short` in the report box repository before writing.
   Work with existing changes, and avoid overwriting unrelated user edits.
3. Configure the GitHub target once when needed:

   ```bash
   python scripts/hachi_report_box.py target set \
     --box-dir /path/to/hachi-report-box \
     --remote git@github.com:owner/report-box.git \
     --branch main
   ```

4. Register each report source once. Use `--pattern` for directories that
   contain mixed files:

   ```bash
   python scripts/hachi_report_box.py source add daily-research \
     --box-dir /path/to/hachi-report-box \
     --path /path/to/project/reports \
     --project my-project \
     --title "Daily research reports" \
     --pattern "*.md"
   ```

5. Sync all registered sources and push them to the configured GitHub target:

   ```bash
   python scripts/hachi_report_box.py sync \
     --box-dir /path/to/hachi-report-box \
     --commit \
     --push
   ```

6. For one-off files, use `collect` directly. Add `--remote` and `--branch`
   when the target is not already configured.
7. Report created entry paths, index paths, commit hash, push target, and any
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

The helper writes entries under:

```text
reports/<project-slug>/<yyyy>/<yyyy-mm-dd>/<entry-slug>/
```

Each entry contains:

- `files/`: copied source artifacts.
- `manifest.json`: metadata, source paths, copied files, sizes, and SHA-256
  hashes.

The helper regenerates:

- `reports/INDEX.md`: human-readable latest-first index.
- `reports/index.json`: static-site-friendly index data.

## Commit Discipline

When committing manually, stage only new entries and regenerated indexes. Do not
stage unrelated files from the report box repository or local registration
files. Use concise messages such as:

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
