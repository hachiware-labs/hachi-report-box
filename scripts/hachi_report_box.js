#!/usr/bin/env node

import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const DEFAULT_CONFIG_FILE = ".hachi-report-box.local.json";
const DEFAULT_DEST_ROOT = "reports";
const SKIP_DIRS = new Set([".git", ".hg", ".svn", "__pycache__", ".pytest_cache"]);

function fail(message) {
  throw new Error(message);
}

function printJson(data) {
  process.stdout.write(`${JSON.stringify(data, null, 2)}\n`);
}

function slugify(value, fallback = "report") {
  const slug = String(value ?? "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, "-")
    .replace(/-{2,}/g, "-")
    .replace(/^[-._]+|[-._]+$/g, "")
    .slice(0, 80);
  return slug || fallback;
}

function pad2(value) {
  return String(value).padStart(2, "0");
}

function localDateString(date = new Date()) {
  return [
    date.getFullYear(),
    pad2(date.getMonth() + 1),
    pad2(date.getDate()),
  ].join("-");
}

function localIsoString(date = new Date()) {
  const offsetMinutes = -date.getTimezoneOffset();
  const sign = offsetMinutes >= 0 ? "+" : "-";
  const absOffset = Math.abs(offsetMinutes);
  const offset = `${sign}${pad2(Math.floor(absOffset / 60))}:${pad2(absOffset % 60)}`;
  return (
    `${localDateString(date)}T${pad2(date.getHours())}:${pad2(date.getMinutes())}:` +
    `${pad2(date.getSeconds())}${offset}`
  );
}

function parseDate(value) {
  if (!value) {
    return localDateString();
  }
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    fail(`--date must be YYYY-MM-DD: ${value}`);
  }
  const parsed = new Date(`${value}T00:00:00`);
  if (Number.isNaN(parsed.getTime()) || localDateString(parsed) !== value) {
    fail(`--date must be a valid YYYY-MM-DD date: ${value}`);
  }
  return value;
}

function expandHome(input) {
  if (!input) {
    return input;
  }
  if (input === "~") {
    return os.homedir();
  }
  if (input.startsWith("~/") || input.startsWith("~\\")) {
    return path.join(os.homedir(), input.slice(2));
  }
  return input;
}

function resolvePath(input) {
  return path.resolve(expandHome(input));
}

function toPosix(relativePath) {
  return relativePath.split(path.sep).join("/");
}

function resolveBoxDir(raw) {
  const candidate =
    raw || process.env.HACHI_REPORT_BOX_DIR || process.env.HACHI_REPORT_BOX_REPO;
  if (candidate) {
    return resolvePath(candidate);
  }

  const fallback = path.join(os.homedir(), "workspace", "hachi-report-box");
  if (fs.existsSync(fallback)) {
    return path.resolve(fallback);
  }
  fail(
    "Report box repository not found. Pass --box-dir or set HACHI_REPORT_BOX_DIR."
  );
}

function resolveConfigPath(boxDir, raw) {
  const candidate = raw || process.env.HACHI_REPORT_BOX_CONFIG;
  if (candidate) {
    return resolvePath(candidate);
  }
  return path.join(boxDir, DEFAULT_CONFIG_FILE);
}

function runGit(cwd, args, { check = true } = {}) {
  const result = spawnSync("git", args, {
    cwd,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  });
  if (check && result.status !== 0) {
    const message = (result.stderr || result.stdout || "").trim();
    fail(`git ${args.join(" ")} failed: ${message}`);
  }
  return result;
}

function ensureGitRepo(repoPath) {
  if (!fs.existsSync(repoPath)) {
    fail(`Report box directory does not exist: ${repoPath}`);
  }
  const result = runGit(repoPath, ["rev-parse", "--is-inside-work-tree"], {
    check: false,
  });
  if (result.status !== 0 || result.stdout.trim() !== "true") {
    fail(`Report box directory is not a Git worktree: ${repoPath}`);
  }
}

function loadConfig(configPath) {
  if (!fs.existsSync(configPath)) {
    return { version: 1, target: {}, sources: [] };
  }
  let data;
  try {
    data = JSON.parse(fs.readFileSync(configPath, "utf8"));
  } catch (error) {
    fail(`Invalid JSON config: ${configPath}: ${error.message}`);
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    fail(`Config must be a JSON object: ${configPath}`);
  }
  data.version ??= 1;
  data.target ??= {};
  data.sources ??= [];
  if (!Array.isArray(data.sources)) {
    fail(`Config sources must be a list: ${configPath}`);
  }
  return data;
}

function writeJson(filePath, data) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, `${JSON.stringify(data, null, 2)}\n`, "utf8");
}

function saveConfig(configPath, config) {
  writeJson(configPath, config);
}

function uniquePath(targetPath, overwrite) {
  if (overwrite || !fs.existsSync(targetPath)) {
    return targetPath;
  }
  const directory = path.dirname(targetPath);
  const extension = path.extname(targetPath);
  const name = path.basename(targetPath, extension);
  for (let index = 2; index < 1000; index += 1) {
    const candidate = path.join(directory, `${name}-${index}${extension}`);
    if (!fs.existsSync(candidate)) {
      return candidate;
    }
  }
  fail(`Could not find an unused destination for ${targetPath}`);
}

function copySource(source, filesDir) {
  if (!fs.existsSync(source)) {
    fail(`Source does not exist: ${source}`);
  }

  const destination = uniquePath(path.join(filesDir, path.basename(source)), false);
  const stat = fs.statSync(source);
  if (stat.isDirectory()) {
    copyDirectory(source, destination);
  } else {
    fs.mkdirSync(path.dirname(destination), { recursive: true });
    fs.copyFileSync(source, destination);
  }
  return destination;
}

function copyDirectory(source, destination) {
  fs.mkdirSync(destination, { recursive: true });
  for (const entry of fs.readdirSync(source, { withFileTypes: true })) {
    if (SKIP_DIRS.has(entry.name)) {
      continue;
    }
    const from = path.join(source, entry.name);
    const to = path.join(destination, entry.name);
    if (entry.isDirectory()) {
      copyDirectory(from, to);
    } else if (entry.isFile()) {
      fs.copyFileSync(from, to);
    }
  }
}

function sha256File(filePath) {
  return createHash("sha256").update(fs.readFileSync(filePath)).digest("hex");
}

function iterFiles(targetPath) {
  if (!fs.existsSync(targetPath)) {
    return [];
  }
  const stat = fs.statSync(targetPath);
  if (stat.isFile()) {
    return [targetPath];
  }

  const files = [];
  for (const entry of fs.readdirSync(targetPath, { withFileTypes: true })) {
    if (SKIP_DIRS.has(entry.name)) {
      continue;
    }
    const child = path.join(targetPath, entry.name);
    if (entry.isDirectory()) {
      files.push(...iterFiles(child));
    } else if (entry.isFile()) {
      files.push(child);
    }
  }
  return files.sort();
}

function collectFileMetadata(entryDir) {
  const filesDir = path.join(entryDir, "files");
  return iterFiles(filesDir).map((filePath) => ({
    path: toPosix(path.relative(entryDir, filePath)),
    size: fs.statSync(filePath).size,
    sha256: sha256File(filePath),
  }));
}

function relToBox(boxDir, targetPath) {
  return toPosix(path.relative(boxDir, targetPath));
}

function readManifest(manifestPath) {
  return JSON.parse(fs.readFileSync(manifestPath, "utf8"));
}

function mdEscape(value) {
  return String(value ?? "").replaceAll("|", "\\|").replaceAll("\n", " ");
}

function manifestSummary(boxDir, manifestPath) {
  const manifest = readManifest(manifestPath);
  const entryDir = path.dirname(manifestPath);
  const files = Array.isArray(manifest.files) ? manifest.files : [];
  return {
    title: manifest.title || path.basename(entryDir),
    project: manifest.project || "",
    kind: manifest.kind || "",
    date: manifest.date || "",
    created_at: manifest.created_at || "",
    entry_path: relToBox(boxDir, entryDir),
    manifest_path: relToBox(boxDir, manifestPath),
    source_name: manifest.source_name || "",
    file_count: files.length,
    notes: manifest.notes || [],
  };
}

function listManifestPaths(reportsDir) {
  if (!fs.existsSync(reportsDir)) {
    return [];
  }
  return iterFiles(reportsDir)
    .filter((filePath) => path.basename(filePath) === "manifest.json")
    .sort();
}

function renderIndexes(boxDir, reportsDir) {
  const summaries = listManifestPaths(reportsDir)
    .map((manifestPath) => manifestSummary(boxDir, manifestPath))
    .sort((left, right) => {
      const leftKey = `${left.date}|${left.created_at}`;
      const rightKey = `${right.date}|${right.created_at}`;
      return rightKey.localeCompare(leftKey);
    });

  const indexJson = path.join(reportsDir, "index.json");
  writeJson(indexJson, { reports: summaries });

  const lines = [
    "# Hachi Report Box Index",
    "",
    "| Date | Project | Kind | Title | Source | Files | Path |",
    "| --- | --- | --- | --- | --- | ---: | --- |",
  ];
  for (const item of summaries) {
    lines.push(
      `| ${mdEscape(item.date)} | ${mdEscape(item.project)} | ${mdEscape(
        item.kind
      )} | ${mdEscape(item.title)} | ${mdEscape(item.source_name)} | ${
        item.file_count
      } | ${mdEscape(item.entry_path)} |`
    );
  }
  lines.push("");

  const indexMd = path.join(reportsDir, "INDEX.md");
  fs.mkdirSync(reportsDir, { recursive: true });
  fs.writeFileSync(indexMd, lines.join("\n"), "utf8");
  return [indexMd, indexJson];
}

function gitStatusShort(cwd) {
  return runGit(cwd, ["status", "--short"]).stdout;
}

function currentBranch(cwd) {
  const branch = runGit(cwd, ["branch", "--show-current"], {
    check: false,
  }).stdout.trim();
  return branch || "main";
}

function ensureOriginRemote(cwd, remote) {
  const existing = runGit(cwd, ["remote", "get-url", "origin"], {
    check: false,
  });
  if (existing.status === 0) {
    runGit(cwd, ["remote", "set-url", "origin", remote]);
  } else {
    runGit(cwd, ["remote", "add", "origin", remote]);
  }
}

function gitStageCommitPush(boxDir, paths, message, push, remote, branch) {
  const pathspecs = paths.map((targetPath) => relToBox(boxDir, targetPath));
  runGit(boxDir, ["add", "--", ...pathspecs]);

  const diff = runGit(boxDir, ["diff", "--cached", "--quiet"], { check: false });
  if (diff.status === 0) {
    return { committed: false, commit: null, pushed: false };
  }

  runGit(boxDir, ["commit", "-m", message]);
  const commit = runGit(boxDir, ["rev-parse", "--short", "HEAD"]).stdout.trim();
  let pushed = false;
  let pushTarget = null;
  if (push) {
    if (remote) {
      const targetBranch = branch || currentBranch(boxDir);
      runGit(boxDir, ["push", remote, `HEAD:${targetBranch}`]);
      pushTarget = `${remote} HEAD:${targetBranch}`;
    } else if (branch) {
      runGit(boxDir, ["push", "origin", `HEAD:${branch}`]);
      pushTarget = `origin HEAD:${branch}`;
    } else {
      runGit(boxDir, ["push"]);
      pushTarget = "default";
    }
    pushed = true;
  }
  return { committed: true, commit, pushed, push_target: pushTarget };
}

function globToRegex(pattern) {
  const normalized = pattern.replaceAll("\\", "/");
  let regex = "^";
  for (let index = 0; index < normalized.length; index += 1) {
    const char = normalized[index];
    const next = normalized[index + 1];
    const nextNext = normalized[index + 2];
    if (char === "*" && next === "*" && nextNext === "/") {
      regex += "(?:.*/)?";
      index += 2;
    } else if (char === "*" && next === "*") {
      regex += ".*";
      index += 1;
    } else if (char === "*") {
      regex += "[^/]*";
    } else if (char === "?") {
      regex += "[^/]";
    } else if ("\\^$+?.()|{}[]".includes(char)) {
      regex += `\\${char}`;
    } else {
      regex += char;
    }
  }
  regex += "$";
  return new RegExp(regex);
}

function walkEntries(root) {
  const entries = [];
  function walk(directory) {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      if (SKIP_DIRS.has(entry.name)) {
        continue;
      }
      const fullPath = path.join(directory, entry.name);
      entries.push(fullPath);
      if (entry.isDirectory()) {
        walk(fullPath);
      }
    }
  }
  walk(root);
  return entries;
}

function discoverSourcePaths(source, skipMissing = false) {
  const sourcePath = resolvePath(String(source.path));
  if (!fs.existsSync(sourcePath)) {
    if (skipMissing) {
      return [];
    }
    fail(`Registered source does not exist: ${sourcePath}`);
  }

  const patterns = Array.isArray(source.patterns) ? source.patterns : [];
  if (patterns.length === 0 || fs.statSync(sourcePath).isFile()) {
    return [sourcePath];
  }

  const regexes = patterns.map(globToRegex);
  const matches = new Map();
  for (const candidate of walkEntries(sourcePath)) {
    const relative = toPosix(path.relative(sourcePath, candidate));
    if (regexes.some((regex) => regex.test(relative))) {
      matches.set(path.resolve(candidate), path.resolve(candidate));
    }
  }
  return [...matches.keys()].sort();
}

function createEntry({
  boxDir,
  sourcePaths,
  project,
  title,
  kind,
  date,
  destRoot,
  notes,
  sourceName,
  slug,
  overwrite,
}) {
  const projectSlug = slugify(project, "project");
  const entrySlug = slugify(slug || title);
  const [year] = date.split("-");
  const reportsDir = path.join(boxDir, destRoot);
  const entryParent = path.join(reportsDir, projectSlug, year, date);
  const entryDir = uniquePath(path.join(entryParent, entrySlug), overwrite);
  const filesDir = path.join(entryDir, "files");
  fs.mkdirSync(filesDir, { recursive: true });

  const copiedRoots = [];
  for (const source of sourcePaths) {
    const copiedTo = copySource(source, filesDir);
    copiedRoots.push({
      source,
      copied_to: relToBox(boxDir, copiedTo),
    });
  }

  const copiedFiles = collectFileMetadata(entryDir);
  const manifest = {
    title,
    project,
    project_slug: projectSlug,
    kind,
    date,
    created_at: localIsoString(),
    entry_path: relToBox(boxDir, entryDir),
    source_name: sourceName,
    sources: copiedRoots,
    notes,
    files: copiedFiles,
  };
  const manifestPath = path.join(entryDir, "manifest.json");
  writeJson(manifestPath, manifest);
  return { entryDir, manifestPath, fileCount: copiedFiles.length };
}

function selectedSources(config, names) {
  const sources = config.sources || [];
  if (!names || names.length === 0) {
    return sources;
  }

  const byName = new Map(sources.map((source) => [source.name, source]));
  return names.map((name) => {
    if (!byName.has(name)) {
      fail(`Unknown source: ${name}`);
    }
    return byName.get(name);
  });
}

function resolveTarget(config, options) {
  const target = config.target || {};
  return {
    remote: options.remote || target.remote,
    branch: options.branch || target.branch,
  };
}

function splitStatusLines(output) {
  const trimmed = output.trimEnd();
  return trimmed ? trimmed.split(/\r?\n/) : [];
}

function collectCommand(options) {
  const boxDir = resolveBoxDir(options.boxDir);
  ensureGitRepo(boxDir);
  const config = loadConfig(resolveConfigPath(boxDir, options.config));

  const date = parseDate(options.date);
  const project = options.project || path.basename(process.cwd());
  const title = options.title || `${options.kind} ${timeSlug()}`;
  const sourcePaths = options.sources.map(resolvePath);
  if (options.dryRun) {
    const entryDir = path.join(
      boxDir,
      options.destRoot,
      slugify(project, "project"),
      date.slice(0, 4),
      date,
      slugify(options.slug || title)
    );
    printJson({
      dry_run: true,
      box_dir: boxDir,
      entry_dir: entryDir,
      sources: sourcePaths,
    });
    return;
  }

  const statusBefore = gitStatusShort(boxDir);
  const entry = createEntry({
    boxDir,
    sourcePaths,
    project,
    title,
    kind: options.kind,
    date,
    destRoot: options.destRoot,
    notes: options.note,
    sourceName: options.sourceName,
    slug: options.slug,
    overwrite: options.overwrite,
  });
  const [indexMd, indexJson] = renderIndexes(boxDir, path.join(boxDir, options.destRoot));

  let git = { committed: false, commit: null, pushed: false };
  if (options.commit || options.push) {
    const target = resolveTarget(config, options);
    git = gitStageCommitPush(
      boxDir,
      [entry.entryDir, indexMd, indexJson],
      options.message || `Add report: ${title}`,
      options.push,
      target.remote,
      target.branch
    );
  }

  printJson({
    entry_dir: entry.entryDir,
    manifest: entry.manifestPath,
    index_md: indexMd,
    index_json: indexJson,
    file_count: entry.fileCount,
    git,
    status_before: splitStatusLines(statusBefore),
  });
}

function timeSlug(date = new Date()) {
  return `${pad2(date.getHours())}${pad2(date.getMinutes())}${pad2(
    date.getSeconds()
  )}`;
}

function sourceAddCommand(options) {
  const boxDir = resolveBoxDir(options.boxDir);
  ensureGitRepo(boxDir);
  const configPath = resolveConfigPath(boxDir, options.config);
  const config = loadConfig(configPath);

  const sources = config.sources;
  const existingIndex = sources.findIndex((source) => source.name === options.name);
  if (existingIndex !== -1 && !options.replace) {
    fail(`Source already exists: ${options.name}. Use --replace.`);
  }

  const source = {
    name: options.name,
    type: "local",
    path: resolvePath(options.path),
    project: options.project || options.name,
    title: options.title || options.name,
    kind: options.kind,
    patterns: options.pattern,
    notes: options.note,
  };
  if (options.destRoot) {
    source.dest_root = options.destRoot;
  }

  if (existingIndex === -1) {
    sources.push(source);
  } else {
    sources[existingIndex] = source;
  }
  sources.sort((left, right) => left.name.localeCompare(right.name));
  saveConfig(configPath, config);
  printJson({ config: configPath, source });
}

function sourceListCommand(options) {
  const boxDir = resolveBoxDir(options.boxDir);
  ensureGitRepo(boxDir);
  const configPath = resolveConfigPath(boxDir, options.config);
  const config = loadConfig(configPath);
  printJson({ config: configPath, sources: config.sources || [] });
}

function sourceRemoveCommand(options) {
  const boxDir = resolveBoxDir(options.boxDir);
  ensureGitRepo(boxDir);
  const configPath = resolveConfigPath(boxDir, options.config);
  const config = loadConfig(configPath);
  const before = config.sources.length;
  config.sources = config.sources.filter((source) => source.name !== options.name);
  if (config.sources.length === before) {
    fail(`Unknown source: ${options.name}`);
  }
  saveConfig(configPath, config);
  printJson({ config: configPath, removed: options.name });
}

function targetSetCommand(options) {
  const boxDir = resolveBoxDir(options.boxDir);
  ensureGitRepo(boxDir);
  const configPath = resolveConfigPath(boxDir, options.config);
  const config = loadConfig(configPath);
  const target = config.target || {};
  config.target = target;

  if (options.remote) {
    target.remote = options.remote;
  }
  if (options.branch) {
    target.branch = options.branch;
  }
  if (options.destRoot) {
    target.dest_root = options.destRoot;
  }
  if (options.setOrigin) {
    if (!options.remote) {
      fail("--set-origin requires --remote");
    }
    ensureOriginRemote(boxDir, options.remote);
  }

  saveConfig(configPath, config);
  printJson({ config: configPath, target });
}

function targetShowCommand(options) {
  const boxDir = resolveBoxDir(options.boxDir);
  ensureGitRepo(boxDir);
  const configPath = resolveConfigPath(boxDir, options.config);
  const config = loadConfig(configPath);
  printJson({ config: configPath, target: config.target || {} });
}

function syncCommand(options) {
  const boxDir = resolveBoxDir(options.boxDir);
  ensureGitRepo(boxDir);
  const configPath = resolveConfigPath(boxDir, options.config);
  const config = loadConfig(configPath);
  const sources = selectedSources(config, options.source);
  if (sources.length === 0) {
    fail("No report sources registered. Use source add first.");
  }

  const date = parseDate(options.date);
  if (options.dryRun) {
    const preview = sources.map((source) => ({
      source: source.name,
      paths: discoverSourcePaths(source, options.skipMissing),
      dest_root:
        options.destRoot ||
        source.dest_root ||
        config.target?.dest_root ||
        DEFAULT_DEST_ROOT,
    }));
    printJson({ dry_run: true, box_dir: boxDir, sources: preview });
    return;
  }

  const statusBefore = gitStatusShort(boxDir);
  const entries = [];
  const skipped = [];
  const destRoots = new Set();

  for (const source of sources) {
    const paths = discoverSourcePaths(source, options.skipMissing);
    if (paths.length === 0) {
      skipped.push({ source: source.name, reason: "no paths" });
      continue;
    }

    const destRoot =
      options.destRoot || source.dest_root || config.target?.dest_root || DEFAULT_DEST_ROOT;
    destRoots.add(destRoot);
    entries.push(
      createEntry({
        boxDir,
        sourcePaths: paths,
        project: source.project || source.name || "project",
        title: source.title || source.name || "report",
        kind: source.kind || "report",
        date,
        destRoot,
        notes: source.notes || [],
        sourceName: source.name,
        slug: source.slug,
        overwrite: options.overwrite,
      })
    );
  }

  const indexPaths = [];
  for (const destRoot of [...destRoots].sort()) {
    indexPaths.push(...renderIndexes(boxDir, path.join(boxDir, destRoot)));
  }

  let git = { committed: false, commit: null, pushed: false };
  if (entries.length > 0 && (options.commit || options.push)) {
    const target = resolveTarget(config, options);
    git = gitStageCommitPush(
      boxDir,
      [...entries.map((entry) => entry.entryDir), ...indexPaths],
      options.message || `Sync reports: ${date}`,
      options.push,
      target.remote,
      target.branch
    );
  }

  printJson({
    entries: entries.map((entry) => ({
      entry_dir: entry.entryDir,
      manifest: entry.manifestPath,
      file_count: entry.fileCount,
    })),
    indexes: indexPaths,
    skipped,
    git,
    status_before: splitStatusLines(statusBefore),
  });
}

function parseOptions(tokens, definitions, positionalNames = []) {
  const options = {};
  for (const [flag, definition] of Object.entries(definitions)) {
    if (definition.type === "array") {
      options[definition.key] = [];
    } else if (definition.type === "bool") {
      options[definition.key] = false;
    } else if ("default" in definition) {
      options[definition.key] = definition.default;
    } else {
      options[definition.key] = undefined;
    }
  }

  const positional = [];
  for (let index = 0; index < tokens.length; index += 1) {
    const token = tokens[index];
    if (token === "--") {
      positional.push(...tokens.slice(index + 1));
      break;
    }
    if (!token.startsWith("--")) {
      positional.push(token);
      continue;
    }

    const [rawFlag, inlineValue] = token.slice(2).split(/=(.*)/s, 2);
    const definition = definitions[rawFlag];
    if (!definition) {
      fail(`Unknown option: --${rawFlag}`);
    }
    if (definition.type === "bool") {
      options[definition.key] = true;
      continue;
    }

    let value = inlineValue;
    if (value === undefined) {
      index += 1;
      if (index >= tokens.length) {
        fail(`Missing value for --${rawFlag}`);
      }
      value = tokens[index];
    }
    if (definition.type === "array") {
      options[definition.key].push(value);
    } else {
      options[definition.key] = value;
    }
  }

  for (const name of positionalNames) {
    if (name.rest) {
      options[name.key] = positional.splice(0);
    } else {
      const value = positional.shift();
      if (!value && name.required) {
        fail(`Missing positional argument: ${name.key}`);
      }
      options[name.key] = value;
    }
  }
  if (positional.length > 0) {
    fail(`Unexpected positional arguments: ${positional.join(" ")}`);
  }
  return options;
}

function commonRepoDefinitions() {
  return {
    "box-dir": { key: "boxDir", type: "value" },
    config: { key: "config", type: "value" },
  };
}

function pushDefinitions() {
  return {
    commit: { key: "commit", type: "bool" },
    push: { key: "push", type: "bool" },
    remote: { key: "remote", type: "value" },
    branch: { key: "branch", type: "value" },
    message: { key: "message", type: "value" },
  };
}

function topUsage() {
  return `Usage: hachi-report-box <command> [options]

Commands:
  collect <sources...>        Collect one-off report files or directories
  source add|list|remove      Manage registered report sources
  target set|show             Manage the GitHub push target
  sync                        Fetch registered sources, index, commit, and push
`;
}

function sourceUsage() {
  return `Usage: hachi-report-box source <command> [options]

Commands:
  add <name> --path <path>    Register a report source
  list                       List registered sources
  remove <name>              Remove a registered source
`;
}

function targetUsage() {
  return `Usage: hachi-report-box target <command> [options]

Commands:
  set --remote <url>          Configure target GitHub repository
  show                       Show target configuration
`;
}

function dispatch(argv) {
  if (argv.length === 0 || argv[0] === "--help" || argv[0] === "-h") {
    process.stdout.write(topUsage());
    return;
  }

  const [command, subcommand, ...rest] = argv;
  if (command === "collect") {
    const options = parseOptions(
      [subcommand, ...rest].filter((value) => value !== undefined),
      {
        ...commonRepoDefinitions(),
        ...pushDefinitions(),
        project: { key: "project", type: "value" },
        title: { key: "title", type: "value" },
        slug: { key: "slug", type: "value" },
        "source-name": { key: "sourceName", type: "value" },
        kind: { key: "kind", type: "value", default: "report" },
        date: { key: "date", type: "value" },
        "dest-root": { key: "destRoot", type: "value", default: DEFAULT_DEST_ROOT },
        note: { key: "note", type: "array" },
        overwrite: { key: "overwrite", type: "bool" },
        "dry-run": { key: "dryRun", type: "bool" },
      },
      [{ key: "sources", rest: true }]
    );
    if (options.push) {
      options.commit = true;
    }
    if (options.sources.length === 0) {
      fail("collect requires at least one source path");
    }
    collectCommand(options);
    return;
  }

  if (command === "source") {
    if (!subcommand || subcommand === "--help" || subcommand === "-h") {
      process.stdout.write(sourceUsage());
      return;
    }
    if (subcommand === "add") {
      const options = parseOptions(
        rest,
        {
          ...commonRepoDefinitions(),
          path: { key: "path", type: "value" },
          project: { key: "project", type: "value" },
          title: { key: "title", type: "value" },
          kind: { key: "kind", type: "value", default: "report" },
          pattern: { key: "pattern", type: "array" },
          note: { key: "note", type: "array" },
          "dest-root": { key: "destRoot", type: "value" },
          replace: { key: "replace", type: "bool" },
        },
        [{ key: "name", required: true }]
      );
      if (!options.path) {
        fail("source add requires --path");
      }
      sourceAddCommand(options);
      return;
    }
    if (subcommand === "list") {
      sourceListCommand(parseOptions(rest, commonRepoDefinitions()));
      return;
    }
    if (subcommand === "remove") {
      sourceRemoveCommand(
        parseOptions(rest, commonRepoDefinitions(), [{ key: "name", required: true }])
      );
      return;
    }
    fail(`Unknown source command: ${subcommand}`);
  }

  if (command === "target") {
    if (!subcommand || subcommand === "--help" || subcommand === "-h") {
      process.stdout.write(targetUsage());
      return;
    }
    if (subcommand === "set") {
      targetSetCommand(
        parseOptions(rest, {
          ...commonRepoDefinitions(),
          remote: { key: "remote", type: "value" },
          branch: { key: "branch", type: "value" },
          "dest-root": { key: "destRoot", type: "value" },
          "set-origin": { key: "setOrigin", type: "bool" },
        })
      );
      return;
    }
    if (subcommand === "show") {
      targetShowCommand(parseOptions(rest, commonRepoDefinitions()));
      return;
    }
    fail(`Unknown target command: ${subcommand}`);
  }

  if (command === "sync") {
    const options = parseOptions([subcommand, ...rest].filter(Boolean), {
      ...commonRepoDefinitions(),
      ...pushDefinitions(),
      source: { key: "source", type: "array" },
      date: { key: "date", type: "value" },
      "dest-root": { key: "destRoot", type: "value" },
      overwrite: { key: "overwrite", type: "bool" },
      "skip-missing": { key: "skipMissing", type: "bool" },
      "dry-run": { key: "dryRun", type: "bool" },
    });
    if (options.push) {
      options.commit = true;
    }
    syncCommand(options);
    return;
  }

  fail(`Unknown command: ${command}`);
}

try {
  dispatch(process.argv.slice(2));
} catch (error) {
  process.stderr.write(`${error.message}\n`);
  process.exit(1);
}
