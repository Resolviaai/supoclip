#!/usr/bin/env python3
"""
SupoClip Auto-Updater
====================
Automatically synchronizes the local repository and Resolvia fork with the
upstream repository (FujiwaraChoki/supoclip).

Design Principles:
1. Non-destructive: If working directory has changes, safely stashes them.
2. Conflict-immune: If an upstream rebase produces conflicts, immediately
   aborts the rebase (git rebase --abort) and leaves the workspace clean.
3. Bidirectional Sync:
   - Fetches upstream/main
   - Fast-forwards local main to match upstream/main exactly
   - Rebase resolvia-features onto updated main
   - Pushes both main and resolvia-features to origin (Resolviaai/supoclip)
4. Daemon & Single-Shot Modes:
   - Run once: python scripts/auto_updater.py
   - Run in background: python scripts/auto_updater.py --daemon --interval 3600
   - Check status: python scripts/auto_updater.py --status
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Dict, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
LOG_FILE = REPO_ROOT / "auto_updater.log"
STATUS_FILE = REPO_ROOT / "auto_updater_status.json"

UPSTREAM_URL = "https://github.com/FujiwaraChoki/supoclip.git"
ORIGIN_URL = "https://github.com/Resolviaai/supoclip.git"
FEATURE_BRANCH = "resolvia-features"
MAIN_BRANCH = "main"

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("auto_updater")


def run_cmd(
    cmd: list[str],
    cwd: Optional[Path] = None,
    check: bool = True,
    capture: bool = True,
) -> Tuple[int, str, str]:
    """Execute a system command and return (returncode, stdout, stderr)."""
    cwd = cwd or REPO_ROOT
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        stdout = proc.stdout.strip() if proc.stdout else ""
        stderr = proc.stderr.strip() if proc.stderr else ""
        if check and proc.returncode != 0:
            raise RuntimeError(
                f"Command failed (code {proc.returncode}): {' '.join(cmd)}\nStderr: {stderr}\nStdout: {stdout}"
            )
        return proc.returncode, stdout, stderr
    except Exception as exc:
        if check:
            raise
        return -1, "", str(exc)


def ensure_remotes() -> None:
    """Ensure upstream and origin remotes are properly configured."""
    code, remotes_out, _ = run_cmd(["git", "remote"], check=True)
    remotes = set(remotes_out.splitlines())

    if "upstream" not in remotes:
        logger.info(f"Adding upstream remote: {UPSTREAM_URL}")
        run_cmd(["git", "remote", "add", "upstream", UPSTREAM_URL], check=True)
    else:
        # Check URL
        _, url, _ = run_cmd(["git", "remote", "get-url", "upstream"], check=False)
        if url != UPSTREAM_URL:
            logger.info(f"Updating upstream URL to {UPSTREAM_URL}")
            run_cmd(["git", "remote", "set-url", "upstream", UPSTREAM_URL], check=True)

    if "origin" not in remotes:
        logger.info(f"Adding origin remote: {ORIGIN_URL}")
        run_cmd(["git", "remote", "add", "origin", ORIGIN_URL], check=True)


def get_current_branch() -> str:
    """Get the currently checked out git branch."""
    _, branch, _ = run_cmd(["git", "branch", "--show-current"], check=True)
    return branch.strip()


def is_working_tree_dirty() -> bool:
    """Return True if there are uncommitted or untracked changes."""
    _, status, _ = run_cmd(["git", "status", "--porcelain"], check=True)
    # Check if there are unstaged or staged changes (ignore untracked .env, logs)
    return bool(status.strip())


def get_commit_hash(ref: str) -> str:
    """Get commit SHA for a ref."""
    code, sha, _ = run_cmd(["git", "rev-parse", ref], check=False)
    return sha.strip() if code == 0 else ""


def save_status(
    status: str,
    message: str,
    upstream_commit: str = "",
    feature_commit: str = "",
) -> None:
    """Persist status state for health checks and dashboards."""
    data = {
        "status": status,
        "message": message,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "upstream_commit": upstream_commit,
        "feature_commit": feature_commit,
    }
    try:
        with open(STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.warning(f"Failed to write status file: {e}")


def sync_dependencies(changed_files: list[str]) -> None:
    """If backend pyproject.toml or frontend package.json changed, update dependencies."""
    backend_changed = any("backend/pyproject.toml" in f or "backend/uv.lock" in f for f in changed_files)
    frontend_changed = any("frontend/package.json" in f or "frontend/pnpm-lock.yaml" in f for f in changed_files)

    if backend_changed:
        logger.info("Backend dependencies changed. Running uv sync...")
        run_cmd(["uv", "sync"], cwd=REPO_ROOT / "backend", check=False)

    if frontend_changed:
        logger.info("Frontend dependencies changed. Running pnpm install...")
        run_cmd(["pnpm", "install"], cwd=REPO_ROOT / "frontend", check=False)


def perform_update() -> bool:
    """
    Main update routine.
    Returns True if an update was performed, False if already up to date.
    Raises RuntimeError on non-recoverable error.
    """
    logger.info("Starting update check...")
    ensure_remotes()

    # Step 1: Fetch upstream main
    logger.info("Fetching latest changes from upstream (FujiwaraChoki/supoclip)...")
    code, _, stderr = run_cmd(["git", "fetch", "upstream", MAIN_BRANCH], check=False)
    if code != 0:
        logger.error(f"Failed to fetch from upstream: {stderr}")
        save_status("ERROR", f"Failed to fetch from upstream: {stderr}")
        return False

    upstream_sha = get_commit_hash(f"upstream/{MAIN_BRANCH}")
    local_main_sha = get_commit_hash(MAIN_BRANCH)

    logger.info(f"Upstream {MAIN_BRANCH} SHA: {upstream_sha[:9]}")
    logger.info(f"Local {MAIN_BRANCH} SHA:    {local_main_sha[:9]}")

    if upstream_sha and upstream_sha == local_main_sha:
        logger.info("Local main is already up to date with upstream/main. No update required.")
        feature_sha = get_commit_hash(FEATURE_BRANCH)
        save_status("UP_TO_DATE", "Already up to date with upstream", upstream_sha, feature_sha)
        return False

    # Step 2: Handle uncommitted changes safely
    initial_branch = get_current_branch()
    was_dirty = is_working_tree_dirty()
    stash_created = False

    if was_dirty:
        logger.info("Working directory has uncommitted modifications. Creating protective stash...")
        stash_msg = f"auto-updater-backup-{int(time.time())}"
        code, stash_out, _ = run_cmd(
            ["git", "stash", "push", "-u", "-m", stash_msg], check=False
        )
        if code == 0 and "Saved working directory" in stash_out:
            stash_created = True
            logger.info("Safely stashed uncommitted changes.")

    try:
        # Step 3: Fast-forward local main
        logger.info(f"Switching to {MAIN_BRANCH} to fast-forward upstream changes...")
        run_cmd(["git", "checkout", MAIN_BRANCH], check=True)
        code, merge_out, merge_err = run_cmd(
            ["git", "merge", "--ff-only", f"upstream/{MAIN_BRANCH}"], check=False
        )
        if code != 0:
            logger.error(f"Fast-forward merge of main failed: {merge_err}")
            save_status("ERROR", f"Fast-forward merge failed: {merge_err}", upstream_sha)
            return False

        logger.info(f"Fast-forwarded {MAIN_BRANCH} successfully.")

        # Step 4: Push updated main to origin fork
        logger.info(f"Pushing updated {MAIN_BRANCH} to origin...")
        run_cmd(["git", "push", "origin", MAIN_BRANCH], check=False)

        # Step 5: Check what files changed between old main and new main
        code, diff_out, _ = run_cmd(
            ["git", "diff", "--name-only", local_main_sha, upstream_sha],
            check=False,
        )
        changed_files = diff_out.splitlines() if code == 0 else []

        # Step 6: Rebase feature branch (resolvia-features) onto updated main
        logger.info(f"Switching to {FEATURE_BRANCH} to rebase onto updated {MAIN_BRANCH}...")
        run_cmd(["git", "checkout", FEATURE_BRANCH], check=True)

        logger.info(f"Rebasing {FEATURE_BRANCH} onto {MAIN_BRANCH}...")
        code, rebase_out, rebase_err = run_cmd(["git", "rebase", MAIN_BRANCH], check=False)

        if code != 0:
            logger.critical("CONFLICT DETECTED during rebase! Aborting rebase to preserve stability.")
            logger.critical(f"Rebase error output:\n{rebase_err or rebase_out}")
            run_cmd(["git", "rebase", "--abort"], check=False)
            save_status(
                "CONFLICT",
                "Conflict detected between upstream changes and resolvia-features. Rebase safely aborted.",
                upstream_sha,
                get_commit_hash(FEATURE_BRANCH),
            )
            return False

        logger.info(f"Rebase succeeded cleanly! All custom features preserved.")
        new_feature_sha = get_commit_hash(FEATURE_BRANCH)

        # Step 7: Push rebased feature branch to origin
        logger.info(f"Pushing rebased {FEATURE_BRANCH} to origin with lease...")
        run_cmd(
            ["git", "push", "origin", FEATURE_BRANCH, "--force-with-lease"],
            check=False,
        )

        # Step 8: Sync dependencies if package files changed
        if changed_files:
            sync_dependencies(changed_files)

        save_status(
            "SUCCESS",
            f"Successfully synchronized upstream commit {upstream_sha[:9]} into {FEATURE_BRANCH}.",
            upstream_sha,
            new_feature_sha,
        )
        logger.info("Auto-update completed successfully!")
        return True

    finally:
        # Step 9: Always restore initial state
        current = get_current_branch()
        if initial_branch and current != initial_branch:
            logger.info(f"Returning to initial branch: {initial_branch}")
            run_cmd(["git", "checkout", initial_branch], check=False)

        if stash_created:
            logger.info("Restoring previously stashed uncommitted changes...")
            code, pop_out, pop_err = run_cmd(["git", "stash", "pop"], check=False)
            if code != 0:
                logger.warning(
                    f"Could not automatically pop stash cleanly. Your changes remain saved in git stash. Err: {pop_err}"
                )


def run_daemon(interval_seconds: int = 3600) -> None:
    """Run update loop periodically in the background."""
    logger.info(f"Starting SupoClip Auto-Updater daemon (interval: {interval_seconds}s)...")
    while True:
        try:
            perform_update()
        except Exception as exc:
            logger.error(f"Unexpected error during update cycle: {exc}", exc_info=True)
            save_status("ERROR", f"Exception during cycle: {exc}")

        logger.info(f"Sleeping for {interval_seconds} seconds until next check...")
        time.sleep(interval_seconds)


def print_status() -> None:
    """Display current updater status to console."""
    if not STATUS_FILE.exists():
        print("No auto-updater status available. Has the updater run yet?")
        return

    try:
        with open(STATUS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        print("=" * 50)
        print("SupoClip Auto-Updater Status")
        print("=" * 50)
        print(f"Status:          {data.get('status')}")
        print(f"Last Sync:       {data.get('timestamp')}")
        print(f"Upstream SHA:    {data.get('upstream_commit', '')[:9]}")
        print(f"Feature SHA:     {data.get('feature_commit', '')[:9]}")
        print(f"Message:         {data.get('message')}")
        print("=" * 50)
    except Exception as e:
        print(f"Error reading status: {e}")


def main() -> None:
    parser = argparse.ArgumentParser(description="SupoClip Auto-Updater")
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Run continuously as a background daemon",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=3600,
        help="Sync interval in seconds for daemon mode (default: 3600s / 1 hour)",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Print latest updater status",
    )
    args = parser.parse_args()

    if args.status:
        print_status()
        return

    if args.daemon:
        run_daemon(args.interval)
    else:
        perform_update()


if __name__ == "__main__":
    main()
