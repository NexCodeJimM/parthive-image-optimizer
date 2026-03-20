"""
Application version and update source.

- Bump __version__ to match each GitHub release tag (e.g. tag v1.2.0 → __version__ = "1.2.0").
- Set GITHUB_OWNER_REPO to "owner/repo" for your public GitHub repository.
  You can also set the environment variable PARTHIVE_UPDATE_REPO instead.
"""

from __future__ import annotations

import os

__version__ = "1.1.0"

# Default repo for update checks (must be a *public* repo unless you add a GitHub token).
_default_repo = "NexCodeJimM/parthive-image-optimizer"

GITHUB_OWNER_REPO = (
    os.environ.get("PARTHIVE_UPDATE_REPO", "").strip()
    or _default_repo
).strip()

# Asset names must match .github/workflows/release.yml
ASSET_NAME_WINDOWS = "PartHive-Image-Optimizer-Windows.exe"
ASSET_NAME_MACOS_ZIP = "PartHive-Image-Optimizer-macOS.zip"
