# Config/health_check.py
"""
Startup health check for BotTrader.

Validates:
1. Configuration values (via validators.py)
2. Environment variables are set
3. Database connectivity
4. File paths exist and are writable

Usage:
    # At application startup
    from Config.health_check import run_health_check
    run_health_check()  # Raises on critical failures

    # Or get detailed report
    report = run_health_check(raise_on_error=False)
    print(report.format())
"""

from __future__ import annotations
import os
import sys
from pathlib import Path
from typing import List, Tuple, Optional
from dataclasses import dataclass, field

from .validators import validate_all_config, ValidationResult
from .exceptions import ConfigError


@dataclass
class HealthCheckResult:
    """Result of full health check."""

    config_validation: ValidationResult
    env_checks: List[Tuple[str, str, bool]] = field(default_factory=list)  # (key, status, is_ok)
    path_checks: List[Tuple[str, str, bool]] = field(default_factory=list)  # (path, status, is_ok)
    db_check: Optional[Tuple[bool, str]] = None  # (success, message)

    @property
    def is_healthy(self) -> bool:
        """True if all critical checks passed."""
        # Config must be valid
        if not self.config_validation.is_valid:
            return False

        # All env checks must be OK
        if any(not ok for _, _, ok in self.env_checks):
            return False

        # Critical paths must be OK
        critical_paths = {"BOTTRADER_CACHE_DIR", "SCORE_JSONL_PATH"}
        for path, status, ok in self.path_checks:
            if any(cp in path for cp in critical_paths) and not ok:
                return False

        # DB connection is critical
        if self.db_check and not self.db_check[0]:
            return False

        return True

    def format(self, verbose: bool = True) -> str:
        """Format health check as human-readable report."""
        lines = []
        lines.append("=" * 70)
        lines.append("BOTTRADER HEALTH CHECK")
        lines.append("=" * 70)
        lines.append("")

        # Overall status
        if self.is_healthy:
            lines.append("✅ OVERALL STATUS: HEALTHY")
        else:
            lines.append("❌ OVERALL STATUS: UNHEALTHY - See details below")
        lines.append("")

        # Config validation
        lines.append("-" * 70)
        lines.append("1. CONFIGURATION VALIDATION")
        lines.append("-" * 70)
        if self.config_validation.is_valid:
            lines.append("✅ All config values valid")
            if verbose and self.config_validation.warnings:
                lines.append(f"   ({len(self.config_validation.warnings)} warnings - see below)")
        else:
            lines.append(f"❌ {len(self.config_validation.errors)} config errors found:")
            for key, msg in self.config_validation.errors:
                lines.append(f"   • {key}: {msg}")

        if verbose and self.config_validation.warnings:
            lines.append("\n⚠️  Config warnings:")
            for key, msg in self.config_validation.warnings:
                lines.append(f"   • {key}: {msg}")
        lines.append("")

        # Environment variables
        lines.append("-" * 70)
        lines.append("2. ENVIRONMENT VARIABLES")
        lines.append("-" * 70)
        if not self.env_checks:
            lines.append("⚠️  No env checks performed")
        else:
            ok_count = sum(1 for _, _, ok in self.env_checks if ok)
            lines.append(f"Checked {len(self.env_checks)} variables: {ok_count} OK, {len(self.env_checks) - ok_count} missing")
            if verbose or ok_count < len(self.env_checks):
                for key, status, ok in self.env_checks:
                    icon = "✅" if ok else "❌"
                    lines.append(f"   {icon} {key}: {status}")
        lines.append("")

        # File paths
        lines.append("-" * 70)
        lines.append("3. FILE PATHS")
        lines.append("-" * 70)
        if not self.path_checks:
            lines.append("⚠️  No path checks performed")
        else:
            ok_count = sum(1 for _, _, ok in self.path_checks if ok)
            lines.append(f"Checked {len(self.path_checks)} paths: {ok_count} OK, {len(self.path_checks) - ok_count} issues")
            if verbose or ok_count < len(self.path_checks):
                for path, status, ok in self.path_checks:
                    icon = "✅" if ok else "⚠️"
                    lines.append(f"   {icon} {path}")
                    if verbose or not ok:
                        lines.append(f"      └─ {status}")
        lines.append("")

        # Database
        lines.append("-" * 70)
        lines.append("4. DATABASE CONNECTIVITY")
        lines.append("-" * 70)
        if self.db_check is None:
            lines.append("⚠️  Database check not performed")
        else:
            ok, msg = self.db_check
            icon = "✅" if ok else "❌"
            lines.append(f"{icon} {msg}")
        lines.append("")

        lines.append("=" * 70)
        if self.is_healthy:
            lines.append("✅ ALL CRITICAL CHECKS PASSED - System ready")
        else:
            lines.append("❌ CRITICAL FAILURES DETECTED - Fix issues above before running")
        lines.append("=" * 70)

        return "\n".join(lines)


def check_required_env_vars() -> List[Tuple[str, str, bool]]:
    """
    Check required environment variables are set.

    Returns:
        List of (key, status, is_ok) tuples
    """
    checks = []

    # Critical env vars (required)
    required = {
        "IN_DOCKER": "Environment type (true/false)",
        "DB_HOST": "Database host",
        "DB_NAME": "Database name",
        "DB_USER": "Database user",
        "DB_PASSWORD": "Database password",
    }

    for key, desc in required.items():
        value = os.getenv(key)
        if value:
            # Don't expose sensitive values in health check
            if "PASSWORD" in key or "SECRET" in key or "KEY" in key:
                display = "***" if value else "(not set)"
            else:
                display = value[:50] + ("..." if len(value) > 50 else "")
            checks.append((key, f"{desc}: {display}", True))
        else:
            checks.append((key, f"{desc}: NOT SET", False))

    # Optional but recommended
    optional = {
        "REPORT_SENDER": "Email sender for reports",
        "REPORT_RECIPIENTS": "Email recipients",
        "AWS_REGION": "AWS region for SES",
    }

    for key, desc in optional.items():
        value = os.getenv(key)
        if value:
            display = value[:50] + ("..." if len(value) > 50 else "")
            checks.append((key, f"{desc}: {display}", True))
        else:
            checks.append((key, f"{desc}: not set (optional)", True))  # Not critical

    return checks


def check_file_paths() -> List[Tuple[str, str, bool]]:
    """
    Check important file paths exist and are accessible.

    Returns:
        List of (path_description, status, is_ok) tuples
    """
    checks = []

    # Cache directory
    cache_dir = os.getenv("BOTTRADER_CACHE_DIR")
    if cache_dir:
        path = Path(cache_dir)
        if path.exists():
            if path.is_dir():
                if os.access(path, os.W_OK):
                    checks.append((f"Cache dir: {cache_dir}", "exists and writable", True))
                else:
                    checks.append((f"Cache dir: {cache_dir}", "exists but not writable", False))
            else:
                checks.append((f"Cache dir: {cache_dir}", "exists but is not a directory", False))
        else:
            checks.append((f"Cache dir: {cache_dir}", "does not exist (will be created on first use)", True))
    else:
        checks.append(("Cache dir", "BOTTRADER_CACHE_DIR not set", False))

    # Score log path
    score_path = os.getenv("SCORE_JSONL_PATH", "/app/logs/score_log.jsonl")
    path = Path(score_path)
    parent = path.parent
    if parent.exists():
        if os.access(parent, os.W_OK):
            checks.append((f"Score log dir: {parent}", "writable", True))
        else:
            checks.append((f"Score log dir: {parent}", "exists but not writable", False))
    else:
        checks.append((f"Score log dir: {parent}", "does not exist (will be created)", True))

    # TP/SL log path
    tpsl_path = os.getenv("TP_SL_LOG_PATH", "/app/logs/tpsl.jsonl")
    path = Path(tpsl_path)
    parent = path.parent
    if parent.exists():
        if os.access(parent, os.W_OK):
            checks.append((f"TP/SL log dir: {parent}", "writable", True))
        else:
            checks.append((f"TP/SL log dir: {parent}", "exists but not writable", False))
    else:
        checks.append((f"TP/SL log dir: {parent}", "does not exist (will be created)", True))

    return checks


def check_database_connection(timeout_seconds: float = 5.0) -> Tuple[bool, str]:
    """
    Test database connectivity.

    Returns:
        (success, message) tuple
    """
    try:
        # Try to import database module
        try:
            from ..botreport.db import get_sa_engine
        except ImportError:
            try:
                from botreport.db import get_sa_engine
            except ImportError:
                return (False, "Cannot import database module")

        # Try to connect
        engine = get_sa_engine()
        with engine.connect() as conn:
            # Simple query to verify connection
            result = conn.execute("SELECT 1 AS test").fetchone()
            if result and result[0] == 1:
                return (True, f"Connected to {engine.url.host}")
            else:
                return (False, "Connection succeeded but test query failed")

    except Exception as e:
        return (False, f"Connection failed: {type(e).__name__}: {str(e)[:100]}")


def run_health_check(
        check_db: bool = True,
        raise_on_error: bool = True,
        verbose: bool = True,
) -> HealthCheckResult:
    """
    Run comprehensive health check.

    Args:
        check_db: If True, test database connectivity
        raise_on_error: If True, raise ConfigError if unhealthy
        verbose: If True, print detailed report

    Returns:
        HealthCheckResult

    Raises:
        ConfigError: If unhealthy and raise_on_error=True
    """
    # 1. Validate configuration
    config_result = validate_all_config(raise_on_error=False, verbose=False)

    # 2. Check environment variables
    env_checks = check_required_env_vars()

    # 3. Check file paths
    path_checks = check_file_paths()

    # 4. Check database (optional)
    db_check = None
    if check_db:
        db_check = check_database_connection()

    # Build result
    result = HealthCheckResult(
        config_validation=config_result,
        env_checks=env_checks,
        path_checks=path_checks,
        db_check=db_check,
    )

    # Print or raise
    if verbose or not result.is_healthy:
        print(result.format(verbose=verbose))

    if not result.is_healthy and raise_on_error:
        raise ConfigError("Health check failed - see report above")

    return result


# ============================================================================
# CLI
# ============================================================================

if __name__ == "__main__":
    """Run health check from command line: python -m Config.health_check"""
    result = run_health_check(check_db=True, raise_on_error=False, verbose=True)
    sys.exit(0 if result.is_healthy else 1)