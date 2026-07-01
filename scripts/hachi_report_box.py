#!/usr/bin/env python3
"""Collect report artifacts into a hachi-report-box repository."""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable


SKIP_DIRS = {".git", ".hg", ".svn", "__pycache__", ".pytest_cache"}


@dataclasses.dataclass
class CopiedFile:
    path: str
    size: int
    sha256: str


def slugify(value: str, fallback: str = "report") -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9._-]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-._")
    return value[:80] or fallback


def local_now() -> dt.datetime:
    return dt.datetime.now().astimezone()


def parse_date(value: str | None) -> dt.date:
    if not value:
        return local_now().date()
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise SystemExit(f"--date must be YYYY-MM-DD: {value}") from exc


def resolve_box_dir(raw: str | None) -> Path:
    candidate = (
        raw
        or os.environ.get("HACHI_REPORT_BOX_DIR")
        or os.environ.get("HACHI_REPORT_BOX_REPO")
    )
    if candidate:
        return Path(candidate).expanduser().resolve()

    default = Path.home() / "workspace" / "hachi-report-box"
    if default.exists():
        return default.resolve()

    raise SystemExit(
        "Report box repository not found. Pass --box-dir or set "
        "HACHI_REPORT_BOX_DIR."
    )


def ensure_git_repo(path: Path) -> None:
    if not path.exists():
        raise SystemExit(f"Report box directory does not exist: {path}")
    result = run_git(path, ["rev-parse", "--is-inside-work-tree"], check=False)
    if result.returncode != 0 or result.stdout.strip() != "true":
        raise SystemExit(f"Report box directory is not a Git worktree: {path}")


def unique_path(path: Path, overwrite: bool) -> Path:
    if overwrite or not path.exists():
        return path
    base = path
    for index in range(2, 1000):
        candidate = base.with_name(f"{base.name}-{index}")
        if not candidate.exists():
            return candidate
    raise SystemExit(f"Could not find an unused destination for {path}")


def ignore_names(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name in SKIP_DIRS}


def copy_source(source: Path, files_dir: Path) -> Path:
    if not source.exists():
        raise SystemExit(f"Source does not exist: {source}")

    destination = unique_path(files_dir / source.name, overwrite=False)
    if source.is_dir():
        shutil.copytree(source, destination, ignore=ignore_names)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return destination


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iter_files(path: Path) -> Iterable[Path]:
    if path.is_file():
        yield path
        return
    for child in sorted(path.rglob("*")):
        if child.is_file():
            yield child


def collect_file_metadata(entry_dir: Path) -> list[CopiedFile]:
    files: list[CopiedFile] = []
    files_dir = entry_dir / "files"
    if not files_dir.exists():
        return files
    for path in iter_files(files_dir):
        relative = path.relative_to(entry_dir).as_posix()
        files.append(
            CopiedFile(
                path=relative,
                size=path.stat().st_size,
                sha256=sha256_file(path),
            )
        )
    return files


def rel_to_box(box_dir: Path, path: Path) -> str:
    return path.relative_to(box_dir).as_posix()


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def read_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def md_escape(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def manifest_summary(box_dir: Path, manifest_path: Path) -> dict:
    manifest = read_manifest(manifest_path)
    entry_dir = manifest_path.parent
    files = manifest.get("files", [])
    return {
        "title": manifest.get("title", entry_dir.name),
        "project": manifest.get("project", ""),
        "kind": manifest.get("kind", ""),
        "date": manifest.get("date", ""),
        "created_at": manifest.get("created_at", ""),
        "entry_path": rel_to_box(box_dir, entry_dir),
        "manifest_path": rel_to_box(box_dir, manifest_path),
        "file_count": len(files),
        "notes": manifest.get("notes", []),
    }


def render_indexes(box_dir: Path, reports_dir: Path) -> tuple[Path, Path]:
    manifests = sorted(reports_dir.rglob("manifest.json"))
    summaries = [manifest_summary(box_dir, path) for path in manifests]
    summaries.sort(key=lambda item: (item["date"], item["created_at"]), reverse=True)

    index_json = reports_dir / "index.json"
    write_json(index_json, {"reports": summaries})

    lines = [
        "# Hachi Report Box Index",
        "",
        "| Date | Project | Kind | Title | Files | Path |",
        "| --- | --- | --- | --- | ---: | --- |",
    ]
    for item in summaries:
        lines.append(
            "| {date} | {project} | {kind} | {title} | {file_count} | {path} |".format(
                date=md_escape(item["date"]),
                project=md_escape(item["project"]),
                kind=md_escape(item["kind"]),
                title=md_escape(item["title"]),
                file_count=item["file_count"],
                path=md_escape(item["entry_path"]),
            )
        )
    lines.append("")

    index_md = reports_dir / "INDEX.md"
    index_md.write_text("\n".join(lines), encoding="utf-8")
    return index_md, index_json


def run_git(
    cwd: Path,
    args: list[str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        raise SystemExit(f"git {' '.join(args)} failed: {message}")
    return result


def git_status_short(cwd: Path) -> str:
    return run_git(cwd, ["status", "--short"]).stdout


def git_stage_commit_push(
    box_dir: Path,
    paths: list[Path],
    message: str,
    push: bool,
) -> dict:
    pathspecs = [rel_to_box(box_dir, path) for path in paths]
    run_git(box_dir, ["add", "--", *pathspecs])

    diff = run_git(box_dir, ["diff", "--cached", "--quiet"], check=False)
    if diff.returncode == 0:
        return {"committed": False, "commit": None, "pushed": False}

    run_git(box_dir, ["commit", "-m", message])
    commit = run_git(box_dir, ["rev-parse", "--short", "HEAD"]).stdout.strip()
    pushed = False
    if push:
        run_git(box_dir, ["push"])
        pushed = True
    return {"committed": True, "commit": commit, "pushed": pushed}


def collect(args: argparse.Namespace) -> int:
    box_dir = resolve_box_dir(args.box_dir)
    ensure_git_repo(box_dir)

    date = parse_date(args.date)
    project = args.project or Path.cwd().name
    project_slug = slugify(project, fallback="project")
    title = args.title or f"{args.kind} {local_now().strftime('%H%M%S')}"
    entry_slug = slugify(args.slug or title)

    reports_dir = box_dir / args.dest_root
    entry_parent = reports_dir / project_slug / str(date.year) / date.isoformat()
    entry_dir = unique_path(entry_parent / entry_slug, overwrite=args.overwrite)
    files_dir = entry_dir / "files"

    sources = [Path(source).expanduser().resolve() for source in args.sources]
    if args.dry_run:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "box_dir": str(box_dir),
                    "entry_dir": str(entry_dir),
                    "sources": [str(source) for source in sources],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    status_before = git_status_short(box_dir)
    copied_roots: list[dict] = []
    files_dir.mkdir(parents=True, exist_ok=True)
    for source in sources:
        copied_to = copy_source(source, files_dir)
        copied_roots.append(
            {
                "source": str(source),
                "copied_to": rel_to_box(box_dir, copied_to),
            }
        )

    copied_files = collect_file_metadata(entry_dir)
    created_at = local_now().isoformat(timespec="seconds")
    manifest = {
        "title": title,
        "project": project,
        "project_slug": project_slug,
        "kind": args.kind,
        "date": date.isoformat(),
        "created_at": created_at,
        "entry_path": rel_to_box(box_dir, entry_dir),
        "sources": copied_roots,
        "notes": args.note or [],
        "files": [dataclasses.asdict(file) for file in copied_files],
    }
    manifest_path = entry_dir / "manifest.json"
    write_json(manifest_path, manifest)
    index_md, index_json = render_indexes(box_dir, reports_dir)

    git_result = {"committed": False, "commit": None, "pushed": False}
    if args.commit or args.push:
        git_result = git_stage_commit_push(
            box_dir,
            [entry_dir, index_md, index_json],
            args.message or f"Add report: {title}",
            push=args.push,
        )

    result = {
        "entry_dir": str(entry_dir),
        "manifest": str(manifest_path),
        "index_md": str(index_md),
        "index_json": str(index_json),
        "file_count": len(copied_files),
        "git": git_result,
        "status_before": status_before.splitlines(),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect report artifacts into hachi-report-box."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect_parser = subparsers.add_parser("collect")
    collect_parser.add_argument("sources", nargs="+")
    collect_parser.add_argument("--box-dir")
    collect_parser.add_argument("--project")
    collect_parser.add_argument("--title")
    collect_parser.add_argument("--slug")
    collect_parser.add_argument("--kind", default="report")
    collect_parser.add_argument("--date")
    collect_parser.add_argument("--dest-root", default="reports")
    collect_parser.add_argument("--note", action="append")
    collect_parser.add_argument("--message")
    collect_parser.add_argument("--overwrite", action="store_true")
    collect_parser.add_argument("--dry-run", action="store_true")
    collect_parser.add_argument("--commit", action="store_true")
    collect_parser.add_argument("--push", action="store_true")
    collect_parser.set_defaults(func=collect)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.push:
        args.commit = True
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

