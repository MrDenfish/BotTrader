#!/usr/bin/env python3
"""
Git version tracking for BotTrader containers.

Provides version information to verify deployed code matches repository state.
Used by all services (webhook, sighook, report) for deployment verification.
"""
import subprocess
import os
from datetime import datetime
from typing import Optional


def get_git_info() -> dict:
    """
    Get git repository information for version tracking.

    Returns:
        dict: Version information including commit, branch, date, and status
    """
    try:
        # Get current commit hash (short)
        commit = subprocess.check_output(
            ['git', 'rev-parse', '--short=8', 'HEAD'],
            cwd='/app',
            stderr=subprocess.DEVNULL
        ).decode().strip()

        # Get current branch name
        branch = subprocess.check_output(
            ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
            cwd='/app',
            stderr=subprocess.DEVNULL
        ).decode().strip()

        # Get commit date
        commit_date = subprocess.check_output(
            ['git', 'show', '-s', '--format=%ci', 'HEAD'],
            cwd='/app',
            stderr=subprocess.DEVNULL
        ).decode().strip()

        # Check if working directory is clean
        try:
            status = subprocess.check_output(
                ['git', 'status', '--porcelain'],
                cwd='/app',
                stderr=subprocess.DEVNULL
            ).decode().strip()
            is_clean = len(status) == 0
        except:
            is_clean = None

        return {
            'commit': commit,
            'branch': branch,
            'commit_date': commit_date,
            'is_clean': is_clean,
            'version_string': f"{branch}@{commit}",
            'full_version': f"{branch}@{commit} ({commit_date})"
        }
    except Exception as e:
        return {
            'commit': 'unknown',
            'branch': 'unknown',
            'commit_date': 'unknown',
            'is_clean': None,
            'version_string': 'unknown',
            'full_version': f'unknown (error: {str(e)})',
            'error': str(e)
        }


def get_container_start_time() -> str:
    """Get the container start time (useful for deployment tracking)."""
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S %Z')


def format_version_banner(service_name: str, additional_info: Optional[dict] = None) -> str:
    """
    Create a formatted version banner for startup logs.

    Args:
        service_name: Name of the service (e.g., "webhook", "sighook", "report")
        additional_info: Optional dict of additional info to display

    Returns:
        str: Formatted multi-line version banner
    """
    git_info = get_git_info()
    start_time = get_container_start_time()

    lines = [
        "=" * 80,
        f"🚀 {service_name.upper()} SERVICE STARTING",
        "=" * 80,
        f"Version:      {git_info['version_string']}",
        f"Branch:       {git_info['branch']}",
        f"Commit:       {git_info['commit']}",
        f"Commit Date:  {git_info['commit_date']}",
        f"Working Dir:  {'clean' if git_info['is_clean'] else 'modified' if git_info['is_clean'] is False else 'unknown'}",
        f"Started:      {start_time}",
    ]

    if additional_info:
        lines.append("-" * 80)
        for key, value in additional_info.items():
            lines.append(f"{key:12}  {value}")

    lines.append("=" * 80)

    return "\n".join(lines)


# Module-level constants for easy import
try:
    _git_info = get_git_info()
    VERSION = _git_info['version_string']
    COMMIT = _git_info['commit']
    BRANCH = _git_info['branch']
    COMMIT_DATE = _git_info['commit_date']
    FULL_VERSION = _git_info['full_version']
except:
    VERSION = 'unknown'
    COMMIT = 'unknown'
    BRANCH = 'unknown'
    COMMIT_DATE = 'unknown'
    FULL_VERSION = 'unknown'


if __name__ == '__main__':
    # Test the version module
    print(format_version_banner("test-service", {
        "Environment": os.getenv('ENV', 'unknown'),
        "Python": f"{os.sys.version_info.major}.{os.sys.version_info.minor}.{os.sys.version_info.micro}"
    }))
