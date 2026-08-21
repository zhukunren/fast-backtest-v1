from __future__ import annotations

import logging
from pathlib import Path


def close_file_handlers_under(root: Path) -> tuple[Path, ...]:
    """Close and detach file handlers whose output belongs to root."""
    root_resolved = root.resolve(strict=True)
    named_loggers = [
        logger
        for _, logger in sorted(
            logging.root.manager.loggerDict.items(),
            key=lambda item: item[0].casefold(),
        )
        if isinstance(logger, logging.Logger)
    ]
    loggers = [logging.getLogger(), *named_loggers]
    matched: dict[int, tuple[logging.Handler, Path, list[logging.Logger]]] = {}

    def register(handler: logging.Handler, owner: logging.Logger | None = None) -> None:
        filename = getattr(handler, "baseFilename", None)
        if not filename:
            return
        try:
            path = Path(filename).resolve(strict=False)
            path.relative_to(root_resolved)
        except (OSError, ValueError):
            return
        record = matched.setdefault(id(handler), (handler, path, []))
        if owner is not None:
            record[2].append(owner)

    for logger in loggers:
        for handler in logger.handlers:
            register(handler, logger)

    # A fixture can detach a FileHandler without closing it. Logging retains a
    # weak reference to that live handler even though no logger owns it anymore.
    for handler_ref in tuple(getattr(logging, "_handlerList", ())):
        handler = handler_ref()
        if isinstance(handler, logging.Handler):
            register(handler)

    closed: list[Path] = []
    for handler, path, owners in matched.values():
        for logger in owners:
            logger.removeHandler(handler)
        handler.close()
        closed.append(path)
    return tuple(sorted(closed, key=lambda path: str(path).casefold()))
