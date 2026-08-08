"""Reusable widgets for the Qt dashboard."""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QProgressBar, QVBoxLayout, QWidget


class Card(QFrame):
    def __init__(self, title: str, subtitle: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.layout_root = QVBoxLayout(self)
        self.layout_root.setContentsMargins(12, 10, 12, 12)
        self.layout_root.setSpacing(6)

        title_label = QLabel(title.upper())
        title_label.setObjectName("SectionTitle")
        self.layout_root.addWidget(title_label)

        if subtitle:
            subtitle_label = QLabel(subtitle)
            subtitle_label.setObjectName("SectionSubtitle")
            subtitle_label.setWordWrap(True)
            self.layout_root.addWidget(subtitle_label)

        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(6)
        self.layout_root.addLayout(self.body)


class StatCard(QFrame):
    def __init__(self, title: str, accent: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName(accent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 11, 14, 11)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        self.icon = QLabel("●")
        self.icon.setStyleSheet("background: transparent; border: none; color: #94A3B8; font-size: 14px;")
        caption = QLabel(title.upper())
        caption.setObjectName("KpiCaption")
        header.addWidget(self.icon)
        header.addWidget(caption)
        header.addStretch(1)
        layout.addLayout(header)

        self.value = QLabel("—")
        self.value.setObjectName("KpiValue")
        layout.addWidget(self.value)

        self.meta = QLabel("")
        self.meta.setObjectName("KpiMeta")
        layout.addWidget(self.meta)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setMaximumHeight(7)
        layout.addWidget(self.progress)


class StatusPill(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("GlobalWarning")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(11, 6, 11, 6)
        self.label = QLabel("Проверка готовности")
        self.label.setObjectName("WarningText")
        layout.addWidget(self.label)

    def set_ready(self, ready: bool, text: str) -> None:
        self.setObjectName("GlobalReady" if ready else "GlobalWarning")
        self.style().unpolish(self)
        self.style().polish(self)
        self.label.setObjectName("ReadyText" if ready else "WarningText")
        self.label.setText(("✓  " if ready else "⚠  ") + text)
        self.label.style().unpolish(self.label)
        self.label.style().polish(self.label)


class LabeledValue(QFrame):
    """Compact key/value row used in the current-task card."""

    def __init__(self, label: str, parent=None) -> None:
        super().__init__(parent)
        self.setStyleSheet("background: transparent; border: none;")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        key = QLabel(label)
        key.setObjectName("MutedLabel")
        self.value = QLabel("—")
        self.value.setObjectName("StrongLabel")
        self.value.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(key)
        layout.addWidget(self.value)


class SegmentedProgressBar(QWidget):
    """Lightweight segmented progress bar matching the dashboard mockup."""

    def __init__(self, segments: int = 42, parent=None) -> None:
        super().__init__(parent)
        self._segments = max(8, segments)
        self._value = 0.0
        self.setMinimumHeight(18)
        self.setMaximumHeight(18)

    def setValue(self, value: float) -> None:
        self._value = max(0.0, min(1.0, float(value)))
        self.update()

    def value(self) -> float:
        return self._value

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        width = self.width()
        height = self.height()
        gap = 3.0
        segment_width = max(1.0, (width - gap * (self._segments - 1)) / self._segments)
        filled = self._value * self._segments
        for index in range(self._segments):
            x = index * (segment_width + gap)
            rect = QRectF(x, 2.0, segment_width, max(2.0, height - 4.0))
            color = QColor("#7C5CE0") if index < filled else QColor("#26283A")
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(rect, 2.5, 2.5)
