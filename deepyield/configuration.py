"""Public defaults and explicitly selected, local-only configuration files."""
import os
from pathlib import Path

DEFAULTS = Path(__file__).resolve().parents[1] / "config" / "defaults"
PRIVATE_CONFIG_ENV = "ATEATIME_PRIVATE_CONFIG"


def configuration_directory(directory=None):
    selected = directory if directory is not None else os.environ.get(PRIVATE_CONFIG_ENV)
    path = Path(selected).expanduser().resolve() if selected else DEFAULTS
    if not path.is_dir():
        raise ValueError(f"Configuration directory does not exist: {path}")
    return path


def configuration_file(name, directory=None):
    """A missing optional file uses its public default; invalid files still fail."""
    path = configuration_directory(directory) / name
    return path if path.exists() else DEFAULTS / name
