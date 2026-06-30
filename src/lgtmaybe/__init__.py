"""lgtmaybe — provider-agnostic PR reviewer."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("lgtmaybe")
except PackageNotFoundError:  # not installed (e.g. running from a raw checkout)
    __version__ = "0.9.0"
