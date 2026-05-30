"""Download InkyPi release tarballs from GitHub and stage them over
the working checkout.

Adds a path-of-least-resistance OTA flow alongside the existing
`git pull && update.sh` apply. It does NOT (yet) replace git pull —
both paths share the same install/update.sh, so this is purely a
different way of getting the source tree onto the Pi:

  • Pi has no upstream git remote? Tarball works.
  • Local tree is dirty (unstaged edits the user forgot)? Tarball
    sidesteps the "Working tree has uncommitted changes" guard.
  • User wants to pin to a specific release tag without dealing
    with detached HEAD? Tarball lands the exact tree.

The flow:

  1. List releases via the GitHub REST API.
  2. Download the chosen release's source tarball (HTTPS, TLS-verified).
  3. Extract into a temp dir.
  4. rsync the extracted tree into the working checkout, preserving
     `.git/` if it exists so dual-mode (git-pull + tarball) keeps
     working.
  5. Re-run install/update.sh against the new tree — same script,
     same systemd handling, same marker-write semantics.

We stay stdlib-only (urllib + tarfile + shutil) so the Pi doesn't
need an extra Python dep installed before OTA can fetch updates.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from typing import Generator, Optional

logger = logging.getLogger(__name__)


def _git(repo_root: str, *args: str, timeout: int = 5) -> subprocess.CompletedProcess:
    return subprocess.run(
        ['git', '-c', 'safe.directory=*', '-C', repo_root, *args],
        capture_output=True, text=True, timeout=timeout,
    )


def detect_github_repo(repo_root: str) -> Optional[tuple[str, str]]:
    """Returns (owner, repo) inferred from `git remote get-url origin`,
    or None if the remote isn't a GitHub URL we can parse.

    Handles both HTTPS and SSH style remotes:
      https://github.com/owner/repo[.git]
      git@github.com:owner/repo[.git]
    """
    try:
        r = _git(repo_root, 'remote', 'get-url', 'origin')
    except Exception:
        return None
    if r.returncode != 0:
        return None
    url = r.stdout.strip()
    # HTTPS
    m = re.match(r'^https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$', url)
    if m:
        return m.group(1), m.group(2)
    # SSH
    m = re.match(r'^git@github\.com:([^/]+)/([^/]+?)(?:\.git)?$', url)
    if m:
        return m.group(1), m.group(2)
    return None


def list_releases(repo_root: str, limit: int = 10) -> list[dict]:
    """Recent releases for the upstream GitHub repo.

    Returns up to `limit` entries with fields the companion app
    needs: tag, name, published_at, tarball_url, prerelease, body.
    """
    owner_repo = detect_github_repo(repo_root)
    if not owner_repo:
        raise RuntimeError("Could not infer GitHub owner/repo from remote 'origin'.")
    owner, repo = owner_repo
    url = f'https://api.github.com/repos/{owner}/{repo}/releases?per_page={int(limit)}'
    req = urllib.request.Request(
        url, headers={
            'Accept':     'application/vnd.github+json',
            'User-Agent': 'inkypi-ota/1',
        })
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 (https)
        data = json.loads(resp.read().decode('utf-8'))
    out = []
    for r in data:
        out.append({
            'tag':           r.get('tag_name'),
            'name':          r.get('name'),
            'published_at':  r.get('published_at'),
            'tarball_url':   r.get('tarball_url'),
            'prerelease':    bool(r.get('prerelease')),
            'body':          (r.get('body') or '')[:4096],
        })
    return out


def _download_tarball(tarball_url: str, dest_path: str) -> None:
    """Stream a tarball to disk. Caller owns dest_path."""
    req = urllib.request.Request(
        tarball_url, headers={'User-Agent': 'inkypi-ota/1'})
    with urllib.request.urlopen(req, timeout=120) as resp, open(dest_path, 'wb') as f:  # noqa: S310
        shutil.copyfileobj(resp, f, length=1 << 16)


def _safe_extract(tar_path: str, extract_dir: str) -> str:
    """Extract a tarball with traversal guards. Returns the single
    top-level directory name (GitHub source tarballs are wrapped in
    one top-level dir like `owner-repo-<sha>`)."""
    with tarfile.open(tar_path, 'r:*') as tf:
        members = tf.getmembers()
        # Reject absolute paths and `..` traversal.
        for m in members:
            target = os.path.normpath(os.path.join(extract_dir, m.name))
            if not target.startswith(os.path.abspath(extract_dir) + os.sep):
                raise RuntimeError(f"Refusing to extract path-traversing member: {m.name}")
        tf.extractall(extract_dir)  # noqa: S202 (members validated above)
    # Find the unique top-level dir.
    entries = [e for e in os.listdir(extract_dir) if not e.startswith('.')]
    if len(entries) != 1:
        raise RuntimeError(f"Tarball has {len(entries)} top-level entries; expected 1.")
    return os.path.join(extract_dir, entries[0])


def _rsync_overlay(src_dir: str, dest_dir: str) -> None:
    """Copy files from src_dir into dest_dir, OVERWRITING existing
    paths but PRESERVING dest_dir/.git so subsequent git operations
    still work. Uses rsync when available (Pi has it pre-installed),
    falls back to a stdlib copy otherwise.
    """
    if shutil.which('rsync'):
        cmd = [
            'rsync', '-a',
            '--exclude=.git/',
            f'{src_dir.rstrip("/")}/',
            f'{dest_dir.rstrip("/")}/',
        ]
        subprocess.run(cmd, check=True, timeout=300)
        return
    # Fallback — Python copy. Slower; we don't expect to hit this on
    # the Pi but the harness tests should exercise it on macOS.
    for root, dirs, files in os.walk(src_dir):
        rel = os.path.relpath(root, src_dir)
        # Skip top-level .git (we don't expect one in the tarball,
        # but defense in depth).
        if rel == '.git' or rel.startswith('.git/' + os.sep) or rel.startswith('.git' + os.sep):
            continue
        target_root = os.path.join(dest_dir, rel) if rel != '.' else dest_dir
        os.makedirs(target_root, exist_ok=True)
        for f in files:
            shutil.copy2(os.path.join(root, f), os.path.join(target_root, f))


def apply_release_streaming(
    repo_root: str,
    tag: str,
    *,
    defer_restart: bool = True,
) -> Generator[dict, None, None]:
    """Drive the full OTA: list/find/download/extract/overlay/run.

    Yields plain dicts the caller serializes; lets blueprints/main.py
    keep its existing NDJSON-emit pattern without coupling this
    module to Flask.

    Stage names mirror the existing `apply_update_streaming` so the
    companion app's progress UI can reuse its stage-pill state machine
    with just one extra stage: `download` (and skips `git_pull`).
    """
    import time

    yield {"event": "stage_start", "stage": "preflight"}

    # Resolve the tarball_url for the requested tag.
    try:
        releases = list_releases(repo_root, limit=50)
    except Exception as exc:
        yield {"event": "done", "success": False, "stage": "preflight",
               "error": f"Could not list releases: {exc}"}
        return
    match = next((r for r in releases if r['tag'] == tag), None)
    if not match:
        yield {"event": "done", "success": False, "stage": "preflight",
               "error": f"Release tag '{tag}' not found in the upstream repo."}
        return
    tarball_url = match['tarball_url']
    yield {"event": "stage_complete", "stage": "preflight",
           "step": {"cmd": f"resolve {tag}", "exit_code": 0,
                    "stdout": tarball_url, "stderr": "", "timed_out": False}}

    # Download.
    yield {"event": "stage_start", "stage": "download"}
    workdir = tempfile.mkdtemp(prefix='inkypi-ota-')
    tar_path = os.path.join(workdir, 'release.tar.gz')
    try:
        t0 = time.monotonic()
        yield {"event": "log", "line": f"Downloading {tarball_url} …"}
        _download_tarball(tarball_url, tar_path)
        size = os.path.getsize(tar_path)
        yield {"event": "log",
               "line": f"Downloaded {size} bytes in {time.monotonic() - t0:.1f}s"}

        # Extract.
        yield {"event": "log", "line": "Extracting tarball …"}
        extracted = _safe_extract(tar_path, workdir)
        yield {"event": "log", "line": f"Extracted into {extracted}"}

        # Overlay.
        yield {"event": "log", "line": f"Overlaying onto {repo_root} (preserving .git/) …"}
        _rsync_overlay(extracted, repo_root)
        yield {"event": "stage_complete", "stage": "download",
               "step": {"cmd": f"download+extract+overlay {tag}",
                        "exit_code": 0, "stdout": "", "stderr": "",
                        "timed_out": False}}
    except Exception as exc:
        logger.exception("OTA download/extract/overlay failed")
        yield {"event": "done", "success": False, "stage": "download",
               "error": f"OTA staging failed: {exc}"}
        return
    finally:
        # Best-effort cleanup of the workdir (keeps the freshly
        # overlaid checkout in repo_root, which we want).
        try:
            shutil.rmtree(workdir, ignore_errors=True)
        except Exception:
            pass

    # Hand off to update.sh — same script, same semantics.
    yield {"event": "stage_start", "stage": "update_sh"}
    cmd = ['sudo', '-E', 'bash', os.path.join(repo_root, 'install', 'update.sh'), '--force']
    if defer_restart:
        cmd.append('--defer-restart')
    proc = subprocess.Popen(  # noqa: S603
        cmd, cwd=repo_root,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        bufsize=1, text=True,
        env={**os.environ, 'TERM': 'dumb'},
    )
    collected = []
    timed_out = False
    start = time.monotonic()
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip('\n')
            collected.append(line)
            yield {"event": "log", "line": line}
            if time.monotonic() - start > 30 * 60:
                proc.kill()
                timed_out = True
                break
    finally:
        try:
            exit_code = proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            exit_code = -1
            timed_out = True

    step = {"cmd": ' '.join(cmd), "exit_code": exit_code,
            "stdout": '\n'.join(collected[-1000:]), "stderr": "",
            "timed_out": timed_out}
    yield {"event": "stage_complete", "stage": "update_sh", "step": step}
    yield {"event": "done", "success": exit_code == 0 and not timed_out,
           "stage": "update_sh", "applied_tag": tag, "steps": [step]}
