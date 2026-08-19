"""Bounded verification of a model written by the Revit ``SaveAs`` endpoint.

The local plugin receives a destination directory rather than a final filename.
Different plugin releases therefore preserve the source extension differently.
Never construct a model path from the source name after a save: report only a
file that changed in the requested destination directory.
"""

from __future__ import annotations

from pathlib import Path


FileFingerprint = tuple[int, int]


def snapshot_folder_files(folder: Path) -> dict[Path, FileFingerprint]:
    """Return direct regular-file fingerprints without traversing subfolders."""
    if not folder.is_dir():
        return {}
    snapshot: dict[Path, FileFingerprint] = {}
    for path in folder.iterdir():
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        snapshot[path] = (stat.st_size, stat.st_mtime_ns)
    return snapshot


def saved_model_name_collision(folder: Path, source_model_path: str | None) -> bool:
    """Detect both normal and legacy extensionless SaveAs output names."""
    if not source_model_path:
        return False
    source = Path(source_model_path)
    names = {source.name, source.stem}
    return any(name and (folder / name).exists() for name in names)


def resolve_fresh_saved_model_path(
    folder: Path,
    source_model_path: str | None,
    before: dict[Path, FileFingerprint],
) -> Path | None:
    """Return one uniquely preferred file changed by the just-completed SaveAs.

    Preference is deliberately narrow: exact original name, then the same
    name without its extension (a legacy plugin behaviour).  An unrelated new
    RVT is not enough evidence to claim that this SaveAs produced it.
    """
    after = snapshot_folder_files(folder)
    fresh = [path for path, fingerprint in after.items() if before.get(path) != fingerprint]
    if not fresh:
        return None

    source = Path(source_model_path) if source_model_path else None
    source_name = source.name.casefold() if source else ""
    source_stem = source.stem.casefold() if source else ""

    def rank(path: Path) -> tuple[int, str] | None:
        name = path.name.casefold()
        if source_name and name == source_name:
            return (0, name)
        if source_stem and name == source_stem:
            return (1, name)
        # A save can generate other fresh files (audit/export sidecars), and a
        # user or synchronisation service can create unrelated RVT files in
        # the same folder.  Neither is reliable evidence of this SaveAs.
        return None

    eligible = [path for path in fresh if rank(path) is not None]
    if not eligible:
        return None
    ordered = sorted(eligible, key=lambda path: rank(path) or (99, path.name.casefold()))
    best_rank = rank(ordered[0])[0]
    best = [path for path in ordered if rank(path)[0] == best_rank]
    return best[0] if len(best) == 1 else None
