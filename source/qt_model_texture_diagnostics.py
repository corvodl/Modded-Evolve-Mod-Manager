"""Compact, read-only Qt diagnostics for CryEngine model material dependencies.

Thousands of streamed DDS mip parts must not turn a status popup into an
unbounded message box. All source paths remain accessible in tooltips and
through explicit 'Copy full report', but are collapsed by default.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QTreeWidget, QTreeWidgetItem, QHeaderView, QPushButton, QDialogButtonBox,
)


def texture_diagnostic_summary(reports, issues=()):
    """Compute summary without expanding all physical DDS fragment paths."""
    total = found = missing = ambiguous = parts = 0
    for _, report in reports:
        for entry in report.get('textures', ()):
            total += 1
            status = str(entry.get('status', 'missing')).casefold()
            if status == 'found':
                found += 1
            elif status == 'ambiguous':
                ambiguous += 1
            else:
                missing += 1
            parts += len(entry.get('stream_parts') or ())
    return {
        'models': len(reports), 'total': total, 'found': found,
        'missing': missing, 'ambiguous': ambiguous,
        'fragments': parts, 'issues': len(issues),
    }


def full_texture_diagnostic_report(selected, preview, reports, issues=()):
    """Create the complete text only when explicitly copied by the user."""
    lines = ['Model: ' + str(selected), 'Preview: ' + str(preview)]
    for path, report in reports:
        lines.extend(('', str(path),
                      '  Material: ' + str(report.get('material') or '(unresolved)'),
                      '  Status: ' + str(report.get('status', 'missing'))))
        basis = report.get('basis')
        if basis:
            lines.append('  Association: ' + str(basis))
        for nearby in report.get('nearby_materials') or ():
            lines.append('  Nearby material: ' + str(nearby))
        for record in report.get('textures', ()):
            lines.append('  %s: %s — %s' % (
                record.get('map', 'Texture'),
                record.get('reference', ''),
                record.get('status', 'missing')))
            for key, title in (('paths', 'Virtual'), ('archives', 'PAK'),
                               ('physical_paths', 'File')):
                for value in record.get(key) or ():
                    lines.append('    ' + title + ': ' + str(value))
            for frag in record.get('stream_parts') or ():
                lines.append('    DDS .%s: %s | %s' % (
                    frag.get('part', '?'), frag.get('archive', ''),
                    frag.get('physical', '')))
    if issues:
        lines.append('\nMaterial decode issues:')
        lines.extend('  ' + str(issue) for issue in issues)
    return '\n'.join(lines)


class ModelTextureDiagnosticsDialog(QDialog):
    """Bounded window, collapsible dependency tree and searchable texture rows."""

    def __init__(self, selected, preview, reports, issues=(), parent=None):
        super().__init__(parent)
        self.setWindowTitle('Model texture diagnostics')
        self.setWindowFlags(
            self.windowFlags() | Qt.WindowMaximizeButtonHint)
        self.setMinimumSize(580, 360)
        screen = QApplication.primaryScreen()
        usable = screen.availableGeometry() if screen is not None else None
        width = min(900, max(580, int(usable.width() * .78))) if usable else 900
        height = min(580, max(360, int(usable.height() * .68))) if usable else 580
        self.resize(width, height)

        self.selected = str(selected)
        self.preview = str(preview)
        self.reports = tuple(reports)
        self.issues = tuple(issues)
        stats = texture_diagnostic_summary(self.reports, self.issues)
        root = QVBoxLayout(self)
        description = QLabel('Material references from imported and extracted game PAKs')
        root.addWidget(description)
        self.summary = QLabel(
            '%d/%d textures resolved  •  %d missing  •  %d ambiguous  •  '
            '%d DDS fragments  •  %d decode warnings' % (
                stats['found'], stats['total'], stats['missing'],
                stats['ambiguous'], stats['fragments'], stats['issues']))
        self.summary.setWordWrap(True)
        root.addWidget(self.summary)

        controls = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText('Filter model, material, texture, or PAK…')
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        controls.addWidget(self.search, 1)
        self.detail_button = QPushButton('Expand details')
        self.detail_button.clicked.connect(self._toggle_details)
        controls.addWidget(self.detail_button)
        root.addLayout(controls)

        self.tree = QTreeWidget(self)
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(('Model / Material / Texture', 'Status',
                                   'PAK', 'DDS parts'))
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setExpandsOnDoubleClick(True)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.Interactive)
        self.tree.header().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.tree.header().resizeSection(2, 200)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._build_rows()
        root.addWidget(self.tree, 1)

        footer = QHBoxLayout()
        note = QLabel('Click arrows for DDS parts. Full paths are available by copying the report.')
        note.setWordWrap(True)
        footer.addWidget(note, 1)
        copy = QPushButton('Copy full report')
        copy.clicked.connect(self._copy_report)
        footer.addWidget(copy)
        close = QPushButton('Close')
        close.clicked.connect(self.accept)
        footer.addWidget(close)
        root.addLayout(footer)

    def _build_rows(self):
        self.tree.clear()
        for path, report in self.reports:
            material = str(report.get('material') or '(not found)')
            root = QTreeWidgetItem(self.tree, [
                Path(str(path)).name, str(report.get('status', 'missing')),
                str(report.get('material_archive', '')), ''])
            root.setToolTip(0, str(path) + '\nMaterial: ' + material)
            root.setToolTip(1, str(report.get('basis') or ''))
            detail = QTreeWidgetItem(root, ['Material: ' + Path(material).name,
                                             str(report.get('basis', '')),
                                             str(report.get('material_archive', '')), ''])
            detail.setToolTip(0, material)
            for record in report.get('textures', ()):
                fragments = record.get('stream_parts') or ()
                sources = record.get('archives') or ()
                label = str(record.get('map') or 'Texture')
                ref = str(record.get('reference') or '')
                row = QTreeWidgetItem(root, [
                    label + ': ' + Path(ref.replace('\\', '/')).name,
                    str(record.get('status') or 'missing'),
                    ', '.join(str(x) for x in sources[:2]),
                    str(len(fragments)) if fragments else '—'])
                row.setToolTip(0, ref)
                row.setToolTip(2, '\n'.join(str(x) for x in sources))
                physical = record.get('physical_paths') or ()
                if physical:
                    resolved = QTreeWidgetItem(row, [
                        'Resolved virtual path', '', '', ''])
                    resolved.setToolTip(0, '\n'.join(
                        str(x) for x in record.get('paths') or ()))
                    for value in record.get('paths') or ():
                        QTreeWidgetItem(resolved, [str(value), '', '', ''])
                if fragments:
                    fragment_node = QTreeWidgetItem(row, [
                        'DDS mip fragments (%d)' % len(fragments), '', '', ''])
                    for part in fragments:
                        entry = QTreeWidgetItem(fragment_node, [
                            '.%s' % part.get('part', '?'), '',
                            str(part.get('archive', '')), ''])
                        entry.setToolTip(0, str(part.get('physical', '')))
                elif physical:
                    row.setToolTip(3, '\n'.join(str(x) for x in physical))
            nearby = report.get('nearby_materials') or ()
            if nearby and report.get('status') != 'found':
                unresolved = QTreeWidgetItem(root, [
                    '%d nearby material candidates (not automatically assigned)' % len(nearby),
                    'unresolved', '', ''])
                for candidate in nearby[:16]:
                    QTreeWidgetItem(unresolved, [str(candidate), '', '', ''])
            root.setExpanded(True)  # Model-level textures are visible; paths stay collapsed.
        if self.issues:
            warnings = QTreeWidgetItem(self.tree, [
                'Material decode warnings (%d)' % len(self.issues), 'warning', '', ''])
            for message in self.issues[:30]:
                QTreeWidgetItem(warnings, [str(message), '', '', ''])
            warnings.setExpanded(False)

    def _filter(self, value):
        query = value.casefold().strip()
        for i in range(self.tree.topLevelItemCount()):
            root = self.tree.topLevelItem(i)
            self._filter_item(root, query)
            if query and not root.isHidden():
                root.setExpanded(True)

    @classmethod
    def _filter_item(cls, item, query):
        matching = (not query or any(query in (item.text(column) or '').casefold()
                                     for column in range(item.columnCount()))
                    or query in (item.toolTip(0) or '').casefold())
        descendant = False
        for i in range(item.childCount()):
            descendant |= cls._filter_item(item.child(i), query)
        visible = matching or descendant
        item.setHidden(not visible)
        return visible

    def _toggle_details(self):
        if self.detail_button.text() == 'Expand details':
            self.tree.expandAll()
            self.detail_button.setText('Collapse details')
        else:
            self.tree.collapseAll()
            for index in range(self.tree.topLevelItemCount()):
                item = self.tree.topLevelItem(index)
                if item.text(1) != 'warning':
                    item.setExpanded(True)
            self.detail_button.setText('Expand details')

    def _copy_report(self):
        QApplication.clipboard().setText(
            full_texture_diagnostic_report(
                self.selected, self.preview, self.reports, self.issues))
