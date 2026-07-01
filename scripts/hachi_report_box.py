#!/usr/bin/env python3
"""Collect registered report sources into a hachi-report-box repository."""

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


DEFAULT_CONFIG_FILE = ".hachi-report-box.local.json"
DEFAULT_DEST_ROOT = "reports"
SKIP_DIRS = {".git", ".hg", ".svn", "__pycache__", ".pytest_cache"}


@dataclasses.dataclass
class CopiedFile:
    path: str
    size: int
    sha256: str


@dataclasses.dataclass
class EntryResult:
    entry_dir: Path
    manifest_path: Path
    file_count: int


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


def resolve_config_path(box_dir: Path, raw: str | None) -> Path:
    candidate = raw or os.environ.get("HACHI_REPORT_BOX_CONFIG")
    if candidate:
        return Path(candidate).expanduser().resolve()
    return box_dir / DEFAULT_CONFIG_FILE


def ensure_git_repo(path: Path) -> None:
    if not path.exists():
        raise SystemExit(f"Report box directory does not exist: {path}")
    result = run_git(path, ["rev-parse", "--is-inside-work-tree"], check=False)
    if result.returncode != 0 or result.stdout.strip() != "true":
        raise SystemExit(f"Report box directory is not a Git worktree: {path}")


def load_config(config_path: Path) -> dict:
    if not config_path.exists():
        return {"version": 1, "target": {}, "sources": []}
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON config: {config_path}: {exc}") from exc

    if not isinstance(data, dict):
        raise SystemExit(f"Config must be a JSON object: {config_path}")
    data.setdefault("version", 1)
    data.setdefault("target", {})
    data.setdefault("sources", [])
    if not isinstance(data["sources"], list):
        raise SystemExit(f"Config sources must be a list: {config_path}")
    return data


def save_config(config_path: Path, config: dict) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(config_path, config)


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
        "source_name": manifest.get("source_name", ""),
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
        "| Date | Project | Kind | Title | Source | Files | Path |",
        "| --- | --- | --- | --- | --- | ---: | --- |",
    ]
    for item in summaries:
        lines.append(
            "| {date} | {project} | {kind} | {title} | {source} | {file_count} | {path} |".format(
                date=md_escape(item["date"]),
                project=md_escape(item["project"]),
                kind=md_escape(item["kind"]),
                title=md_escape(item["title"]),
                source=md_escape(item["source_name"]),
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


def current_branch(cwd: Path) -> str:
    branch = run_git(cwd, ["branch", "--show-current"], check=False).stdout.strip()
    return branch or "main"


def ensure_origin_remote(cwd: Path, remote: str) -> None:
    existing = run_git(cwd, ["remote", "get-url", "origin"], check=False)
    if existing.returncode == 0:
        run_git(cwd, ["remote", "set-url", "origin", remote])
    else:
        run_git(cwd, ["remote", "add", "origin", remote])


def git_stage_commit_push(
    box_dir: Path,
    paths: list[Path],
    message: str,
    push: bool,
    remote: str | None = None,
    branch: str | None = None,
) -> dict:
    pathspecs = [rel_to_box(box_dir, path) for path in paths]
    run_git(box_dir, ["add", "--", *pathspecs])

    diff = run_git(box_dir, ["diff", "--cached", "--quiet"], check=False)
    if diff.returncode == 0:
        return {"committed": False, "commit": None, "pushed": False}

    run_git(box_dir, ["commit", "-m", message])
    commit = run_git(box_dir, ["rev-parse", "--short", "HEAD"]).stdout.strip()
    pushed = False
    push_target = None
    if push:
        if remote:
            target_branch = branch or current_branch(box_dir)
            run_git(box_dir, ["push", remote, f"HEAD:{target_branch}"])
            push_target = f"{remote} HEAD:{target_branch}"
        elif branch:
            run_git(box_dir, ["push", "origin", f"HEAD:{branch}"])
            push_target = f"origin HEAD:{branch}"
        else:
            run_git(box_dir, ["push"])
            push_target = "default"
        pushed = True
    return {
        "committed": True,
        "commit": commit,
        "pushed": pushed,
        "push_target": push_target,
    }


def discover_source_paths(source: dict, skip_missing: bool = False) -> list[Path]:
    source_path = Path(str(source["path"])).expanduser().resolve()
    if not source_path.exists():
        if skip_missing:
            return []
        raise SystemExit(f"Registered source does not exist: {source_path}")

    patterns = source.get("patterns") or []
    if not patterns:
        return [source_path]

    if source_path.is_file():
        return [source_path]

    matches: dict[str, Path] = {}
    for pattern in patterns:
        for path in source_path.glob(pattern):
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            if path.exists():
                matches[str(path.resolve())] = path.resolve()
    return [matches[key] for key in sorted(matches)]


def create_entry(
    *,
    box_dir: Path,
    source_paths: list[Path],
    project: str,
    title: str,
    kind: str,
    date: dt.date,
    dest_root: str,
    notes: list[str],
    source_name: str | None,
    slug: str | None,
    overwrite: bool,
) -> EntryResult:
    project_slug = slugify(project, fallback="project")
    entry_slug = slugify(slug or title)
    reports_dir = box_dir / dest_root
    entry_parent = reports_dir / project_slug / str(date.year) / date.isoformat()
    entry_dir = unique_path(entry_parent / entry_slug, overwrite=overwrite)
    files_dir = entry_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    copied_roots: list[dict] = []
    for source in source_paths:
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
        "kind": kind,
        "date": date.isoformat(),
        "created_at": created_at,
        "entry_path": rel_to_box(box_dir, entry_dir),
        "source_name": source_name,
        "sources": copied_roots,
        "notes": notes,
        "files": [dataclasses.asdict(file) for file in copied_files],
    }
    manifest_path = entry_dir / "manifest.json"
    write_json(manifest_path, manifest)
    return EntryResult(
        entry_dir=entry_dir,
        manifest_path=manifest_path,
        file_count=len(copied_files),
    )


def selected_sources(config: dict, names: list[str] | None) -> list[dict]:
    sources = config.get("sources", [])
    if not names:
        return sources

    by_name = {source.get("name"): source for source in sources}
    selected = []
    for name in names:
        if name not in by_name:
            raise SystemExit(f"Unknown source: {name}")
        selected.append(by_name[name])
    return selected


def resolve_target(config: dict, args: argparse.Namespace) -> tuple[str | None, str | None]:
    target = config.get("target") or {}
    remote = args.remote if hasattr(args, "remote") else None
    branch = args.branch if hasattr(args, "branch") else None
    return remote or target.get("remote"), branch or target.get("branch")


def collect(args: argparse.Namespace) -> int:
    box_dir = resolve_box_dir(args.box_dir)
    ensure_git_repo(box_dir)
    config = load_config(resolve_config_path(box_dir, args.config))

    date = parse_date(args.date)
    project = args.project or Path.cwd().name
    title = args.title or f"{args.kind} {local_now().strftime('%H%M%S')}"
    source_paths = [Path(source).expanduser().resolve() for source in args.sources]
    if args.dry_run:
        entry_slug = slugify(args.slug or title)
        entry_dir = (
            box_dir
            / args.dest_root
            / slugify(project, fallback="project")
            / str(date.year)
            / date.isoformat()
            / entry_slug
        )
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "box_dir": str(box_dir),
                    "entry_dir": str(entry_dir),
                    "sources": [str(source) for source in source_paths],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    status_before = git_status_short(box_dir)
    entry = create_entry(
        box_dir=box_dir,
        source_paths=source_paths,
        project=project,
        title=title,
        kind=args.kind,
        date=date,
        dest_root=args.dest_root,
        notes=args.note or [],
        source_name=args.source_name,
        slug=args.slug,
        overwrite=args.overwrite,
    )
    index_md, index_json = render_indexes(box_dir, box_dir / args.dest_root)

    git_result = {"committed": False, "commit": None, "pushed": False}
    if args.commit or args.push:
        remote, branch = resolve_target(config, args)
        git_result = git_stage_commit_push(
            box_dir,
            [entry.entry_dir, index_md, index_json],
            args.message or f"Add report: {title}",
            push=args.push,
            remote=remote,
            branch=branch,
        )

    result = {
        "entry_dir": str(entry.entry_dir),
        "manifest": str(entry.manifest_path),
        "index_md": str(index_md),
        "index_json": str(index_json),
        "file_count": entry.file_count,
        "git": git_result,
        "status_before": status_before.splitlines(),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def source_add(args: argparse.Namespace) -> int:
    box_dir = resolve_box_dir(args.box_dir)
    ensure_git_repo(box_dir)
    config_path = resolve_config_path(box_dir, args.config)
    config = load_config(config_path)

    sources = config.setdefault("sources", [])
    existing_index = next(
        (index for index, source in enumerate(sources) if source.get("name") == args.name),
        None,
    )
    if existing_index is not None and not args.replace:
        raise SystemExit(f"Source already exists: {args.name}. Use --replace.")

    source_path = Path(args.path).expanduser().resolve()
    source = {
        "name": args.name,
        "type": "local",
        "path": str(source_path),
        "project": args.project or args.name,
        "title": args.title or args.name,
        "kind": args.kind,
        "patterns": args.pattern or [],
        "notes": args.note or [],
    }
    if args.dest_root:
        source["dest_root"] = args.dest_root

    if existing_index is None:
        sources.append(source)
    else:
        sources[existing_index] = source
    sources.sort(key=lambda item: item["name"])
    save_config(config_path, config)

    print(
        json.dumps(
            {"config": str(config_path), "source": source},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def source_list(args: argparse.Namespace) -> int:
    box_dir = resolve_box_dir(args.box_dir)
    ensure_git_repo(box_dir)
    config_path = resolve_config_path(box_dir, args.config)
    config = load_config(config_path)
    print(
        json.dumps(
            {
                "config": str(config_path),
                "sources": config.get("sources", []),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def source_remove(args: argparse.Namespace) -> int:
    box_dir = resolve_box_dir(args.box_dir)
    ensure_git_repo(box_dir)
    config_path = resolve_config_path(box_dir, args.config)
    config = load_config(config_path)
    before = len(config.get("sources", []))
    config["sources"] = [
        source for source in config.get("sources", []) if source.get("name") != args.name
    ]
    if len(config["sources"]) == before:
        raise SystemExit(f"Unknown source: {args.name}")
    save_config(config_path, config)
    print(
        json.dumps(
            {"config": str(config_path), "removed": args.name},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def target_set(args: argparse.Namespace) -> int:
    box_dir = resolve_box_dir(args.box_dir)
    ensure_git_repo(box_dir)
    config_path = resolve_config_path(box_dir, args.config)
    config = load_config(config_path)
    target = config.setdefault("target", {})

    if args.remote:
        target["remote"] = args.remote
    if args.branch:
        target["branch"] = args.branch
    if args.dest_root:
        target["dest_root"] = args.dest_root
    if args.set_origin:
        if not args.remote:
            raise SystemExit("--set-origin requires --remote")
        ensure_origin_remote(box_dir, args.remote)

    save_config(config_path, config)
    print(
        json.dumps(
            {"config": str(config_path), "target": target},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def target_show(args: argparse.Namespace) -> int:
    box_dir = resolve_box_dir(args.box_dir)
    ensure_git_repo(box_dir)
    config_path = resolve_config_path(box_dir, args.config)
    config = load_config(config_path)
    print(
        json.dumps(
            {
                "config": str(config_path),
                "target": config.get("target", {}),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def sync(args: argparse.Namespace) -> int:
    box_dir = resolve_box_dir(args.box_dir)
    ensure_git_repo(box_dir)
    config_path = resolve_config_path(box_dir, args.config)
    config = load_config(config_path)
    sources = selected_sources(config, args.source)
    if not sources:
        raise SystemExit("No report sources registered. Use source add first.")

    date = parse_date(args.date)
    status_before = git_status_short(box_dir)
    entries: list[EntryResult] = []
    skipped: list[dict] = []
    dest_roots: set[str] = set()

    if args.dry_run:
        preview = []
        for source in sources:
            paths = discover_source_paths(source, skip_missing=args.skip_missing)
            preview.append(
                {
                    "source": source.get("name"),
                    "paths": [str(path) for path in paths],
                    "dest_root": args.dest_root
                    or source.get("dest_root")
                    or config.get("target", {}).get("dest_root")
                    or DEFAULT_DEST_ROOT,
                }
            )
        print(
            json.dumps(
                {"dry_run": True, "box_dir": str(box_dir), "sources": preview},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    for source in sources:
        paths = discover_source_paths(source, skip_missing=args.skip_missing)
        if not paths:
            skipped.append({"source": source.get("name"), "reason": "no paths"})
            continue

        dest_root = (
            args.dest_root
            or source.get("dest_root")
            or config.get("target", {}).get("dest_root")
            or DEFAULT_DEST_ROOT
        )
        dest_roots.add(dest_root)
        entries.append(
            create_entry(
                box_dir=box_dir,
                source_paths=paths,
                project=source.get("project") or source.get("name") or "project",
                title=source.get("title") or source.get("name") or "report",
                kind=source.get("kind") or "report",
                date=date,
                dest_root=dest_root,
                notes=source.get("notes") or [],
                source_name=source.get("name"),
                slug=source.get("slug"),
                overwrite=args.overwrite,
            )
        )

    index_paths: list[Path] = []
    for dest_root in sorted(dest_roots):
        index_md, index_json = render_indexes(box_dir, box_dir / dest_root)
        index_paths.extend([index_md, index_json])

    git_result = {"committed": False, "commit": None, "pushed": False}
    if entries and (args.commit or args.push):
        remote, branch = resolve_target(config, args)
        git_result = git_stage_commit_push(
            box_dir,
            [entry.entry_dir for entry in entries] + index_paths,
            args.message or f"Sync reports: {date.isoformat()}",
            push=args.push,
            remote=remote,
            branch=branch,
        )

    result = {
        "entries": [
            {
                "entry_dir": str(entry.entry_dir),
                "manifest": str(entry.manifest_path),
                "file_count": entry.file_count,
            }
            for entry in entries
        ],
        "indexes": [str(path) for path in index_paths],
        "skipped": skipped,
        "git": git_result,
        "status_before": status_before.splitlines(),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def add_common_repo_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--box-dir")
    parser.add_argument("--config")


def add_push_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--remote")
    parser.add_argument("--branch")
    parser.add_argument("--message")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect registered report artifacts into hachi-report-box."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect_parser = subparsers.add_parser("collect")
    add_common_repo_args(collect_parser)
    add_push_args(collect_parser)
    collect_parser.add_argument("sources", nargs="+")
    collect_parser.add_argument("--project")
    collect_parser.add_argument("--title")
    collect_parser.add_argument("--slug")
    collect_parser.add_argument("--source-name")
    collect_parser.add_argument("--kind", default="report")
    collect_parser.add_argument("--date")
    collect_parser.add_argument("--dest-root", default=DEFAULT_DEST_ROOT)
    collect_parser.add_argument("--note", action="append")
    collect_parser.add_argument("--overwrite", action="store_true")
    collect_parser.add_argument("--dry-run", action="store_true")
    collect_parser.set_defaults(func=collect)

    source_parser = subparsers.add_parser("source")
    source_subparsers = source_parser.add_subparsers(dest="source_command", required=True)

    source_add_parser = source_subparsers.add_parser("add")
    add_common_repo_args(source_add_parser)
    source_add_parser.add_argument("name")
    source_add_parser.add_argument("--path", required=True)
    source_add_parser.add_argument("--project")
    source_add_parser.add_argument("--title")
    source_add_parser.add_argument("--kind", default="report")
    source_add_parser.add_argument("--pattern", action="append")
    source_add_parser.add_argument("--note", action="append")
    source_add_parser.add_argument("--dest-root")
    source_add_parser.add_argument("--replace", action="store_true")
    source_add_parser.set_defaults(func=source_add)

    source_list_parser = source_subparsers.add_parser("list")
    add_common_repo_args(source_list_parser)
    source_list_parser.set_defaults(func=source_list)

    source_remove_parser = source_subparsers.add_parser("remove")
    add_common_repo_args(source_remove_parser)
    source_remove_parser.add_argument("name")
    source_remove_parser.set_defaults(func=source_remove)

    target_parser = subparsers.add_parser("target")
    target_subparsers = target_parser.add_subparsers(dest="target_command", required=True)

    target_set_parser = target_subparsers.add_parser("set")
    add_common_repo_args(target_set_parser)
    target_set_parser.add_argument("--remote")
    target_set_parser.add_argument("--branch")
    target_set_parser.add_argument("--dest-root")
    target_set_parser.add_argument("--set-origin", action="store_true")
    target_set_parser.set_defaults(func=target_set)

    target_show_parser = target_subparsers.add_parser("show")
    add_common_repo_args(target_show_parser)
    target_show_parser.set_defaults(func=target_show)

    sync_parser = subparsers.add_parser("sync")
    add_common_repo_args(sync_parser)
    add_push_args(sync_parser)
    sync_parser.add_argument("--source", action="append")
    sync_parser.add_argument("--date")
    sync_parser.add_argument("--dest-root")
    sync_parser.add_argument("--overwrite", action="store_true")
    sync_parser.add_argument("--skip-missing", action="store_true")
    sync_parser.add_argument("--dry-run", action="store_true")
    sync_parser.set_defaults(func=sync)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if hasattr(args, "push") and args.push:
        args.commit = True
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
