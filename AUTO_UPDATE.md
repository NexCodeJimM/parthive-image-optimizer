# Automatic updates (GitHub Releases)

The packaged app checks **public** GitHub Releases a few seconds after startup (PyInstaller / frozen builds only).

## Setup

1. In **`version.py`** set:
   - `__version__` — must match the version you ship (e.g. `1.2.0`).
   - `GITHUB_OWNER_REPO` default or environment variable `PARTHIVE_UPDATE_REPO` to `your-username/your-repo`.

2. Create GitHub releases with tags like **`v1.2.0`** (the updater compares numeric parts with `__version__`).

3. Keep release asset names aligned with **`version.py`**:
   - `PartHive-Image-Optimizer-Windows.exe`
   - `PartHive-Image-Optimizer-macOS.zip`  
   (Same as `.github/workflows/release.yml`.)

## Behavior

- If a newer tag exists, the user gets a dialog with a link to release notes and can choose to download.
- **Windows:** downloads the new `.exe`, then a small helper batch file replaces the running exe after exit and restarts.
- **macOS:** downloads the zip, extracts it, then a shell script replaces the running `.app` after quit and reopens it.

## Development

- Running from source **skips** the check unless you set `PARTHIVE_FORCE_UPDATE_CHECK=1`.
- Private repos require API authentication (not implemented); use a **public** repo or extend `updater.py`.

## Limitations

- Installing under `Program Files` (Windows) or locked folders may require running the app from a user-writable location.
- Replacing a signed macOS app may trigger Gatekeeper; users may need to allow the updated app once.
