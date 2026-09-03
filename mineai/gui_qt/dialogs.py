"""Qt versions of Settings, Prompt Editor and Migration dialogs.

They use the same configuration, prompt and migration APIs as the current beta.
No translation engine or processor behavior is reimplemented here.
"""

from __future__ import annotations

import os
import re
import threading
import textwrap
import traceback
from dataclasses import replace

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QPen
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QGraphicsScene,
    QGraphicsView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mineai.constants import DEFAULT_OPENROUTER_MODEL, LANGUAGES
from mineai.engines.llama import list_llama_models, normalize_llama_base_url
from mineai.engines.lmstudio import (
    list_loaded_lmstudio_models,
    normalize_lmstudio_base_url,
)
from mineai.engines.ollama import (
    list_loaded_ollama_models,
    normalize_ollama_base_url,
)
from mineai.engines.llm_common import get_default_prompts, load_prompts, save_prompts
from mineai.processors.migration import run_migration
from mineai.gui_qt.bridge import LmStudioSignals, MigrationSignals, ProviderSignals
from mineai.gui_qt.i18n import t
from mineai.gui_qt.widgets import HelpMarker, ScrollSafeSpinBox


class QuestGraphView(QGraphicsView):
    """Compact graph view used by the preview dialog, not by translation."""

    node_clicked = pyqtSignal(str)
    zoom_changed = pyqtSignal(float)

    def __init__(self, documents, parent=None) -> None:
        super().__init__(parent)
        from mineai.preview import build_quest_graph_layout

        scene = QGraphicsScene(self)
        self.setScene(scene)
        self.setMinimumHeight(260)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setStyleSheet("QGraphicsView { background: #151724; border: 1px solid #3b3f5b; }")
        self.setToolTip("Колесо мыши — масштаб; перетаскивание — панорамирование")
        self._zoom_level = 1.0
        self._min_zoom = 0.25
        self._max_zoom = 2.5
        self._node_rects: dict[str, object] = {}
        self._node_defaults: dict[str, tuple[QBrush, QPen]] = {}
        self._node_dependencies: dict[str, frozenset[str]] = {}
        self._node_texts: dict[str, object] = {}
        self.highlighted_node_id = ""
        self.highlighted_dependency_ids: set[str] = set()
        card_width = 235
        card_height = 98
        x_offset = 0
        for document in documents:
            if document.kind != "quest" or not document.graph_nodes:
                continue
            layout = build_quest_graph_layout(document)
            document_dependencies = {
                graph_node.node_id.casefold(): graph_node.dependencies
                for graph_node in document.graph_nodes
            }
            positions = {}
            max_column = max((node.column for node in layout.nodes), default=0)
            for node in layout.nodes:
                x = x_offset + node.column * 270
                y = 30 + node.level * 145
                positions[node.node_id] = (x, y)
            for source_id, target_id in layout.edges:
                source = positions.get(source_id)
                target = positions.get(target_id)
                if source is None or target is None:
                    continue
                line = scene.addLine(
                    source[0] + card_width / 2,
                    source[1] + card_height,
                    target[0] + card_width / 2,
                    target[1],
                    QPen(QColor("#737a92"), 1),
                )
                # Keep dependency lines behind cards so a dense graph remains
                # readable and the card itself receives the mouse click.
                line.setZValue(-1)
            for node in layout.nodes:
                x, y = positions[node.node_id]
                rect = scene.addRect(x, y, card_width, card_height)
                rect.setData(0, node.node_id)
                rect.setBrush(QBrush(QColor("#4a4d57")))
                rect.setPen(QPen(QColor("#20c7b7"), 1))
                self._node_rects[node.node_id] = rect
                self._node_defaults[node.node_id] = (rect.brush(), rect.pen())
                self._node_dependencies[node.node_id.casefold()] = frozenset(
                    dependency.casefold()
                    for dependency in document_dependencies.get(node.node_id.casefold(), ())
                )
                title = _strip_mc_codes(node.title or "Квест")
                title = _graph_card_text(title) or "Квест"
                text = scene.addText(title)
                text.setData(0, node.node_id)
                text.setDefaultTextColor(Qt.GlobalColor.white)
                text.setTextWidth(215)
                text.setPos(x + 10, y + 8)
                self._node_texts[node.node_id] = text
            x_offset += (max_column + 1) * 290 + 40
            scene.addText(document.logical_path).setPos(x_offset - (max_column + 1) * 290, 4)
        scene.setSceneRect(scene.itemsBoundingRect().adjusted(-20, -20, 20, 20))

    @property
    def zoom_level(self) -> float:
        return self._zoom_level

    def _set_zoom(self, value: float) -> None:
        target = max(self._min_zoom, min(self._max_zoom, float(value)))
        current = self._zoom_level
        if abs(target - current) < 0.001:
            return
        self.scale(target / current, target / current)
        self._zoom_level = target
        self.zoom_changed.emit(target)

    def zoom_in(self) -> None:
        self._set_zoom(self._zoom_level * 1.15)

    def zoom_out(self) -> None:
        self._set_zoom(self._zoom_level / 1.15)

    def reset_zoom(self) -> None:
        self.resetTransform()
        self._zoom_level = 1.0
        self.zoom_changed.emit(self._zoom_level)

    def fit_graph(self) -> None:
        """Fit the complete graph while never enlarging it past 100%."""
        scene = self.scene()
        if scene is None:
            return
        rect = scene.itemsBoundingRect().adjusted(-20, -20, 20, 20)
        if rect.isEmpty():
            self.reset_zoom()
            return
        self.resetTransform()
        self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        fitted = self.transform().m11()
        if fitted > 1.0:
            self.resetTransform()
            fitted = 1.0
        if fitted < self._min_zoom:
            self.resetTransform()
            self.scale(self._min_zoom, self._min_zoom)
            fitted = self._min_zoom
        self._zoom_level = fitted
        self.zoom_changed.emit(fitted)
        self.centerOn(rect.center())

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        if not delta:
            super().wheelEvent(event)
            return
        self._set_zoom(self._zoom_level * (1.15 ** (delta / 120.0)))
        event.accept()

    def highlight_node(self, node_id: str) -> bool:
        """Highlight and center a quest card selected from the unit list."""
        normalized = (node_id or "").casefold()
        for current_id, rect in self._node_rects.items():
            default_brush, default_pen = self._node_defaults[current_id]
            rect.setBrush(default_brush)
            rect.setPen(default_pen)
        rect = next(
            (item for current_id, item in self._node_rects.items()
             if current_id.casefold() == normalized),
            None,
        )
        if rect is None:
            return False
        dependency_ids = set(self._node_dependencies.get(normalized, ()))
        self.highlighted_dependency_ids = {
            current_id
            for current_id in self._node_rects
            if current_id.casefold() in dependency_ids
        }
        for current_id, dependency_rect in self._node_rects.items():
            if current_id.casefold() in dependency_ids:
                dependency_rect.setBrush(QBrush(QColor("#9a6a2f")))
                dependency_rect.setPen(QPen(QColor("#e6c36a"), 2))
        rect.setBrush(QBrush(QColor("#6650a4")))
        rect.setPen(QPen(QColor("#ffd36e"), 3))
        self.highlighted_node_id = next(
            (current_id for current_id in self._node_rects if current_id.casefold() == normalized),
            node_id,
        )
        self.centerOn(rect)
        return True

    @staticmethod
    def _node_id_from_item(item) -> str:
        while item is not None:
            value = item.data(0)
            if value:
                return str(value)
            item = item.parentItem()
        return ""

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            item = self.itemAt(event.position().toPoint())
            node_id = self._node_id_from_item(item) if item is not None else ""
            if node_id:
                self.node_clicked.emit(node_id)
                event.accept()
                return
        super().mousePressEvent(event)


def _preview_text(value: str, limit: int = 140) -> str:
    value = " ".join((value or "").split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _graph_card_text(value: str, line_width: int = 28, max_lines: int = 3) -> str:
    """Wrap quest titles to a bounded number of lines so cards never overflow."""
    compact = _preview_text(value, line_width * max_lines)
    lines = textwrap.wrap(
        compact,
        width=line_width,
        break_long_words=True,
        break_on_hyphens=False,
    )
    return "\n".join(lines[:max_lines])


def _strip_mc_codes(value: str) -> str:
    return re.sub(r"[&§][0-9A-Za-z]", "", value or "")


def _walk_tree_items(item):
    yield item
    for index in range(item.childCount()):
        yield from _walk_tree_items(item.child(index))


class PreviewDialog(QDialog):
    """Interactive, read-only preview with safe unit-level retry selection."""

    def __init__(self, report, parent=None, on_retranslate=None) -> None:
        super().__init__(parent)
        self.report = report
        self._on_retranslate = on_retranslate
        self._selection_trees: list[QTreeWidget] = []
        self._graph_views: list[QuestGraphView] = []
        self._book_documents = tuple(
            document for document in report.documents if document.kind == "book"
        )
        self._book_chapter_index = 0
        self._book_page_index = 0
        self._book_show_original = False
        self.setWindowTitle(t("preview.title"))
        self.resize(1280, 860)
        self.setMinimumSize(900, 620)
        self.setSizeGripEnabled(True)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        heading = QLabel(t("preview.heading"))
        heading.setObjectName("AppTitle")
        root.addWidget(heading)
        note = QLabel(t("preview.note"))
        note.setObjectName("MutedLabel")
        note.setWordWrap(True)
        root.addWidget(note)

        self.tabs = QTabWidget()
        self.summary_view = self._html_view()
        self.books_view = self._html_view()
        self.quests_view = self._html_view()
        self.issues_view = self._html_view()
        self.tabs.addTab(self.summary_view, t("preview.summary"))
        self.tabs.addTab(
            self._make_preview_tab("book", self.books_view),
            t("preview.books"),
        )
        self.tabs.addTab(
            self._make_preview_tab("quest", self.quests_view),
            t("preview.quests"),
        )
        self.tabs.addTab(
            self._make_preview_tab("issue", self.issues_view),
            t("preview.issues"),
        )
        root.addWidget(self.tabs, 1)

        from mineai.preview import render_preview_html

        self.summary_view.setHtml(render_preview_html(report))
        self._refresh_book_view()
        self.quests_view.setHtml(render_preview_html(report, kind="quest"))
        self.issues_view.setPlainText(report.to_text())

        actions = QHBoxLayout()
        self.retry_button = QPushButton(t("preview.retranslate"))
        self.retry_button.setObjectName("PrimaryButton")
        self.retry_button.clicked.connect(self._retranslate_selected)
        save = QPushButton(t("preview.save"))
        close = QPushButton(t("button.cancel"))
        save.clicked.connect(self._save_report)
        close.clicked.connect(self.reject)
        actions.addWidget(self.retry_button)
        actions.addStretch(1)
        save.setToolTip(t("preview.save_hint"))
        actions.addWidget(save)
        actions.addWidget(close)
        root.addLayout(actions)

    @staticmethod
    def _html_view() -> QTextBrowser:
        view = QTextBrowser()
        view.setOpenExternalLinks(False)
        view.setOpenLinks(False)
        return view

    def _make_preview_tab(self, kind: str, html_view: QTextBrowser) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        controls = QHBoxLayout()
        hint = QLabel(t("preview.selection_hint"))
        hint.setObjectName("MutedLabel")
        hint.setWordWrap(True)
        controls.addWidget(hint, 1)
        select_all = QPushButton(t("preview.select_all"))
        select_none = QPushButton(t("preview.select_none"))
        controls.addWidget(select_all)
        controls.addWidget(select_none)
        layout.addLayout(controls)

        if kind == "book":
            book_navigation = QHBoxLayout()
            chapter_label = QLabel(t("preview.chapter"))
            chapter_label.setObjectName("MutedLabel")
            self.book_chapter_combo = QComboBox()
            self.book_chapter_combo.setObjectName("BookChapterCombo")
            self.book_chapter_combo.setMinimumWidth(280)
            previous = QPushButton("‹")
            previous.setObjectName("BookPreviousButton")
            previous.setToolTip(t("preview.previous"))
            page_label = QLabel("—")
            page_label.setObjectName("BookPageLabel")
            page_label.setMinimumWidth(110)
            page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            following = QPushButton("›")
            following.setObjectName("BookNextButton")
            following.setToolTip(t("preview.next"))
            original = QPushButton(t("preview.show_original"))
            original.setObjectName("BookOriginalButton")
            original.setToolTip(t("preview.show_original_hint"))
            book_navigation.addWidget(chapter_label)
            book_navigation.addWidget(self.book_chapter_combo, 1)
            book_navigation.addWidget(previous)
            book_navigation.addWidget(page_label)
            book_navigation.addWidget(following)
            book_navigation.addWidget(original)
            layout.addLayout(book_navigation)
            self.book_page_label = page_label
            self.book_previous_button = previous
            self.book_next_button = following
            self.book_original_button = original
            self.book_chapter_combo.currentIndexChanged.connect(
                self._book_chapter_changed
            )
            previous.clicked.connect(self._book_previous)
            following.clicked.connect(self._book_next)
            original.clicked.connect(self._toggle_book_original)

        splitter = QSplitter(Qt.Orientation.Vertical)
        if kind == "quest":
            graph_documents = [
                document
                for document in self.report.documents
                if document.kind == "quest"
            ]
            graph_view = QuestGraphView(graph_documents)
            graph_view.node_clicked.connect(self._select_graph_node)
            self._graph_views.append(graph_view)
            splitter.addWidget(graph_view)
            zoom_caption = QLabel(t("preview.zoom"))
            zoom_caption.setObjectName("MutedLabel")
            zoom_label = QLabel("100%")
            zoom_label.setObjectName("GraphZoomLevel")
            zoom_label.setMinimumWidth(48)
            zoom_out = QPushButton("−")
            zoom_out.setObjectName("GraphZoomOut")
            zoom_out.setToolTip(t("preview.zoom_out"))
            zoom_in = QPushButton("+")
            zoom_in.setObjectName("GraphZoomIn")
            zoom_in.setToolTip(t("preview.zoom_in"))
            zoom_fit = QPushButton(t("preview.zoom_fit"))
            zoom_fit.setObjectName("GraphZoomFit")
            zoom_fit.setToolTip(t("preview.zoom_fit_hint"))
            controls.addWidget(zoom_caption)
            controls.addWidget(zoom_out)
            controls.addWidget(zoom_label)
            controls.addWidget(zoom_in)
            controls.addWidget(zoom_fit)
            zoom_out.clicked.connect(graph_view.zoom_out)
            zoom_in.clicked.connect(graph_view.zoom_in)
            zoom_fit.clicked.connect(graph_view.fit_graph)
            graph_view.zoom_changed.connect(
                lambda value: zoom_label.setText(f"{round(value * 100):d}%")
            )
        splitter.addWidget(html_view)

        tree = QTreeWidget()
        tree.setHeaderLabels((t("preview.block"), t("preview.status"), t("preview.source")))
        tree.setColumnWidth(0, 360)
        tree.setColumnWidth(1, 130)
        tree.setAlternatingRowColors(True)
        tree.setObjectName("TranslationSelectionTree")
        tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._populate_selection_tree(tree, kind)
        self._selection_trees.append(tree)
        splitter.addWidget(tree)
        splitter.setSizes([260, 300, 180] if kind == "quest" else [390, 180])
        layout.addWidget(splitter, 1)

        select_all.clicked.connect(lambda: self._set_tree_checks(tree, True))
        select_none.clicked.connect(lambda: self._set_tree_checks(tree, False))
        tree.itemClicked.connect(self._select_tree_item)
        return container

    def _refresh_book_view(self) -> None:
        """Render the selected chapter page with a reversible original view."""
        from mineai.preview import render_preview_html

        if not self._book_documents:
            self.books_view.setHtml(
                render_preview_html(
                    self.report,
                    kind="book",
                )
            )
            if hasattr(self, "book_chapter_combo"):
                self.book_chapter_combo.setEnabled(False)
                self.book_previous_button.setEnabled(False)
                self.book_next_button.setEnabled(False)
                self.book_original_button.setEnabled(False)
                self.book_page_label.setText("—")
            return

        self._book_chapter_index = max(
            0,
            min(self._book_chapter_index, len(self._book_documents) - 1),
        )
        document = self._book_documents[self._book_chapter_index]
        pages = document.pages
        if pages:
            self._book_page_index = max(0, min(self._book_page_index, len(pages) - 1))
            page = pages[self._book_page_index]
            page_ids = set(page.unit_ids)
            current_document = replace(
                document,
                pages=(page,),
                issues=tuple(
                    issue
                    for issue in document.issues
                    if not issue.unit_id or issue.unit_id in page_ids
                ),
            )
            current_issues = tuple(
                issue
                for issue in self.report.issues
                if issue.logical_path == document.logical_path
                and (not issue.unit_id or issue.unit_id in page_ids)
            )
            current_report = replace(
                self.report,
                documents=(current_document,),
                issues=current_issues,
            )
            self.books_view.setHtml(
                render_preview_html(
                    current_report,
                    kind="book",
                    book_original=self._book_show_original,
                )
            )
            self.book_page_label.setText(
                f"{t('preview.page')} {self._book_page_index + 1} / {len(pages)}"
            )
        else:
            self._book_page_index = 0
            self.books_view.setHtml(
                render_preview_html(
                    replace(self.report, documents=(document,)),
                    kind="book",
                    book_original=self._book_show_original,
                )
            )
            self.book_page_label.setText(t("preview.no_pages"))

        self.book_chapter_combo.blockSignals(True)
        self.book_chapter_combo.clear()
        for index, chapter in enumerate(self._book_documents, 1):
            name = chapter.logical_path.replace("\\", "/").rsplit("/", 1)[-1]
            self.book_chapter_combo.addItem(
                f"{index}. {_preview_text(name, 70)} · {chapter.format}",
            )
        self.book_chapter_combo.setCurrentIndex(self._book_chapter_index)
        self.book_chapter_combo.blockSignals(False)
        self.book_chapter_combo.setEnabled(True)
        self.book_previous_button.setEnabled(bool(pages) and self._book_page_index > 0)
        self.book_next_button.setEnabled(
            bool(pages) and self._book_page_index + 1 < len(pages)
        )
        self.book_original_button.setEnabled(bool(pages))
        self.book_original_button.setText(
            t("preview.show_translation")
            if self._book_show_original
            else t("preview.show_original")
        )

    def _book_chapter_changed(self, index: int) -> None:
        if index < 0 or index >= len(self._book_documents):
            return
        self._book_chapter_index = index
        self._book_page_index = 0
        self._refresh_book_view()

    def _book_previous(self) -> None:
        if self._book_page_index <= 0:
            return
        self._book_page_index -= 1
        self._refresh_book_view()

    def _book_next(self) -> None:
        if not self._book_documents:
            return
        pages = self._book_documents[self._book_chapter_index].pages
        if self._book_page_index + 1 >= len(pages):
            return
        self._book_page_index += 1
        self._refresh_book_view()

    def _toggle_book_original(self) -> None:
        self._book_show_original = not self._book_show_original
        self._refresh_book_view()

    def _select_graph_node(self, node_id: str) -> None:
        """Check every text unit belonging to the clicked quest card."""
        prefix = f"snbt/{node_id.casefold()}/"
        for graph_view in self._graph_views:
            graph_view.highlight_node(node_id)
        for tree in self._selection_trees:
            for index in range(tree.topLevelItemCount()):
                for item in _walk_tree_items(tree.topLevelItem(index)):
                    if item.childCount() or item.data(0, Qt.ItemDataRole.UserRole) is None:
                        continue
                    key = str(item.data(0, Qt.ItemDataRole.UserRole))
                    unit_id = key.rsplit("::", 1)[-1].casefold()
                    if unit_id.startswith(prefix):
                        item.setCheckState(0, Qt.CheckState.Checked)
                        item.setSelected(True)
                        tree.setCurrentItem(item)
                        tree.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)

    @staticmethod
    def _quest_node_id_from_selection_key(value) -> str:
        if not value:
            return ""
        unit_id = str(value).rsplit("::", 1)[-1]
        match = re.match(r"(?i)^snbt/([0-9a-f]{16})/", unit_id)
        return match.group(1) if match else ""

    def _select_tree_item(self, item: QTreeWidgetItem, _column: int) -> None:
        """Center the graph card corresponding to the clicked quest row."""
        if item.childCount():
            return
        node_id = self._quest_node_id_from_selection_key(
            item.data(0, Qt.ItemDataRole.UserRole)
        )
        if not node_id:
            return
        item.setSelected(True)
        tree = item.treeWidget()
        if tree is not None:
            tree.setCurrentItem(item)
        for graph_view in self._graph_views:
            graph_view.highlight_node(node_id)

    def _populate_selection_tree(self, tree: QTreeWidget, kind: str) -> None:
        from mineai.preview import preview_selection_key

        problem_kinds = {"untranslated", "structure", "missing"}
        issue_by_unit = {
            (issue.logical_path, issue.unit_id): issue
            for issue in self.report.issues
            if issue.unit_id
        }
        for document in self.report.documents:
            if kind != "issue" and document.kind != kind:
                continue
            if kind == "issue":
                issues = [
                    issue
                    for issue in document.issues
                    if issue.kind in problem_kinds and issue.unit_id
                ]
                if not issues:
                    continue
                parent = QTreeWidgetItem(tree, (document.logical_path, "", ""))
                parent.setExpanded(True)
                for issue in issues:
                    self._add_selection_row(tree, parent, document.logical_path, issue.unit_id, issue)
                continue

            parent = QTreeWidgetItem(tree, (document.logical_path, document.format, ""))
            parent.setExpanded(True)
            seen: set[str] = set()
            for page in document.pages:
                for unit_id in page.unit_ids:
                    if unit_id in seen:
                        continue
                    seen.add(unit_id)
                    issue = issue_by_unit.get((document.logical_path, unit_id))
                    self._add_selection_row(
                        tree,
                        parent,
                        document.logical_path,
                        unit_id,
                        issue,
                        page.index,
                        page.target or page.source,
                    )
            for issue in document.issues:
                if not issue.unit_id or issue.unit_id in seen:
                    continue
                seen.add(issue.unit_id)
                self._add_selection_row(
                    tree,
                    parent,
                    document.logical_path,
                    issue.unit_id,
                    issue,
                )

    def _add_selection_row(
        self,
        tree: QTreeWidget,
        parent: QTreeWidgetItem,
        logical_path: str,
        unit_id: str,
        issue,
        page_index: int | None = None,
        page_source: str = "",
    ) -> None:
        from mineai.preview import preview_selection_key

        source = getattr(issue, "source", "") if issue is not None else ""
        source = source or page_source or "Текстовый блок"
        status = t(
            "preview.status_problem"
            if issue is not None and issue.kind in {"untranslated", "structure", "missing"}
            else "preview.status_translated"
        )
        prefix = f"{t('preview.page')} {page_index + 1}: " if page_index is not None else ""
        display_source = _strip_mc_codes(source) or "Текстовый блок"
        row = QTreeWidgetItem(
            parent,
            (
                prefix + _preview_text(display_source),
                status,
                _preview_text(_strip_mc_codes(source), 240),
            ),
        )
        row.setToolTip(0, source)
        row.setData(0, Qt.ItemDataRole.UserRole, preview_selection_key(logical_path, unit_id))
        checked = issue is not None and issue.kind in {"untranslated", "structure", "missing"}
        row.setCheckState(0, Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)

    @staticmethod
    def _set_tree_checks(tree: QTreeWidget, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        def visit(item: QTreeWidgetItem) -> None:
            if item.childCount() == 0:
                item.setCheckState(0, state)
            for index in range(item.childCount()):
                visit(item.child(index))

        for index in range(tree.topLevelItemCount()):
            visit(tree.topLevelItem(index))

    def _selected_keys(self) -> set[str]:
        selected: set[str] = set()

        def visit(item: QTreeWidgetItem) -> None:
            if item.childCount() == 0 and item.checkState(0) == Qt.CheckState.Checked:
                key = item.data(0, Qt.ItemDataRole.UserRole)
                if key:
                    selected.add(str(key))
            for index in range(item.childCount()):
                visit(item.child(index))

        for tree in self._selection_trees:
            for index in range(tree.topLevelItemCount()):
                visit(tree.topLevelItem(index))
        return selected

    def _retranslate_selected(self) -> None:
        keys = self._selected_keys()
        selected = self.report.units_for_selection(keys)
        if not selected:
            QMessageBox.information(self, t("preview.title"), t("preview.no_selection"))
            return
        if not callable(self._on_retranslate):
            QMessageBox.information(self, t("preview.title"), t("preview.retranslate_unavailable"))
            return
        if self._on_retranslate(selected):
            self.accept()

    def _save_report(self) -> None:
        path, selected_filter = QFileDialog.getSaveFileName(
            self,
            t("preview.save_title"),
            "MineAI_Beta45_preview.txt",
            "Текстовый отчёт (*.txt);;JSON (*.json);;HTML (*.html)",
        )
        if not path:
            return
        try:
            if selected_filter and "JSON" in selected_filter:
                text = self.report.to_json()
            elif selected_filter and "HTML" in selected_filter:
                from mineai.preview import render_preview_html

                text = render_preview_html(self.report)
            else:
                text = self.report.to_text()
            with open(path, "w", encoding="utf-8-sig", newline="\n") as handle:
                handle.write(text)
        except OSError as exc:
            QMessageBox.critical(self, t("error.title"), str(exc))


class AnalysisSelectionDialog(QDialog):
    """Dedicated tree editor for analysis targets and quest subgroups."""

    def __init__(self, items, selected_keys, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(t("analysis.dialog_title"))
        self.resize(980, 680)
        self.setMinimumSize(760, 520)
        self._items = {item.key: item for item in items}

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        title = QLabel(t("analysis.dialog_title"))
        title.setObjectName("DialogTitle")
        root.addWidget(title)
        subtitle = QLabel(t("analysis.dialog_hint"))
        subtitle.setObjectName("MutedLabel")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(t("analysis.search"))
        self.type_filter = QComboBox()
        for label, scope in (
            (t("analysis.filter_all"), "all"),
            (t("analysis.filter_mods"), "mods"),
            (t("analysis.filter_books"), "books"),
            (t("analysis.filter_quests"), "quests"),
        ):
            self.type_filter.addItem(label, scope)
        select_all = QPushButton(t("analysis.select_all"))
        select_none = QPushButton(t("analysis.select_none"))
        filters.addWidget(self.search, 1)
        filters.addWidget(self.type_filter)
        filters.addWidget(select_all)
        filters.addWidget(select_none)
        root.addLayout(filters)

        self.tree = QTreeWidget()
        self.tree.setObjectName("TranslationSelectionTree")
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(
            (
                t("analysis.check"),
                t("analysis.item"),
                t("analysis.type"),
                t("analysis.progress"),
            )
        )
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.tree.setColumnWidth(0, 105)
        self.tree.setColumnWidth(1, 430)
        self.tree.setColumnWidth(2, 150)
        root.addWidget(self.tree, 1)

        rows: dict[str, QTreeWidgetItem] = {}
        ordered_items = list(items)
        for item in ordered_items:
            if item.parent_key is not None:
                continue
            row = QTreeWidgetItem(self.tree)
            rows[item.key] = row
            self._configure_row(row, item, selected_keys)
        for item in ordered_items:
            if item.parent_key is None:
                continue
            parent_row = rows.get(item.parent_key)
            if parent_row is None:
                parent_row = QTreeWidgetItem(self.tree)
            row = QTreeWidgetItem(parent_row)
            rows[item.key] = row
            self._configure_row(row, item, selected_keys)

        for item in ordered_items:
            if not item.is_group:
                continue
            row = rows.get(item.key)
            if row is None:
                continue
            row.setFlags(
                row.flags()
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsAutoTristate
            )
            checked_children = sum(
                row.child(index).checkState(0) == Qt.CheckState.Checked
                for index in range(row.childCount())
            )
            if checked_children == row.childCount() and row.childCount():
                row.setCheckState(0, Qt.CheckState.Checked)
            elif checked_children:
                row.setCheckState(0, Qt.CheckState.PartiallyChecked)
            else:
                row.setCheckState(0, Qt.CheckState.Unchecked)
            row.setExpanded(True)

        self.summary = QLabel()
        self.summary.setObjectName("SelectionSummary")
        root.addWidget(self.summary)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(t("analysis.apply"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(t("analysis.cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self.search.textChanged.connect(self._apply_filters)
        self.type_filter.currentIndexChanged.connect(self._apply_filters)
        self.tree.itemChanged.connect(lambda *_args: self._update_summary())
        select_all.clicked.connect(lambda: self._set_all(True))
        select_none.clicked.connect(lambda: self._set_all(False))
        self._update_summary()

    @staticmethod
    def _configure_row(row, item, selected_keys) -> None:
        row.setData(0, Qt.ItemDataRole.UserRole, item.key)
        row.setData(0, Qt.ItemDataRole.UserRole + 1, item.scope)
        row.setText(1, f"{item.icon} {item.name}")
        row.setToolTip(1, item.path)
        row.setText(2, item.kind)
        row.setText(3, f"{item.translated}/{item.total} · {item.percent}%")
        if not item.is_group:
            row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            row.setCheckState(
                0,
                Qt.CheckState.Checked
                if item.key in selected_keys
                else Qt.CheckState.Unchecked,
            )

    def _leaf_rows(self):
        for index in range(self.tree.topLevelItemCount()):
            root = self.tree.topLevelItem(index)
            if root.childCount():
                for child_index in range(root.childCount()):
                    yield root.child(child_index)
            else:
                yield root

    def selected_keys(self) -> frozenset[str]:
        return frozenset(
            row.data(0, Qt.ItemDataRole.UserRole)
            for row in self._leaf_rows()
            if row.checkState(0) == Qt.CheckState.Checked
        )

    def _set_all(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self.tree.blockSignals(True)
        try:
            for row in self._leaf_rows():
                row.setCheckState(0, state)
        finally:
            self.tree.blockSignals(False)
        self._update_summary()

    def _apply_filters(self) -> None:
        query = self.search.text().strip().casefold()
        scope = self.type_filter.currentData() or "all"
        for index in range(self.tree.topLevelItemCount()):
            root = self.tree.topLevelItem(index)
            root_match = scope in ("all", root.data(0, Qt.ItemDataRole.UserRole + 1))
            visible_children = 0
            for child_index in range(root.childCount()):
                child = root.child(child_index)
                text_match = query in " ".join(
                    child.text(column) for column in range(1, 4)
                ).casefold()
                visible = root_match and text_match
                child.setHidden(not visible)
                visible_children += int(visible)
            if root.childCount():
                root_text_match = query in " ".join(
                    root.text(column) for column in range(1, 4)
                ).casefold()
                root.setHidden(not root_match or (not root_text_match and not visible_children))
            else:
                text_match = query in " ".join(
                    root.text(column) for column in range(1, 4)
                ).casefold()
                root.setHidden(not root_match or not text_match)

    def _update_summary(self) -> None:
        selected = len(self.selected_keys())
        total = sum(1 for _row in self._leaf_rows())
        self.summary.setText(t("analysis.selected_summary", selected=selected, total=total))


class SettingsDialog(QDialog):
    def __init__(self, config, on_saved, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self.on_saved = on_saved
        self.setWindowTitle(t("settings.title"))
        self.resize(760, 720)
        self.setMinimumSize(650, 600)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)

        ai_tab, ai_layout = self._scroll_tab()
        lm_tab, lm_layout = self._scroll_tab()
        ollama_tab, ollama_layout = self._scroll_tab()
        llama_tab, llama_layout = self._scroll_tab()
        or_tab, or_layout = self._scroll_tab()
        general_tab, general_layout = self._scroll_tab()
        self.tabs.addTab(ai_tab, t("settings.tab.local"))
        self.tabs.addTab(lm_tab, t("settings.tab.lmstudio"))
        self.tabs.addTab(ollama_tab, t("settings.tab.ollama"))
        self.tabs.addTab(llama_tab, t("settings.tab.llama"))
        self.tabs.addTab(or_tab, t("settings.tab.openrouter"))
        self.tabs.addTab(general_tab, t("settings.tab.general"))

        self.ai_exe = self._file_row(ai_layout, t("settings.local_exe"), config.get("AI", "exe_path"), "Executables (*.exe)")
        self.ai_model = self._file_row(ai_layout, t("settings.model"), config.get("AI", "model_path"), "GGUF Models (*.gguf)")
        self.gpu_layers = self._slider_row(ai_layout, t("settings.gpu_layers"), config.getint("AI", "gpu_layers", 99), 0, 99)
        ai_layout.addStretch(1)

        lm_note = QLabel(t("settings.lm_note"))
        lm_note.setObjectName("MutedLabel")
        lm_note.setWordWrap(True)
        lm_layout.addWidget(lm_note)
        self.lm_url = self._line_row(
            lm_layout,
            t("settings.lm_url"),
            config.get("LMSTUDIO", "base_url"),
        )
        self.lm_key = self._line_row(
            lm_layout,
            t("settings.lm_key"),
            config.get("LMSTUDIO", "api_key"),
            secret=True,
        )
        lm_layout.addWidget(self._field_label(t("settings.model_id")))
        self.lm_model = QComboBox()
        self.lm_model.setEditable(True)
        self.lm_model.setCurrentText(config.get("LMSTUDIO", "model"))
        lm_layout.addWidget(self.lm_model)
        lm_actions = QHBoxLayout()
        self.lm_refresh = QPushButton(t("settings.lm_refresh"))
        self.lm_test = QPushButton(t("settings.lm_test"))
        lm_actions.addWidget(self.lm_refresh)
        lm_actions.addWidget(self.lm_test)
        lm_actions.addStretch(1)
        lm_layout.addLayout(lm_actions)
        self.lm_status = QLabel(t("settings.lm_idle"))
        self.lm_status.setObjectName("MutedLabel")
        self.lm_status.setWordWrap(True)
        lm_layout.addWidget(self.lm_status)
        lm_layout.addStretch(1)
        self._lmstudio_signals = LmStudioSignals(self)
        self._lmstudio_signals.finished.connect(self._lmstudio_probe_finished)
        self._lmstudio_worker: threading.Thread | None = None
        self.lm_refresh.clicked.connect(self._start_lmstudio_probe)
        self.lm_test.clicked.connect(self._start_lmstudio_probe)

        ollama_note = QLabel(t("settings.ollama_note"))
        ollama_note.setObjectName("MutedLabel")
        ollama_note.setWordWrap(True)
        ollama_layout.addWidget(ollama_note)
        self.ollama_url = self._line_row(
            ollama_layout,
            t("settings.ollama_url"),
            config.get("OLLAMA", "base_url"),
        )
        self.ollama_key = self._line_row(
            ollama_layout,
            t("settings.ollama_key"),
            config.get("OLLAMA", "api_key"),
            secret=True,
        )
        ollama_layout.addWidget(self._field_label(t("settings.model_id")))
        self.ollama_model = QComboBox()
        self.ollama_model.setEditable(True)
        self.ollama_model.setCurrentText(config.get("OLLAMA", "model"))
        ollama_layout.addWidget(self.ollama_model)
        ollama_actions = QHBoxLayout()
        self.ollama_refresh = QPushButton(t("settings.ollama_refresh"))
        self.ollama_test = QPushButton(t("settings.ollama_test"))
        ollama_actions.addWidget(self.ollama_refresh)
        ollama_actions.addWidget(self.ollama_test)
        ollama_actions.addStretch(1)
        ollama_layout.addLayout(ollama_actions)
        self.ollama_status = QLabel(t("settings.ollama_checking"))
        self.ollama_status.setObjectName("MutedLabel")
        self.ollama_status.setWordWrap(True)
        ollama_layout.addWidget(self.ollama_status)
        ollama_layout.addStretch(1)
        self._ollama_signals = ProviderSignals(self)
        self._ollama_signals.finished.connect(self._ollama_probe_finished)
        self._ollama_worker: threading.Thread | None = None
        self.ollama_refresh.clicked.connect(self._start_ollama_probe)
        self.ollama_test.clicked.connect(self._start_ollama_probe)

        llama_note = QLabel(t("settings.llama_note"))
        llama_note.setObjectName("MutedLabel")
        llama_note.setWordWrap(True)
        llama_layout.addWidget(llama_note)
        self.llama_url = self._line_row(
            llama_layout,
            t("settings.llama_url"),
            config.get("LLAMA", "base_url"),
        )
        self.llama_key = self._line_row(
            llama_layout,
            t("settings.llama_key"),
            config.get("LLAMA", "api_key"),
            secret=True,
        )
        llama_layout.addWidget(self._field_label(t("settings.model_id")))
        self.llama_model = QComboBox()
        self.llama_model.setEditable(True)
        self.llama_model.setCurrentText(config.get("LLAMA", "model"))
        llama_layout.addWidget(self.llama_model)
        llama_actions = QHBoxLayout()
        self.llama_refresh = QPushButton(t("settings.llama_refresh"))
        self.llama_test = QPushButton(t("settings.llama_test"))
        llama_actions.addWidget(self.llama_refresh)
        llama_actions.addWidget(self.llama_test)
        llama_actions.addStretch(1)
        llama_layout.addLayout(llama_actions)
        self.llama_status = QLabel(t("settings.llama_checking"))
        self.llama_status.setObjectName("MutedLabel")
        self.llama_status.setWordWrap(True)
        llama_layout.addWidget(self.llama_status)
        llama_layout.addStretch(1)
        self._llama_signals = ProviderSignals(self)
        self._llama_signals.finished.connect(self._llama_probe_finished)
        self._llama_worker: threading.Thread | None = None
        self.llama_refresh.clicked.connect(self._start_llama_probe)
        self.llama_test.clicked.connect(self._start_llama_probe)

        note = QLabel(t("settings.or_note"))
        note.setObjectName("MutedLabel")
        note.setWordWrap(True)
        or_layout.addWidget(note)
        self.or_url = self._line_row(or_layout, t("settings.api_url"), config.get("OPENROUTER", "api_url"))
        self.or_key = self._line_row(or_layout, t("settings.or_key"), config.get("OPENROUTER", "api_key"), secret=True)
        self.or_model = self._line_row(or_layout, t("settings.model_id"), config.get("OPENROUTER", "model") or DEFAULT_OPENROUTER_MODEL)
        self.or_site = self._line_row(or_layout, t("settings.site_url"), config.get("OPENROUTER", "site_url"))
        self.or_app = self._line_row(or_layout, t("settings.app_title"), config.get("OPENROUTER", "app_name"))
        or_layout.addStretch(1)

        smart_row = QHBoxLayout()
        self.smart_glue = QCheckBox(t("settings.smart_glue"))
        self.smart_glue.setChecked(config.getboolean("GENERAL", "smart_glue"))
        smart_row.addWidget(self.smart_glue)
        smart_row.addWidget(HelpMarker(t("tooltip.smart_glue")))
        smart_row.addStretch(1)
        general_layout.addLayout(smart_row)
        self.ai_retries = self._spin_row(general_layout, t("settings.ai_retries"), config.getint("AI", "ai_retries", 3), 0, 5)
        self.google_workers = self._spin_row(general_layout, t("settings.google_workers"), config.getint("GENERAL", "google_workers", 5), 1, 10)
        self.deepl_key = self._line_row(general_layout, t("settings.deepl"), config.get("API", "deepl_key"), secret=True)
        general_layout.addStretch(1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = QPushButton(t("button.cancel"))
        save = QPushButton(t("button.save_settings"))
        save.setObjectName("PrimaryButton")
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self._save)
        actions.addWidget(cancel)
        actions.addWidget(save)
        root.addLayout(actions)

    @staticmethod
    def _scroll_tab():
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        root_layout.addWidget(scroll)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        scroll.setWidget(content)
        return root, layout

    @staticmethod
    def _field_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("FieldLabel")
        return label

    def _line_row(self, layout, label: str, value: str, *, secret: bool = False) -> QLineEdit:
        layout.addWidget(self._field_label(label))
        edit = QLineEdit(value)
        if secret:
            edit.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(edit)
        return edit

    def _file_row(self, layout, label: str, value: str, file_filter: str) -> QLineEdit:
        layout.addWidget(self._field_label(label))
        row = QHBoxLayout()
        edit = QLineEdit(value)
        browse = QPushButton(t("button.browse"))
        browse.setFixedWidth(88)
        browse.clicked.connect(lambda: self._browse_file(edit, file_filter))
        row.addWidget(edit, 1)
        row.addWidget(browse)
        layout.addLayout(row)
        return edit

    def _slider_row(self, layout, label: str, value: int, minimum: int, maximum: int) -> QSlider:
        value_label = self._field_label(f"{label}: {value}")
        layout.addWidget(value_label)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(minimum, maximum)
        slider.setValue(value)
        slider.valueChanged.connect(lambda v: value_label.setText(f"{label}: {v}"))
        layout.addWidget(slider)
        return slider

    def _spin_row(self, layout, label: str, value: int, minimum: int, maximum: int) -> QSpinBox:
        layout.addWidget(self._field_label(label))
        spin = ScrollSafeSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        layout.addWidget(spin)
        return spin

    def _browse_file(self, edit: QLineEdit, file_filter: str) -> None:
        path, _ = QFileDialog.getOpenFileName(self, t("dialog.choose_file"), edit.text(), file_filter)
        if path:
            edit.setText(path)

    def _start_lmstudio_probe(self, *_args) -> None:
        if self._lmstudio_worker and self._lmstudio_worker.is_alive():
            return
        self.lm_refresh.setEnabled(False)
        self.lm_test.setEnabled(False)
        self.lm_status.setText(t("settings.lm_checking"))
        base_url = self.lm_url.text()
        api_key = self.lm_key.text()

        def task() -> None:
            try:
                models = list_loaded_lmstudio_models(base_url, api_key=api_key)
            except Exception as exc:
                self._lmstudio_signals.finished.emit(False, [], str(exc))
            else:
                self._lmstudio_signals.finished.emit(True, models, "")

        self._lmstudio_worker = threading.Thread(target=task, daemon=True)
        self._lmstudio_worker.start()

    def _start_ollama_probe(self, *_args) -> None:
        if self._ollama_worker and self._ollama_worker.is_alive():
            return
        self.ollama_refresh.setEnabled(False)
        self.ollama_test.setEnabled(False)
        self.ollama_status.setText(t("settings.ollama_checking"))
        base_url = self.ollama_url.text()
        api_key = self.ollama_key.text()

        def task() -> None:
            try:
                models = list_loaded_ollama_models(base_url, api_key=api_key)
            except Exception as exc:
                self._ollama_signals.finished.emit(False, [], str(exc))
            else:
                self._ollama_signals.finished.emit(True, models, "")

        self._ollama_worker = threading.Thread(target=task, daemon=True)
        self._ollama_worker.start()

    def _start_llama_probe(self, *_args) -> None:
        if self._llama_worker and self._llama_worker.is_alive():
            return
        self.llama_refresh.setEnabled(False)
        self.llama_test.setEnabled(False)
        self.llama_status.setText(t("settings.llama_checking"))
        base_url = self.llama_url.text()
        api_key = self.llama_key.text()

        def task() -> None:
            try:
                models = list_llama_models(base_url, api_key=api_key)
            except Exception as exc:
                self._llama_signals.finished.emit(False, [], str(exc))
            else:
                self._llama_signals.finished.emit(True, models, "")

        self._llama_worker = threading.Thread(target=task, daemon=True)
        self._llama_worker.start()

    def _lmstudio_probe_finished(
        self,
        success: bool,
        models: list[str],
        error: str,
    ) -> None:
        self._lmstudio_worker = None
        self.lm_refresh.setEnabled(True)
        self.lm_test.setEnabled(True)
        if not success:
            self.lm_status.setText(t("settings.lm_error", error=error))
            return

        current = self.lm_model.currentText().strip()
        self.lm_model.clear()
        self.lm_model.addItems(models)
        if current in models:
            self.lm_model.setCurrentText(current)
        elif models:
            self.lm_model.setCurrentIndex(0)
        if models:
            self.lm_status.setText(t("settings.lm_models_found", count=len(models)))
        else:
            self.lm_status.setText(t("settings.lm_no_models"))

    @staticmethod
    def _set_provider_models(combo, models: list[str]) -> None:
        current = combo.currentText().strip()
        combo.clear()
        combo.addItems(models)
        if current in models:
            combo.setCurrentText(current)
        elif models:
            combo.setCurrentIndex(0)

    def _ollama_probe_finished(
        self,
        success: bool,
        models: list[str],
        error: str,
    ) -> None:
        self._ollama_worker = None
        self.ollama_refresh.setEnabled(True)
        self.ollama_test.setEnabled(True)
        if not success:
            self.ollama_status.setText(t("settings.ollama_error", error=error))
            return
        self._set_provider_models(self.ollama_model, models)
        self.ollama_status.setText(
            t("settings.ollama_models_found", count=len(models))
            if models
            else t("settings.ollama_no_models")
        )

    def _llama_probe_finished(
        self,
        success: bool,
        models: list[str],
        error: str,
    ) -> None:
        self._llama_worker = None
        self.llama_refresh.setEnabled(True)
        self.llama_test.setEnabled(True)
        if not success:
            self.llama_status.setText(t("settings.llama_error", error=error))
            return
        self._set_provider_models(self.llama_model, models)
        self.llama_status.setText(
            t("settings.llama_models_found", count=len(models))
            if models
            else t("settings.llama_no_models")
        )

    def _save(self) -> None:
        self.config.set_many("AI", {
            "exe_path": self.ai_exe.text(),
            "model_path": self.ai_model.text(),
            "gpu_layers": self.gpu_layers.value(),
            "ai_retries": self.ai_retries.value(),
        })
        self.config.set_many("OPENROUTER", {
            "api_key": self.or_key.text(),
            "api_url": self.or_url.text().strip(),
            "model": self.or_model.text().strip(),
            "site_url": self.or_site.text().strip(),
            "app_name": self.or_app.text().strip(),
        })
        self.config.set_many("LMSTUDIO", {
            "base_url": normalize_lmstudio_base_url(self.lm_url.text()),
            "api_key": self.lm_key.text().strip(),
            "model": self.lm_model.currentText().strip(),
        })
        self.config.set_many("OLLAMA", {
            "base_url": normalize_ollama_base_url(self.ollama_url.text()),
            "api_key": self.ollama_key.text().strip(),
            "model": self.ollama_model.currentText().strip(),
        })
        self.config.set_many("LLAMA", {
            "base_url": normalize_llama_base_url(self.llama_url.text()),
            "api_key": self.llama_key.text().strip(),
            "model": self.llama_model.currentText().strip(),
        })
        self.config.set_many("GENERAL", {
            "smart_glue": self.smart_glue.isChecked(),
            "google_workers": self.google_workers.value(),
        })
        self.config.set_many("API", {"deepl_key": self.deepl_key.text()})
        self.on_saved()
        self.accept()


class PromptEditorDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(t("prompts.title"))
        self.resize(940, 680)
        self.setMinimumSize(720, 520)
        self._dirty = False
        self.prompts = load_prompts()
        defaults = get_default_prompts()

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)
        tabs = QTabWidget()
        root.addWidget(tabs, 1)

        self.editors: dict[str, QPlainTextEdit] = {}
        for key, title in (
            ("mods", t("prompts.mods")),
            ("books", t("prompts.books")),
            ("quests", t("prompts.quests")),
        ):
            editor = self._prompt_tab(tabs, title, t("prompts.note"))
            editor.setPlainText(self.prompts.get(key, defaults[key]))
            self.editors[key] = editor

        tech = self._prompt_tab(
            tabs,
            t("prompts.technical"),
            t("prompts.tech_note"),
            danger=True,
        )
        tech.setPlainText(self.prompts.get("technical", defaults["technical"]))
        self.editors["technical"] = tech

        for editor in self.editors.values():
            editor.textChanged.connect(self._mark_dirty)

        footer = QHBoxLayout()
        self.dirty_label = QLabel(t("prompts.saved"))
        self.dirty_label.setObjectName("MutedLabel")
        footer.addWidget(self.dirty_label, 1)
        reset = QPushButton(t("button.reset"))
        reset.setObjectName("DangerButton")
        save = QPushButton(t("button.save_prompt"))
        save.setObjectName("PrimaryButton")
        reset.clicked.connect(self._reset)
        save.clicked.connect(lambda: self._save(close=True))
        footer.addWidget(reset)
        footer.addWidget(save)
        root.addLayout(footer)
        self._dirty = False

    @staticmethod
    def _prompt_tab(tabs: QTabWidget, title: str, note: str, *, danger: bool = False) -> QPlainTextEdit:
        page = QWidget()
        layout = QVBoxLayout(page)
        note_label = QLabel(note)
        note_label.setWordWrap(True)
        note_label.setObjectName("DangerText" if danger else "MutedLabel")
        editor = QPlainTextEdit()
        layout.addWidget(note_label)
        layout.addWidget(editor, 1)
        tabs.addTab(page, title)
        return editor

    def _mark_dirty(self) -> None:
        self._dirty = True
        self.dirty_label.setText(t("prompts.dirty"))
        self.dirty_label.setObjectName("WarningText")
        self.dirty_label.style().unpolish(self.dirty_label)
        self.dirty_label.style().polish(self.dirty_label)

    def _reset(self) -> None:
        answer = QMessageBox.question(
            self,
            t("prompts.reset_title"),
            t("prompts.reset_text"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        defaults = get_default_prompts()
        for key, editor in self.editors.items():
            editor.setPlainText(defaults[key])
        self._mark_dirty()

    def _save(self, *, close: bool) -> None:
        for key, editor in self.editors.items():
            self.prompts[key] = editor.toPlainText().strip()
        save_prompts(self.prompts)
        self._dirty = False
        self.dirty_label.setText(t("prompts.saved"))
        self.dirty_label.setObjectName("MutedLabel")
        if close:
            self.accept()

    def closeEvent(self, event) -> None:
        if not self._dirty:
            event.accept()
            return
        box = QMessageBox(self)
        box.setWindowTitle(t("prompts.close_title"))
        box.setText(t("prompts.close_text"))
        box.setStandardButtons(
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel
        )
        answer = box.exec()
        if answer == QMessageBox.StandardButton.Cancel:
            event.ignore()
        elif answer == QMessageBox.StandardButton.Save:
            self._save(close=False)
            event.accept()
        else:
            event.accept()


class MigrationDialog(QDialog):
    def __init__(self, mc_dir: str, lang_label: str, cache_std, cache_ai, log_callback, parent=None, *, initial_zip: str | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(t("migration.title"))
        self.resize(650, 500)
        self.setMinimumSize(560, 430)
        self.mc_dir = mc_dir
        self.lang_api_code = LANGUAGES[lang_label]["api"]
        self.cache_std = cache_std
        self.cache_ai = cache_ai
        self.log_callback = log_callback
        self.signals = MigrationSignals()
        self.signals.finished.connect(self._show_result)
        self._worker: threading.Thread | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(11)

        title = QLabel(t("migration.heading"))
        title.setObjectName("AppTitle")
        root.addWidget(title)
        note = QLabel(t("migration.note"))
        note.setObjectName("MutedLabel")
        note.setWordWrap(True)
        root.addWidget(note)

        label = QLabel(t("migration.resource_pack"))
        label.setObjectName("FieldLabel")
        root.addWidget(label)
        zip_row = QHBoxLayout()
        self.zip_edit = QLineEdit(initial_zip or "")
        browse = QPushButton(t("button.browse"))
        browse.clicked.connect(self._browse)
        zip_row.addWidget(self.zip_edit, 1)
        zip_row.addWidget(browse)
        root.addLayout(zip_row)

        cache_label = QLabel(t("migration.destination"))
        cache_label.setObjectName("FieldLabel")
        root.addWidget(cache_label)
        self.ai_radio = QRadioButton(t("migration.ai"))
        self.std_radio = QRadioButton(t("migration.std"))
        self.ai_radio.setChecked(True)
        group = QButtonGroup(self)
        group.addButton(self.ai_radio)
        group.addButton(self.std_radio)
        root.addWidget(self.ai_radio)
        root.addWidget(self.std_radio)

        self.result = QFrame()
        self.result.setObjectName("InnerCard")
        result_layout = QVBoxLayout(self.result)
        self.result_title = QLabel("")
        self.result_title.setObjectName("StrongLabel")
        self.result_details = QLabel("")
        self.result_details.setObjectName("MutedLabel")
        self.result_details.setWordWrap(True)
        result_layout.addWidget(self.result_title)
        result_layout.addWidget(self.result_details)
        self.result.hide()
        root.addWidget(self.result)
        root.addStretch(1)

        self.run_button = QPushButton(t("migration.run"))
        self.run_button.setObjectName("PrimaryButton")
        self.run_button.clicked.connect(self._run)
        root.addWidget(self.run_button)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, t("migration.resource_pack"), self.zip_edit.text(), "ZIP Archives (*.zip)")
        if path:
            self.zip_edit.setText(path)

    def _run(self) -> None:
        path = self.zip_edit.text().strip()
        if not path or not os.path.exists(path):
            QMessageBox.critical(self, t("error.title"), t("migration.invalid_zip"))
            return
        if self._worker and self._worker.is_alive():
            return
        cache_type = "ai" if self.ai_radio.isChecked() else "std"
        self.run_button.setEnabled(False)
        self.run_button.setText(t("migration.running"))
        self.result.hide()

        def task() -> None:
            count = 0
            error = None
            try:
                count = run_migration(path, self.mc_dir, cache_type, self.lang_api_code, self.log_callback)
                if count > 0:
                    if cache_type == "ai":
                        self.cache_ai.load_imported_caches()
                    else:
                        self.cache_std.load_imported_caches()
            except Exception:
                error = traceback.format_exc()
            self.signals.finished.emit(count, error, cache_type)

        self._worker = threading.Thread(target=task, daemon=False)
        self._worker.start()

    def _show_result(self, count: int, error, cache_type: str) -> None:
        self.result.show()
        if error:
            self.result_title.setText(t("migration.error"))
            self.result_title.setObjectName("DangerText")
            self.result_details.setText(str(error))
        else:
            destination = t("migration.destination_ai") if cache_type == "ai" else t("migration.destination_std")
            self.result_title.setText(t("migration.done"))
            self.result_title.setObjectName("ReadyText")
            self.result_details.setText(t("migration.result", count=count, destination=destination))
        self.result_title.style().unpolish(self.result_title)
        self.result_title.style().polish(self.result_title)
        self.run_button.setEnabled(True)
        self.run_button.setText(t("migration.rerun"))

    def closeEvent(self, event) -> None:
        if self._worker and self._worker.is_alive():
            QMessageBox.information(
                self,
                t("migration.busy_title"),
                t("migration.busy_text"),
            )
            event.ignore()
            return
        event.accept()
