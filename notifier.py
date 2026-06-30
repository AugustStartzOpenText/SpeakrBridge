from __future__ import annotations

import logging

LOGGER = logging.getLogger(__name__)
MAX_TOAST_MESSAGE_LENGTH = 240


class Notifier:
    def __init__(self, enabled: bool) -> None:
        self._enabled = enabled

    def notify_success(self, meeting_title: str, section_name: str) -> None:
        if not self._enabled:
            return
        self._show_toast("SpeakrBridge ✓", f"'{meeting_title}' saved to OneNote -> {section_name}")

    def notify_failure(self, detail: str) -> None:
        if not self._enabled:
            return
        self._show_toast("SpeakrBridge", f"OneNote page creation failed - {detail}")

    def _show_toast(self, title: str, message: str) -> None:
        message = self._truncate_message(message)
        try:
            from win10toast import ToastNotifier  # type: ignore[import-not-found]

            ToastNotifier().show_toast(title, message, duration=5)
            return
        except Exception:
            LOGGER.debug("win10toast unavailable, trying plyer", exc_info=True)

        try:
            from plyer import notification  # type: ignore[import-not-found]

            notification.notify(title=title, message=message, timeout=5)
        except Exception:
            LOGGER.warning("Toast notification unavailable", exc_info=True)

    @staticmethod
    def _truncate_message(message: str) -> str:
        if len(message) <= MAX_TOAST_MESSAGE_LENGTH:
            return message
        return message[: MAX_TOAST_MESSAGE_LENGTH - 3] + "..."
