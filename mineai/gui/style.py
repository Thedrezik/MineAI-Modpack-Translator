"""Shared visual tokens and lightweight accessibility helpers for MineAI GUI."""

from __future__ import annotations

import customtkinter as ctk


class UI:
    APP_BG = ("#e8edf5", "#070b14")
    SIDEBAR_BG = ("#eef2f7", "#0a0f1b")
    CARD_BG = ("#ffffff", "#0f1626")
    CARD_ALT_BG = ("#f7f9fc", "#111b2e")
    BORDER = ("#d9e0ea", "#223049")
    TEXT = ("#111827", "#eef2ff")
    MUTED = ("#5b6472", "#93a4bd")
    INFO_BG = ("#e5edff", "#182b47")
    INFO_TEXT = ("#2b5db2", "#7db4ff")

    LOG_BG = "#0b1020"
    PRIMARY = "#2f81f7"
    PRIMARY_HOVER = "#1f6fd8"
    SUCCESS = "#28a745"
    SUCCESS_HOVER = "#218838"
    WARNING = "#ffc107"
    WARNING_HOVER = "#e0a800"
    DANGER = "#dc3545"
    DANGER_HOVER = "#c82333"
    CYAN = "#17a2b8"
    CYAN_HOVER = "#138496"
    NEUTRAL = "#445064"
    NEUTRAL_HOVER = "#364154"


class ToolTip(ctk.CTkToplevel):
    """Small mouse/focus tooltip that stays within the active monitor."""

    def __init__(self, widget, title: str, text: str, *, delay_ms: int = 300) -> None:
        parent = getattr(widget, "master", None) or getattr(widget, "_parent", None)
        super().__init__(master=parent)
        self._tip_widget = widget
        self._delay_ms = delay_ms
        self._after_id = None
        self.withdraw()
        try:
            self.overrideredirect(True)
            self.attributes("-topmost", True)
        except Exception:
            pass

        frame = ctk.CTkFrame(
            self,
            corner_radius=10,
            fg_color="#0b1220",
            border_width=1,
            border_color="#2b3a55",
        )
        frame.pack(fill="both", expand=True)
        ctk.CTkLabel(
            frame,
            text=title,
            anchor="w",
            justify="left",
            font=("Segoe UI", 12, "bold"),
            text_color="#e8efff",
        ).pack(anchor="w", padx=10, pady=(8, 0))
        ctk.CTkLabel(
            frame,
            text=text,
            anchor="w",
            justify="left",
            wraplength=310,
            font=("Segoe UI", 11),
            text_color="#b7c6e0",
        ).pack(anchor="w", padx=10, pady=(2, 8))
        self._bind_targets()

    def _bind_targets(self) -> None:
        targets = [self._tip_widget]
        for attr in ("_entry", "_textbox"):
            target = getattr(self._tip_widget, attr, None)
            if target is not None:
                targets.append(target)
        seen: set[int] = set()
        for target in targets:
            if id(target) in seen:
                continue
            seen.add(id(target))
            target.bind("<Enter>", self._schedule, add="+")
            target.bind("<Leave>", self._hide, add="+")
            target.bind("<FocusIn>", self._schedule, add="+")
            target.bind("<FocusOut>", self._hide, add="+")
            target.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None) -> None:
        self._hide()
        try:
            self._after_id = self._tip_widget.after(self._delay_ms, self._show)
        except Exception:
            self._after_id = None

    def _show(self) -> None:
        try:
            if not self._tip_widget.winfo_exists():
                return
            self.update_idletasks()
            tip_w = max(self.winfo_reqwidth(), 220)
            tip_h = max(self.winfo_reqheight(), 60)
            root_x = self._tip_widget.winfo_rootx()
            root_y = self._tip_widget.winfo_rooty()
            widget_w = self._tip_widget.winfo_width()
            widget_h = self._tip_widget.winfo_height()
            screen_w = self.winfo_screenwidth()
            screen_h = self.winfo_screenheight()
            margin = 10
            right_x = root_x + widget_w + margin
            left_x = root_x - tip_w - margin
            x = right_x if right_x + tip_w <= screen_w - margin else max(margin, left_x)
            below_y = root_y + widget_h + margin
            above_y = root_y - tip_h - margin
            y = below_y if below_y + tip_h <= screen_h - margin else max(margin, above_y)
            self.geometry(f"+{int(x)}+{int(y)}")
            self.deiconify()
        except Exception:
            self.withdraw()

    def _hide(self, _event=None) -> None:
        if self._after_id is not None:
            try:
                self._tip_widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        try:
            self.withdraw()
        except Exception:
            pass
