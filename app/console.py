from __future__ import annotations

import contextvars
import sys
from dataclasses import dataclass, field
from typing import Callable

from core.runtime_flags import get_tag_log_path


@dataclass
class WarningCollector:
    warn_messages: list[tuple[str, str]] = field(default_factory=list)
    logger: object | None = None

    def _log(self, logger, tag: str, msg: str) -> None:
        level = "warning" if tag in ("[WARN]", "[CALENDAR_GAP]") else "info"
        try:
            getattr(logger, level)("print_tag %s", msg)
        except AttributeError:
            try:
                sys.stderr.write(f"[WARN] logger missing level={level}: {msg}\n")
            except Exception:
                pass
        except Exception as e:
            try:
                sys.stderr.write(f"[WARN] logger failed level={level} err={e}: {msg}\n")
            except Exception:
                pass

    def add(self, msg: str, *, tag: str = "") -> None:
        self.warn_messages.append((tag, msg))
        if self.logger is not None:
            self._log(self.logger, tag, msg)

    def set_logger(self, logger) -> None:
        self.logger = logger
        for tag, msg in self.warn_messages:
            self._log(logger, tag, msg)

    def emit_summary(self, printer: Callable[[str], None]) -> None:
        if self.logger is not None:
            for handler in getattr(self.logger, "handlers", []):
                try:
                    handler.flush()
                except Exception as e:
                    try:
                        sys.stderr.write(f"[WARN] logger handler flush failed: {e}\n")
                    except Exception:
                        pass
        total = len(self.warn_messages)
        log_path = get_tag_log_path()
        if log_path:
            try:
                log_total = 0
                with open(log_path, "r", encoding="utf-8") as f:
                    for line in f:
                        if " print_tag " in line:
                            log_total += 1
                total = log_total
            except Exception as e:
                try:
                    sys.stderr.write(f"[WARN] read tag log failed: {e}\n")
                except Exception:
                    pass
        if total <= 0:
            return
        printer(f"TAG汇总：共 {total} 条（详情见日志）")


class PrintHook:
    """
    Hook stdout to aggregate tagged lines into WarningCollector.
    """

    def __init__(self, *, suppress_info: bool, warn_collector: WarningCollector) -> None:
        self.suppress_info = suppress_info
        self.warn_collector = warn_collector
        self._orig_stdout = None
        self._stdout_wrapper = None
        self._token = None
        self._raw_print = None

    @staticmethod
    def _extract_tag(text: str) -> str:
        s = text.lstrip()
        if not s.startswith("["):
            return ""
        end = s.find("]")
        if end <= 1:
            return ""
        return s[: end + 1]

    def install(self):
        if self._orig_stdout is not None:
            return self._raw_print

        self._orig_stdout = sys.stdout
        self._stdout_wrapper = _TaggedStdout(
            stream=self._orig_stdout,
            hook=self,
        )
        sys.stdout = self._stdout_wrapper
        self._token = _PRINT_HOOK_ACTIVE.set(True)

        def _raw_print(*args, **kwargs):
            if "file" not in kwargs and self._orig_stdout is not None:
                kwargs["file"] = self._orig_stdout
            return print(*args, **kwargs)

        self._raw_print = _raw_print
        return self._raw_print

    def restore(self) -> None:
        if self._orig_stdout is None:
            return
        if self._stdout_wrapper is not None:
            try:
                self._stdout_wrapper.flush()
            except Exception:
                pass
        if self._token is not None:
            _PRINT_HOOK_ACTIVE.reset(self._token)
            self._token = None
        if sys.stdout is self._stdout_wrapper:
            sys.stdout = self._orig_stdout
        self._orig_stdout = None
        self._stdout_wrapper = None
        self._raw_print = None


_PRINT_HOOK_ACTIVE: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "print_hook_active",
    default=False,
)


class _TaggedStdout:
    def __init__(self, *, stream, hook: PrintHook) -> None:
        self._stream = stream
        self._hook = hook
        self._buffer = ""

    def _handle_line(self, line: str) -> None:
        msg = line.rstrip("\n")
        if not _PRINT_HOOK_ACTIVE.get():
            self._stream.write(line)
            return

        if msg == "":
            if not self._hook.suppress_info:
                self._stream.write(line)
            return

        tag = self._hook._extract_tag(msg)
        if tag in ("[ERROR]", "[FAIL]"):
            self._stream.write(line)
            return
        if tag:
            self._hook.warn_collector.add(msg, tag=tag)
            if self._hook.suppress_info:
                return
            self._stream.write(line)
            return
        if self._hook.suppress_info:
            return
        self._stream.write(line)

    def write(self, text: str) -> int:
        if not text:
            return 0
        self._buffer += text
        lines = self._buffer.splitlines(keepends=True)
        if not lines:
            return len(text)
        if not lines[-1].endswith("\n"):
            self._buffer = lines[-1]
            lines = lines[:-1]
        else:
            self._buffer = ""
        for line in lines:
            self._handle_line(line)
        return len(text)

    def flush(self) -> None:
        if self._buffer:
            self._handle_line(self._buffer)
            self._buffer = ""
        try:
            self._stream.flush()
        except Exception:
            pass

    def __getattr__(self, name: str):
        return getattr(self._stream, name)
