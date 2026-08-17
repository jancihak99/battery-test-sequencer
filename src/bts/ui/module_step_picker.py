"""Two dependent dropdowns for step-file selection: module type -> step file.

Reused by the main toolbar and the Simulate tab. Step files (programs) are grouped
by their ``meta.module_profile``; the top combo picks the module type (category) and
the bottom combo lists that type's step files as a sub-category.

When ``allow_delete`` is set, each row in the OPEN dropdown carries a small trash
icon: click it on a step file to archive that step file, or on an empty module type
to archive that category. (Deletion lives inside the dropdown, not beside it.)
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QRect, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QWidget,
)

from bts.models.program import ModuleGroup
from bts.ui.theme import TEXT_DIM

# assets/trash.svg (module_step_picker.py → ui → bts → src → root)
_TRASH_SVG = Path(__file__).resolve().parents[3] / "assets" / "trash.svg"
_ICON_COL_W = 26  # right-hand strip in each dropdown row reserved for the trash icon
_UROLE = Qt.ItemDataRole.UserRole


def _same(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except Exception:
        return str(a) == str(b)


class _DownComboBox(QComboBox):
    """QComboBox whose popup always drops straight down below the box.

    A stylesheet'd QComboBox uses the menu-style popup that Qt aligns to the current
    item (so it opens centered / upward). We re-anchor the popup container to the
    box's bottom edge after it is shown.
    """

    def showPopup(self) -> None:  # noqa: N802
        super().showPopup()
        view = self.view()
        container = view.parentWidget() or view
        below = self.mapToGlobal(self.rect().bottomLeft())
        container.move(below.x(), below.y())


class _TrashPaintDelegate(QStyledItemDelegate):
    """Paints a trash icon on the right of deletable dropdown rows (click handled
    by the picker's event filter)."""

    def __init__(self, icon: QIcon, can_delete, parent=None) -> None:
        super().__init__(parent)
        self._icon = icon
        self._can_delete = can_delete  # callable(QModelIndex) -> bool

    def paint(self, painter, option, index) -> None:
        deletable = self._can_delete(index)
        opt = QStyleOptionViewItem(option)
        if deletable:
            opt.rect = opt.rect.adjusted(0, 0, -_ICON_COL_W, 0)  # keep text off the icon
        super().paint(painter, opt, index)
        if deletable and not self._icon.isNull():
            size = 16
            r = option.rect
            x = r.right() - _ICON_COL_W + (_ICON_COL_W - size) // 2
            y = r.top() + (r.height() - size) // 2
            self._icon.paint(painter, QRect(x, y, size, size))

    def sizeHint(self, option, index):
        s = super().sizeHint(option, index)
        if s.height() < 26:
            s.setHeight(26)
        return s


class ModuleStepPicker(QWidget):
    """Module-type + step-file dropdowns.

    Signals (emitted ONLY for user-driven changes, never for programmatic
    ``set_groups`` / ``select_path``):
      * ``selection_changed(object)`` — resolved step-file ``Path`` or ``None``.
      * ``delete_requested(object)``  — step-file ``Path`` (trash icon in step dropdown).
      * ``delete_category_requested(object)`` — empty module id (trash icon in module
        dropdown).

    A ``leading`` label passed to :meth:`set_groups` adds a special first entry in
    the module combo (e.g. "Editor program") that resolves to ``None`` and shows no
    step files — used by the Simulate tab.
    """

    selection_changed = Signal(object)          # Path | None
    delete_requested = Signal(object)           # Path (step file)
    delete_category_requested = Signal(object)  # str (empty module id)

    def __init__(
        self,
        parent=None,
        *,
        allow_delete: bool = False,
        module_label: str = "Typ modulu",
        step_label: str = "Program",
    ) -> None:
        super().__init__(parent)
        self._groups: list[ModuleGroup] = []
        self._leading: str | None = None
        self._suppress = False  # gate signals during programmatic changes
        self._del_map: dict[object, tuple] = {}  # viewport -> (combo, can_delete, on_delete)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        lab_m = QLabel(module_label)
        lab_m.setStyleSheet(f"color:{TEXT_DIM};")
        lab_s = QLabel(step_label)
        lab_s.setStyleSheet(f"color:{TEXT_DIM};")

        self.module_combo = _DownComboBox()
        self.module_combo.setMinimumWidth(150)
        self.module_combo.setToolTip("Typ modulu (kategorie) — filtruje step fily níže.")
        self.step_combo = _DownComboBox()
        self.step_combo.setMinimumWidth(200)
        self.step_combo.setToolTip("Step file pro vybraný typ modulu.")

        row.addWidget(lab_m)
        row.addWidget(self.module_combo, 0)
        row.addWidget(lab_s)
        row.addWidget(self.step_combo, 1)

        if allow_delete:
            self._del_icon = QIcon(str(_TRASH_SVG)) if _TRASH_SVG.exists() else QIcon()
            self.step_combo.setToolTip(
                "Step file pro vybraný typ modulu. V rozbalené roletce klikni na koš "
                "u položky pro smazání (přesun do archivu)."
            )
            self._install_delete(self.step_combo, self._step_deletable, self._delete_step_index)
            self._install_delete(self.module_combo, self._module_deletable, self._delete_module_index)

        self.module_combo.currentIndexChanged.connect(self._on_module_changed)
        self.step_combo.currentIndexChanged.connect(self._on_step_changed)

    # ---- delete wiring (trash icon inside the dropdown) ---------------------

    def _install_delete(self, combo: QComboBox, can_delete, on_delete) -> None:
        combo.setItemDelegate(_TrashPaintDelegate(self._del_icon, can_delete, combo))
        viewport = combo.view().viewport()
        viewport.installEventFilter(self)
        self._del_map[viewport] = (combo, can_delete, on_delete)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        spec = self._del_map.get(obj)
        if spec is not None and event.type() == QEvent.MouseButtonRelease:
            combo, can_delete, on_delete = spec
            index = combo.view().indexAt(event.position().toPoint())
            if (
                index.isValid()
                and can_delete(index)
                and event.position().toPoint().x() >= obj.width() - _ICON_COL_W
            ):
                on_delete(index)
                return True  # consume: don't also select the row
        return super().eventFilter(obj, event)

    def _step_deletable(self, index) -> bool:
        return bool(index.data(_UROLE))

    def _module_deletable(self, index) -> bool:
        mid = index.data(_UROLE)
        if not mid:
            return False
        grp = next((g for g in self._groups if g.module_id == mid), None)
        return grp is not None and not grp.programs  # only empty categories

    def _delete_step_index(self, index) -> None:
        data = index.data(_UROLE)
        self.step_combo.hidePopup()
        if data:
            self.delete_requested.emit(Path(data))

    def _delete_module_index(self, index) -> None:
        data = index.data(_UROLE)
        self.module_combo.hidePopup()
        if data:
            self.delete_category_requested.emit(str(data))

    # ---- population (programmatic; silent) ----------------------------------

    def set_groups(
        self,
        groups: list[ModuleGroup],
        *,
        leading: str | None = None,
        keep_path: Path | None = None,
    ) -> None:
        """Repopulate both combos. Does not emit ``selection_changed``.

        ``keep_path`` (falling back to the current selection) is reselected when still
        present, so a folder refresh doesn't jump the user elsewhere.
        """
        prev = keep_path or self.current_path()
        self._groups = list(groups)
        self._leading = leading
        self._suppress = True
        self.module_combo.blockSignals(True)
        self.module_combo.clear()
        if leading is not None:
            self.module_combo.addItem(leading, None)
        for g in self._groups:
            self.module_combo.addItem(g.label, g.module_id)
        self.module_combo.setCurrentIndex(self._module_row_for_path(prev))
        self.module_combo.blockSignals(False)
        self._populate_steps(reselect=prev, emit=False)
        self._suppress = False

    def select_path(self, path: Path | None) -> None:
        """Programmatically select a step file (no ``selection_changed``)."""
        self._suppress = True
        self.module_combo.blockSignals(True)
        self.module_combo.setCurrentIndex(self._module_row_for_path(path))
        self.module_combo.blockSignals(False)
        self._populate_steps(reselect=path, emit=False)
        self._suppress = False

    # ---- helpers ------------------------------------------------------------

    def _offset(self) -> int:
        return 1 if self._leading is not None else 0

    def _module_row_for_path(self, path: Path | None) -> int:
        if path is not None:
            for gi, g in enumerate(self._groups):
                if any(_same(p, path) for _lbl, p in g.programs):
                    return gi + self._offset()
        return 0

    def _current_group(self) -> ModuleGroup | None:
        mid = self.module_combo.currentData()
        if mid is None:
            return None  # leading (editor) entry
        for g in self._groups:
            if g.module_id == mid:
                return g
        return None

    def _populate_steps(self, *, reselect: Path | None, emit: bool) -> None:
        prev_suppress = self._suppress
        self._suppress = True
        self.step_combo.blockSignals(True)
        self.step_combo.clear()
        group = self._current_group()
        if group is None:
            self.step_combo.setEnabled(False)  # leading (editor) entry: no step files
        else:
            self.step_combo.setEnabled(True)
            for lbl, p in group.programs:
                self.step_combo.addItem(lbl, str(p))
            idx = 0
            if reselect is not None:
                for i in range(self.step_combo.count()):
                    if _same(Path(self.step_combo.itemData(i)), reselect):
                        idx = i
                        break
            if self.step_combo.count():
                self.step_combo.setCurrentIndex(idx)
        self.step_combo.blockSignals(False)
        self._suppress = prev_suppress
        if emit and not self._suppress:
            self.selection_changed.emit(self.current_path())

    # ---- user-driven slots --------------------------------------------------

    def _on_module_changed(self, _idx: int) -> None:
        if self._suppress:
            return
        self._populate_steps(reselect=None, emit=True)

    def _on_step_changed(self, _idx: int) -> None:
        if self._suppress:
            return
        self.selection_changed.emit(self.current_path())

    # ---- query --------------------------------------------------------------

    def current_path(self) -> Path | None:
        if not self.step_combo.isEnabled():
            return None
        data = self.step_combo.currentData()
        return Path(data) if data else None

    def current_module_id(self) -> str | None:
        return self.module_combo.currentData()
