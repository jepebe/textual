from __future__ import annotations

import asyncio
import shutil
from abc import ABC, abstractmethod
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO, Iterator, Literal, TextIO

from . import events
from .events import MouseUp

if TYPE_CHECKING:
    from .app import App


class Driver(ABC):
    """A base class for drivers."""

    def __init__(
        self,
        app: App[Any],
        *,
        debug: bool = False,
        mouse: bool = True,
        size: tuple[int, int] | None = None,
    ) -> None:
        """Initialize a driver.

        Args:
            app: The App instance.
            debug: Enable debug mode.
            mouse: Enable mouse support,
            size: Initial size of the terminal or `None` to detect.
        """
        self._app = app
        self._debug = debug
        self._mouse = mouse
        self._size = size
        self._loop = asyncio.get_running_loop()
        self._down_buttons: list[int] = []
        self._last_move_event: events.MouseMove | None = None
        self._auto_restart = True
        """Should the application auto-restart (where appropriate)?"""
        self.cursor_origin: tuple[int, int] | None = None

    @property
    def is_headless(self) -> bool:
        """Is the driver 'headless' (no output)?"""
        return False

    @property
    def is_inline(self) -> bool:
        """Is the driver 'inline' (not full-screen)?"""
        return False

    @property
    def can_suspend(self) -> bool:
        """Can this driver be suspended?"""
        return False

    def send_event(self, event: events.Event) -> None:
        """Send an event to the target app.

        Args:
            event: An event.
        """
        asyncio.run_coroutine_threadsafe(
            self._app._post_message(event), loop=self._loop
        )

    def process_event(self, event: events.Event) -> None:
        """Perform additional processing on an event, prior to sending.

        Args:
            event: An event to send.
        """
        # NOTE: This runs in a thread.
        # Avoid calling methods on the app.
        event.set_sender(self._app)
        if self.cursor_origin is None:
            offset_x = 0
            offset_y = 0
        else:
            offset_x, offset_y = self.cursor_origin
        if isinstance(event, events.MouseEvent):
            event.x -= offset_x
            event.y -= offset_y
            event.screen_x -= offset_x
            event.screen_y -= offset_y

        if isinstance(event, events.MouseDown):
            if event.button:
                self._down_buttons.append(event.button)
        elif isinstance(event, events.MouseUp):
            if event.button and event.button in self._down_buttons:
                self._down_buttons.remove(event.button)
        elif isinstance(event, events.MouseMove):
            if (
                self._down_buttons
                and not event.button
                and self._last_move_event is not None
            ):
                # Deduplicate self._down_buttons while preserving order.
                buttons = list(dict.fromkeys(self._down_buttons).keys())
                self._down_buttons.clear()
                move_event = self._last_move_event
                for button in buttons:
                    self.send_event(
                        MouseUp(
                            x=move_event.x,
                            y=move_event.y,
                            delta_x=0,
                            delta_y=0,
                            button=button,
                            shift=event.shift,
                            meta=event.meta,
                            ctrl=event.ctrl,
                            screen_x=move_event.screen_x,
                            screen_y=move_event.screen_y,
                            style=event.style,
                        )
                    )
            self._last_move_event = event

        self.send_event(event)

    @abstractmethod
    def write(self, data: str) -> None:
        """Write data to the output device.

        Args:
            data: Raw data.
        """

    def flush(self) -> None:
        """Flush any buffered data."""

    @abstractmethod
    def start_application_mode(self) -> None:
        """Start application mode."""

    @abstractmethod
    def disable_input(self) -> None:
        """Disable further input."""

    @abstractmethod
    def stop_application_mode(self) -> None:
        """Stop application mode, restore state."""

    def suspend_application_mode(self) -> None:
        """Suspend application mode.

        Used to suspend application mode and allow uninhibited access to the
        terminal.
        """
        self.stop_application_mode()
        self.close()

    def resume_application_mode(self) -> None:
        """Resume application mode.

        Used to resume application mode after it has been previously
        suspended.
        """
        self.start_application_mode()

    class SignalResume(events.Event):
        """Event sent to the app when a resume signal should be published."""

    @contextmanager
    def no_automatic_restart(self) -> Iterator[None]:
        """A context manager used to tell the driver to not auto-restart.

        For drivers that support the application being suspended by the
        operating system, this context manager is used to mark a body of
        code as one that will manage its own stop and start.
        """
        auto_restart = self._auto_restart
        self._auto_restart = False
        try:
            yield
        finally:
            self._auto_restart = auto_restart

    def close(self) -> None:
        """Perform any final cleanup."""

    def open_url(self, url: str, new_tab: bool = True) -> None:
        """Open a URL in the default web browser.

        Args:
            url: The URL to open.
            new_tab: Whether to open the URL in a new tab.
                This is only relevant when running via the WebDriver,
                and is ignored when called while running through the terminal.
        """
        import webbrowser

        webbrowser.open(url)

    def deliver_binary(
        self,
        binary: BinaryIO | TextIO,
        *,
        save_path: Path,
        open_method: Literal["browser", "download"] = "download",
        encoding: str | None = None,
        mime_type: str | None = None,
    ) -> None:
        """Save the file `path_or_file` to `save_path`.

        If running via web through Textual Web or Textual Serve,
        this will initiate a download in the web browser.

        Args:
            binary: The binary file to save.
            save_path: The location to save the file to. If None,
                the default "downloads" directory will be used. When
                running via web, only the file name will be used.
            open_method: *web only* Whether to open the file in the browser or
                to prompt the user to download it. When running via a standard
                (non-web) terminal, this is ignored.
            encoding: *web only* The text encoding to use when saving the file.
                This will be passed to Python's `open()` built-in function.
                When running via web, this will be used to set the charset
                in the `Content-Type` header.
            mime_type: *web only* The MIME type of the file. This will be used to
                set the `Content-Type` header in the HTTP response.
        """
        if isinstance(binary, BinaryIO):
            with open(save_path, "wb") as destination_file:
                shutil.copyfileobj(binary, destination_file)
        else:  # isinstance(binary, TextIO):
            with open(save_path, "w", encoding=encoding) as destination_file:
                shutil.copyfileobj(binary, destination_file)

        binary.close()
