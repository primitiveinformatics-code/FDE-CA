"""Side-panel section navigation.

Replaces the old vertical stack of always-visible `CollapsibleGroupBox`
sections (Statements / Run settings / Log output / Token usage & cost)
with a single side panel: a narrow nav rail of section buttons plus a
content area (`QStackedWidget`) that shows whichever one section is
currently selected. Only one section is visible at a time, so the
window isn't crowded by every section's controls simultaneously.

The panel can be driven two ways:
- By the user, clicking a nav button (or the collapse/expand toggle).
- Automatically by `MainWindow`, calling `select_page()` in response to
  app-state transitions (e.g. the first file being added, or a run
  starting) so the relevant section comes forward without the user
  having to go find it.
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

_RAIL_WIDTH = 150

_NAV_BUTTON_STYLE = """
QPushButton { text-align: left; padding: 6px 8px; border: none; }
QPushButton:checked { background-color: palette(highlight); color: palette(highlighted-text); }
"""


class SidePanel(QWidget):
    # Emitted after the content stack's visibility changes, carrying the
    # rail-only width the container (e.g. a QSplitter) should collapse
    # this panel to - since a splitter doesn't shrink a pane on its own
    # just because the pane's own content became invisible.
    collapsed_changed = Signal(bool, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pages: dict[str, QWidget] = {}
        self._nav_buttons: dict[str, QPushButton] = {}
        self._collapsed = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        outer.addLayout(body)

        rail = QWidget()
        rail.setFixedWidth(_RAIL_WIDTH)
        rail_layout = QVBoxLayout(rail)
        rail_layout.setContentsMargins(4, 4, 4, 4)
        rail_layout.setSpacing(2)

        self._toggle_btn = QPushButton("◀  Hide panel")
        self._toggle_btn.setToolTip("Collapse or expand this side panel")
        self._toggle_btn.clicked.connect(self.toggle_collapsed)
        rail_layout.addWidget(self._toggle_btn)
        rail_layout.addSpacing(6)

        self._nav_layout = QVBoxLayout()
        self._nav_layout.setSpacing(2)
        rail_layout.addLayout(self._nav_layout)
        rail_layout.addStretch(1)

        self._stack = QStackedWidget()
        self._stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        body.addWidget(rail)
        body.addWidget(self._stack, stretch=1)

        self._rail = rail

    def add_page(self, key: str, title: str, widget: QWidget) -> None:
        btn = QPushButton(title)
        btn.setCheckable(True)
        btn.setStyleSheet(_NAV_BUTTON_STYLE)
        btn.clicked.connect(lambda _checked=False, k=key: self.select_page(k))
        self._nav_layout.addWidget(btn)
        self._nav_buttons[key] = btn

        self._stack.addWidget(widget)
        self._pages[key] = widget

        if len(self._pages) == 1:
            self.select_page(key)

    def select_page(self, key: str) -> None:
        """Show `key`'s page, expanding the panel first if it was
        collapsed. Safe to call both from user clicks and from
        `MainWindow`'s automatic state-driven navigation."""
        if key not in self._pages:
            return
        if self._collapsed:
            self.set_collapsed(False)
        self._stack.setCurrentWidget(self._pages[key])
        for k, btn in self._nav_buttons.items():
            btn.setChecked(k == key)

    def current_page(self) -> str | None:
        for key, widget in self._pages.items():
            if widget is self._stack.currentWidget():
                return key
        return None

    def toggle_collapsed(self) -> None:
        self.set_collapsed(not self._collapsed)

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = collapsed
        self._stack.setVisible(not collapsed)
        self._toggle_btn.setText("▶  Show panel" if collapsed else "◀  Hide panel")
        self.collapsed_changed.emit(collapsed, self._rail.sizeHint().width())
