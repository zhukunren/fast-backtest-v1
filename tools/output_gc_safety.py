"""Filesystem safety primitives shared by output garbage-collection flows."""
from __future__ import annotations

import os
from pathlib import Path


PIN_NAME = "PIN"
ACTIVE_NAME = "ACTIVE"
PARTIAL_SUFFIXES = (".incomplete", ".partial", ".tmp")


def is_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction()) if callable(is_junction) else False


def path_size(path: Path) -> int:
    if is_link(path):
        return 0
    if path.is_file():
        try:
            return int(path.stat().st_size)
        except OSError:
            return 0
    if not path.is_dir():
        return 0
    total = 0
    for current, directories, files in os.walk(path, topdown=True, followlinks=False):
        directories[:] = sorted(
            [name for name in directories if not is_link(Path(current) / name)],
            key=str.casefold,
        )
        for name in sorted(files, key=str.casefold):
            child = Path(current) / name
            if is_link(child):
                continue
            try:
                total += int(child.stat().st_size)
            except OSError:
                continue
    return total


def modification_time(path: Path) -> float:
    try:
        return float(path.stat().st_mtime)
    except OSError:
        return 0.0


def is_inside(path: Path, root: Path) -> bool:
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
    except ValueError:
        return False
    return Path(path).resolve() != Path(root).resolve()


def has_marker(path: Path, marker: str) -> bool:
    return (path / marker).exists()


def has_pinned_ancestor(path: Path, *, stop: Path) -> bool:
    current = Path(path).resolve()
    boundary = Path(stop).resolve()
    while True:
        if (current / PIN_NAME).exists():
            return True
        if current == boundary:
            return False
        try:
            current = current.parent
            current.relative_to(boundary)
        except ValueError:
            return True
