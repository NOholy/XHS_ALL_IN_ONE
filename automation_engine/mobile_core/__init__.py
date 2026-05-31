# Mobile Core Package
import os
import shutil

def _ensure_adb_in_path():
    """Ensure that the 'adb' executable is available in the system PATH."""
    if shutil.which("adb"):
        return
    
    # Common Android SDK paths on macOS, Windows, and Linux
    home = os.path.expanduser("~")
    common_paths = [
        os.path.join(home, "Library/Android/sdk/platform-tools"),
        os.path.join(home, "AppData/Local/Android/Sdk/platform-tools"),
        "/usr/local/bin",
        "/opt/homebrew/bin",
    ]
    
    for path in common_paths:
        if os.path.isdir(path) and os.path.isfile(os.path.join(path, "adb")):
            os.environ["PATH"] = path + os.path.pathsep + os.environ.get("PATH", "")
            return

_ensure_adb_in_path()
