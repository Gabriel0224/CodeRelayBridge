"""Source/frozen resources remain independent of module subdirectories."""
from pathlib import Path
import sys

def resource_root():
    return Path(sys._MEIPASS) if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent

def web_root():
    return resource_root() / 'web'

def upstreams_file():
    base = resource_root()
    return base / 'upstreams.json' if getattr(sys, 'frozen', False) else base / 'crb/updates/upstreams.json'
