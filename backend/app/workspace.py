"""Central file-access policy for Sammy's tools.

The user controls, in Settings → Security, which folders Sammy may touch (Desktop / Documents /
Downloads toggles) and a default output folder for documents Sammy creates. Tools (Numbers, Excel,
filesystem) read this so access is consistent and user-controlled in one place.
"""
from pathlib import Path
from typing import List, Optional

from . import db
from .config import APP_ROOT


def _home() -> Path:
    return Path.home()


def default_output_dir() -> Optional[Path]:
    """The folder where Sammy saves documents it creates (unless the user names another)."""
    raw = str(db.get_settings().get("files_output_dir") or "").strip()
    if not raw:
        return None
    try:
        return Path(raw).expanduser().resolve()
    except Exception:
        return None


def allowed_roots() -> List[Path]:
    """Directories Sammy's file tools may read/write, based on the user's Settings toggles.

    Always includes the Sammy repo (APP_ROOT) and the chosen output folder; Desktop/Documents/
    Downloads are added only when the user has toggled them on.
    """
    settings = db.get_settings()
    roots: List[Path] = [APP_ROOT]
    home = _home()
    if settings.get("files_allow_desktop"):
        roots.append(home / "Desktop")
    if settings.get("files_allow_documents"):
        roots.append(home / "Documents")
    if settings.get("files_allow_downloads"):
        roots.append(home / "Downloads")
    out = default_output_dir()
    if out:
        roots.append(out)

    resolved: List[Path] = []
    for root in roots:
        try:
            candidate = root.expanduser().resolve()
        except Exception:
            continue
        if candidate not in resolved:
            resolved.append(candidate)
    return resolved


def relative_base() -> Path:
    """Where a bare filename (no folder) should be saved: the output folder, else the repo."""
    return default_output_dir() or APP_ROOT.resolve()
