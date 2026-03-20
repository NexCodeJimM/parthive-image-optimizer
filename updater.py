"""
GitHub Releases-based update flow for frozen (PyInstaller) builds.

Dev/source runs skip the check unless PARTHIVE_FORCE_UPDATE_CHECK=1.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog, QWidget

from version import ASSET_NAME_MACOS_ZIP, ASSET_NAME_WINDOWS, GITHUB_OWNER_REPO, __version__

USER_AGENT = f"PartHiveImageOptimizer/{__version__} (update-check)"


def _repo_configured() -> bool:
    r = GITHUB_OWNER_REPO.strip()
    if not r or r == "YOUR_GITHUB_USERNAME/parthive-image-optimizer":
        return False
    if "/" not in r:
        return False
    return True


def should_run_auto_update_check() -> bool:
    if os.environ.get("PARTHIVE_FORCE_UPDATE_CHECK", "").lower() in ("1", "true", "yes"):
        return True
    return bool(getattr(sys, "frozen", False))


def _parse_version_tuple(s: str) -> tuple[int, ...]:
    s = re.sub(r"^v\s*", "", s.strip(), flags=re.I)
    parts = re.findall(r"\d+", s)
    if not parts:
        return (0,)
    return tuple(int(p) for p in parts)


def _is_remote_newer(remote_tag: str, current: str) -> bool:
    return _parse_version_tuple(remote_tag) > _parse_version_tuple(current)


def _github_latest_release(repo: str) -> dict:
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _pick_asset_url(release: dict, name: str) -> str | None:
    for a in release.get("assets") or []:
        if a.get("name") == name:
            return a.get("browser_download_url")
    return None


def _running_app_bundle() -> Path | None:
    """Path to *.app when frozen on macOS, else None."""
    if sys.platform != "darwin" or not getattr(sys, "frozen", False):
        return None
    p = Path(sys.executable).resolve()
    for i, part in enumerate(p.parts):
        if part.endswith(".app"):
            return Path(*p.parts[: i + 1])
    return None


class _ReleaseFetchThread(QThread):
    succeeded = Signal(dict)  # tag_name, html_url, asset_url, asset_name
    failed = Signal(str)

    def __init__(self, repo: str, parent: QObject | None = None):
        super().__init__(parent)
        self._repo = repo

    def run(self) -> None:
        try:
            data = _github_latest_release(self._repo)
            tag = (data.get("tag_name") or "").strip()
            html_url = (data.get("html_url") or "").strip()
            if sys.platform.startswith("win"):
                name = ASSET_NAME_WINDOWS
            elif sys.platform == "darwin":
                name = ASSET_NAME_MACOS_ZIP
            else:
                self.failed.emit("Automatic updates are only supported on Windows and macOS.")
                return
            asset_url = _pick_asset_url(data, name)
            if not tag or not asset_url:
                self.failed.emit("Release found but the expected download file is missing.")
                return
            self.succeeded.emit(
                {
                    "tag_name": tag,
                    "html_url": html_url,
                    "asset_url": asset_url,
                    "asset_name": name,
                }
            )
        except urllib.error.HTTPError as e:
            if e.code == 404:
                self.failed.emit("No latest release found (404). Check the GitHub repository name.")
            else:
                self.failed.emit(f"GitHub returned an error ({e.code}).")
        except urllib.error.URLError as e:
            self.failed.emit(f"Network error: {e.reason}")
        except Exception as e:
            self.failed.emit(str(e))


class _DownloadThread(QThread):
    progress = Signal(int, int)  # downloaded, total (total may be -1 if unknown)
    succeeded = Signal(str)  # path on disk
    failed = Signal(str)

    def __init__(self, url: str, dest: Path, parent: QObject | None = None):
        super().__init__(parent)
        self._url = url
        self._dest = dest

    def run(self) -> None:
        try:
            req = urllib.request.Request(
                self._url,
                headers={"User-Agent": USER_AGENT},
                method="GET",
            )
            self._dest.parent.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(req, timeout=120) as resp:
                total = int(resp.headers.get("Content-Length") or -1)
                n = 0
                chunk = 256 * 1024
                with open(self._dest, "wb") as f:
                    while True:
                        buf = resp.read(chunk)
                        if not buf:
                            break
                        f.write(buf)
                        n += len(buf)
                        self.progress.emit(n, total)
            self.succeeded.emit(str(self._dest.resolve()))
        except Exception as e:
            self.failed.emit(str(e))


def _apply_update_windows(new_exe_path: Path, parent: QWidget | None) -> None:
    old = Path(sys.executable).resolve()
    if old.suffix.lower() != ".exe":
        QMessageBox.warning(parent, "Update", "Could not detect the running .exe path.")
        return
    batch = Path(tempfile.gettempdir()) / "PartHive_update_helper.bat"
    # Wait briefly so the current process can exit and release the file lock.
    lines = [
        "@echo off",
        f'set "OLD={old}"',
        f'set "NEW={new_exe_path.resolve()}"',
        "ping 127.0.0.1 -n 4 >nul",
        'move /Y "%NEW%" "%OLD%"',
        'start "" "%OLD%"',
        'del "%~f0"',
    ]
    batch.write_text("\r\n".join(lines), encoding="utf-8")
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0)
    try:
        subprocess.Popen(
            [str(batch)],
            close_fds=True,
            creationflags=DETACHED | creationflags,
        )
    except Exception as e:
        QMessageBox.critical(parent, "Update", f"Could not start update helper:\n{e}")
        return
    QMessageBox.information(parent, "Update", "The app will close and restart with the new version.")
    QApplication.quit()
    os._exit(0)


def _find_macos_app_in(staging: Path) -> Path | None:
    for p in staging.rglob("*.app"):
        if p.is_dir():
            return p
    return None


def _apply_update_macos(zip_path: Path, parent: QWidget | None) -> None:
    app_bundle = _running_app_bundle()
    if app_bundle is None:
        QMessageBox.warning(
            parent,
            "Update",
            "Could not locate the running .app bundle. Install from the published .zip and try again.",
        )
        return
    staging = Path(tempfile.mkdtemp(prefix="ph_update_"))
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(staging)
    except Exception as e:
        QMessageBox.critical(parent, "Update", f"Could not extract update archive:\n{e}")
        return
    new_app = _find_macos_app_in(staging)
    if not new_app:
        QMessageBox.critical(parent, "Update", "Update zip did not contain a .app bundle.")
        return
    script = Path(tempfile.gettempdir()) / "PartHive_update_mac.sh"
    script.write_text(
        "#!/bin/bash\n"
        'OLD="$1"\n'
        'NEW="$2"\n'
        'STAGE="$3"\n'
        "sleep 3\n"
        'rm -rf "$OLD"\n'
        'ditto "$NEW" "$OLD"\n'
        'open "$OLD"\n'
        'rm -rf "$STAGE"\n',
        encoding="utf-8",
    )
    os.chmod(script, 0o755)
    try:
        subprocess.Popen(
            ["/bin/bash", str(script), str(app_bundle), str(new_app), str(staging)],
            start_new_session=True,
        )
    except Exception as e:
        QMessageBox.critical(parent, "Update", f"Could not start update script:\n{e}")
        return
    QMessageBox.information(parent, "Update", "The app will quit and reopen with the new version.")
    QApplication.quit()
    os._exit(0)


def _start_download_and_apply(info: dict, parent: QWidget | None) -> None:
    url = info["asset_url"]
    dest = Path(tempfile.gettempdir()) / f"PartHive_update_{info['asset_name']}"

    prog = QProgressDialog("Downloading update…", "", 0, 100, parent)
    prog.setWindowTitle("Update")
    prog.setMinimumDuration(0)
    prog.setValue(0)
    prog.setCancelButton(None)
    prog.show()

    dl = _DownloadThread(url, dest, parent)

    def on_progress(done: int, total: int) -> None:
        if total > 0:
            prog.setMaximum(100)
            prog.setValue(min(99, int(done * 100 / total)))
        else:
            prog.setMaximum(0)

    def on_fail(msg: str) -> None:
        prog.close()
        QMessageBox.critical(parent, "Update failed", msg)
        dl.deleteLater()

    def on_ok(path_str: str) -> None:
        prog.close()
        p = Path(path_str)
        if sys.platform.startswith("win"):
            _apply_update_windows(p, parent)
        elif sys.platform == "darwin":
            _apply_update_macos(p, parent)
        dl.deleteLater()

    dl.progress.connect(on_progress)
    dl.failed.connect(on_fail)
    dl.succeeded.connect(on_ok)
    dl.start()


def schedule_update_check(parent: QWidget | None) -> None:
    if not should_run_auto_update_check():
        return
    if not _repo_configured():
        return

    fetch = _ReleaseFetchThread(GITHUB_OWNER_REPO, parent)

    def on_fail(msg: str) -> None:
        # Silent failure on auto-check (corporate proxy, offline, etc.)
        fetch.deleteLater()

    def on_ok(info: dict) -> None:
        tag = info["tag_name"]
        if not _is_remote_newer(tag, __version__):
            fetch.deleteLater()
            return
        ver_remote = tag.lstrip("vV")
        box = QMessageBox(parent)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle("Update available")
        box.setText(
            f"A newer version is available:\n\n"
            f"Installed: {__version__}\n"
            f"Latest: {ver_remote}\n\n"
            "Download and install now? The app will restart."
        )
        if info.get("html_url"):
            box.setInformativeText(f'Release notes: {info["html_url"]}')
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.Yes)
        res = box.exec()
        fetch.deleteLater()
        if res == QMessageBox.Yes:
            _start_download_and_apply(info, parent)

    fetch.failed.connect(on_fail)
    fetch.succeeded.connect(on_ok)
    parent_ref = parent

    def _keep_alive() -> None:
        # hold reference on parent window
        if parent_ref is not None:
            setattr(parent_ref, "_ph_release_fetch", fetch)

    _keep_alive()
    fetch.start()


def run_manual_update_check(parent: QWidget | None) -> None:
    """Check GitHub for updates; show dialogs for errors, up-to-date, or new version."""
    if not _repo_configured():
        QMessageBox.warning(
            parent,
            "Update checker",
            "Update checks are not configured.\n\n"
            "Set your GitHub owner/repo in version.py (GITHUB_OWNER_REPO) or "
            "the PARTHIVE_UPDATE_REPO environment variable.",
        )
        return

    fetch = _ReleaseFetchThread(GITHUB_OWNER_REPO, parent)

    def on_fail(msg: str) -> None:
        QMessageBox.warning(parent, "Update checker", f"Could not check for updates:\n\n{msg}")
        fetch.deleteLater()

    def on_ok(info: dict) -> None:
        tag = info["tag_name"]
        if not _is_remote_newer(tag, __version__):
            QMessageBox.information(
                parent,
                "Update checker",
                f"You are running the latest release.\n\n"
                f"Installed version: {__version__}\n"
                f"Latest on GitHub: {tag.lstrip('vV')}",
            )
            fetch.deleteLater()
            return
        ver_remote = tag.lstrip("vV")
        box = QMessageBox(parent)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle("Update available")
        box.setText(
            f"A newer version is available:\n\n"
            f"Installed: {__version__}\n"
            f"Latest: {ver_remote}\n\n"
            "Download and install now? The app will restart."
        )
        if info.get("html_url"):
            box.setInformativeText(f'Release notes: {info["html_url"]}')
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.Yes)
        res = box.exec()
        fetch.deleteLater()
        if res == QMessageBox.Yes:
            _start_download_and_apply(info, parent)

    fetch.failed.connect(on_fail)
    fetch.succeeded.connect(on_ok)

    if parent is not None:
        setattr(parent, "_ph_manual_release_fetch", fetch)

    fetch.start()
