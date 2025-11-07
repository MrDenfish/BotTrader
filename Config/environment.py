"""
Environment detection and .env file loading.

Uses a unified .env file for both desktop and Docker environments.
Runtime detection handles environment-specific configuration.
"""
import os
from pathlib import Path
from typing import Optional


class Environment:
    """Detect and configure environment."""

    def __init__(self):
        self.is_docker = self._detect_docker()
        self.env_name = "prod" if self.is_docker else "dev"
        self.env_file = self._find_env_file()
        self._loaded = False

    def _detect_docker(self) -> bool:
        """Detect if running in Docker container."""
        # Multiple detection methods for robustness
        if os.path.exists('/.dockerenv'):
            return True
        if os.getenv('IN_DOCKER', '').lower() == 'true':
            return True
        if os.getenv('IS_DOCKER', '').lower() == 'true':
            return True
        try:
            with open('/proc/1/cgroup', 'r') as f:
                return 'docker' in f.read()
        except Exception:
            pass
        return False

    def _find_env_file(self) -> Optional[Path]:
        """Find the unified .env file for current environment."""
        if self.is_docker:
            # Docker: check container path
            env_path = Path('/app/.env')
        else:
            # Desktop: check project root
            # Go up from Config/ to project root
            project_root = Path(__file__).parents[1]
            env_path = project_root / '.env'

        return env_path if env_path.exists() else None

    def load(self, force_reload: bool = False) -> None:
        """
        Load environment variables from file.

        Args:
            force_reload: If True, reload even if already loaded
        """
        if self._loaded and not force_reload:
            return

        if not self.env_file:
            print(f"[WARNING] No .env file found for {self.env_name} environment")
            print(f"[WARNING] Continuing with existing environment variables")
            self._loaded = True
            return

        try:
            from dotenv import load_dotenv
            load_dotenv(self.env_file, override=False)
            print(f"[INFO] Loaded {self.env_name} environment from {self.env_file}")
            self._loaded = True
        except ImportError:
            print("[WARNING] python-dotenv not installed, skipping .env load")
            print("[WARNING] Install with: pip install python-dotenv")
            self._loaded = True
        except Exception as e:
            print(f"[ERROR] Failed to load {self.env_file}: {e}")
            self._loaded = True

    # ========================================================================
    # Environment-Specific Helpers
    # ========================================================================

    @property
    def db_host(self) -> str:
        """Get database host for current environment."""
        if self.is_docker:
            return os.getenv('DB_HOST', 'db')
        else:
            return os.getenv('DB_HOST', '127.0.0.1')

    @property
    def db_port(self) -> int:
        """Get database port."""
        return int(os.getenv('DB_PORT', '5432'))

    @property
    def log_dir(self) -> Path:
        """Get log directory for current environment."""
        if self.is_docker:
            return Path('/app/logs')
        else:
            # Desktop: use BOTTRADER_CACHE_DIR or sensible default
            base = os.getenv('BOTTRADER_CACHE_DIR')
            if base:
                return Path(base)
            # Default to user home
            return Path.home() / 'Python_Projects' / 'BotTrader' / '.bottrader' / 'cache'

    @property
    def score_jsonl_path(self) -> Path:
        """Get score JSONL path for current environment."""
        env_path = os.getenv('SCORE_JSONL_PATH')
        if env_path:
            return Path(env_path)
        return self.log_dir / 'scores.jsonl'

    @property
    def tp_sl_log_path(self) -> Path:
        """Get TP/SL log path for current environment."""
        env_path = os.getenv('TP_SL_LOG_PATH')
        if env_path:
            return Path(env_path)
        return self.log_dir / 'tpsl.jsonl'

    def __repr__(self) -> str:
        return f"Environment(env={self.env_name}, docker={self.is_docker}, file={self.env_file})"


# ============================================================================
# Global Instance - Auto-load on import
# ============================================================================

env = Environment()
env.load()

# Export for convenience
is_docker = env.is_docker
env_name = env.env_name