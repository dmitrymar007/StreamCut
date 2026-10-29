"""Writable user data lives outside the installed application directory."""
import os
from pathlib import Path


def data_root():
    override = os.environ.get("STREAMCUT_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return Path(os.environ.get("LOCALAPPDATA", Path.home()/".local"/"share"))/"StreamCut"


def cuda_folder():
    return data_root()/"runtime"/"cuda12.9-cudnn9.10"
