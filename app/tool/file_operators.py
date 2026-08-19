"""File operation interfaces and implementations for the local Windows host."""

import asyncio
from pathlib import Path
from typing import Optional, Protocol, Tuple, Union, runtime_checkable

from app.exceptions import ToolError


PathLike = Union[str, Path]


@runtime_checkable
class FileOperator(Protocol):
    """Interface for file operations in different environments."""

    async def read_file(self, path: PathLike) -> str:
        """Read content from a file."""
        ...

    async def write_file(self, path: PathLike, content: str) -> None:
        """Write content to a file."""
        ...

    async def is_directory(self, path: PathLike) -> bool:
        """Check if path points to a directory."""
        ...

    async def exists(self, path: PathLike) -> bool:
        """Check if path exists."""
        ...

    async def run_command(
        self, cmd: str, timeout: Optional[float] = 120.0
    ) -> Tuple[int, str, str]:
        """Run a shell command and return (return_code, stdout, stderr)."""
        ...


class LocalFileOperator(FileOperator):
    """File operations implementation for local filesystem."""

    encoding: str = "utf-8"

    async def read_file(self, path: PathLike) -> str:
        """Read content from a local file."""
        try:
            raw = Path(path).read_bytes()
            for encoding in ("utf-8-sig", "utf-8", "gb18030"):
                try:
                    return raw.decode(encoding)
                except UnicodeDecodeError:
                    continue
            raise UnicodeDecodeError("unknown", raw, 0, 1, "unsupported text encoding")
        except Exception as e:
            raise ToolError(f"Failed to read {path}: {str(e)}") from None

    async def write_file(self, path: PathLike, content: str) -> None:
        """Write content to a local file."""
        try:
            Path(path).write_text(content, encoding=self.encoding)
        except Exception as e:
            raise ToolError(f"Failed to write to {path}: {str(e)}") from None

    async def is_directory(self, path: PathLike) -> bool:
        """Check if path points to a directory."""
        return Path(path).is_dir()

    async def exists(self, path: PathLike) -> bool:
        """Check if path exists."""
        return Path(path).exists()

    async def run_command(
        self, cmd: str, timeout: Optional[float] = 120.0
    ) -> Tuple[int, str, str]:
        """Run a shell command locally."""
        process = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
            return (process.returncode or 0, stdout.decode(errors="replace"), stderr.decode(errors="replace"))
        except asyncio.TimeoutError as exc:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            raise TimeoutError(
                f"Command '{cmd}' timed out after {timeout} seconds"
            ) from exc

