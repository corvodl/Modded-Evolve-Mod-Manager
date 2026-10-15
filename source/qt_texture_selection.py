"""Folder-grouped texture picker for the current model's material slots."""
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QLineEdit, QTreeWidget, QTreeWidgetItem, QPushButton, QDialogButtonBox,
    QFileDialog, QMessageBox)
from texture_selection import texture_choices, load_chosen_texture


class TextureSelectionDialog(QDialog):
    def __init__(self, parent, mesh, materials, index, choices, folder=None):
        super().__init__(parent)
        self.setWindowTitle('Choose Model Textures')
        self.resize(880, 620)
        self.index = index or {}
        self.choices = dict(choices)
        self.folder = folder
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('Choose a material slot, then a texture. Selections apply to the preview and GLB exports.'))
        self.slot = QComboBox()
        for slot in sorted({s for _, _, s in mesh.subsets} or {0}):
            name = materials.names[slot] if materials and slot < len(materials.names) else 'Unresolved'
            self.slot.addItem('%d: %s' % (slot, name), slot)
        layout.addWidget(self.slot)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText('Filter by texture name or folder…')
        layout.addWidget(self.filter)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['Available folders / textures', 'Archive'])
        self.tree.setColumnWidth(0, 600)
        layout.addWidget(self.tree)
        row = QHBoxLayout()
        for label, callback in (('Browse Folder…', self.browse), ('Use Selected Texture', self.choose), ('Reset This Slot', self.reset)):
            button = QPushButton(label);button.clicked.connect(callback);row.addWidget(button)
        layout.addLayout(row)
        self.status = QLabel('No changes applied until you choose Apply.')
        self.status.setWordWrap(True);layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.Apply | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Apply).clicked.connect(self.accept)
        buttons.rejected.connect(self.reject);layout.addWidget(buttons)
        self.filter.textChanged.connect(self.filter_rows)
        self.populate()

    def populate(self):
        self.tree.clear();groups = {}
        for record in texture_choices(self.index, self.folder):
            virtual = str(record['relative']).replace('\\', '/')
            group = str(Path(virtual).parent)
            if group not in groups:
                groups[group] = QTreeWidgetItem(self.tree, [group])
            child = QTreeWidgetItem(groups[group], [Path(virtual).name, str(record.get('archive', 'Folder'))])
            child.setData(0, Qt.UserRole, record)
            child.setToolTip(0, str(record['file']))
        self.filter_rows()

    def filter_rows(self):
        term = self.filter.text().casefold()
        for i in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(i);visible = False
            for j in range(group.childCount()):
                item = group.child(j)
                match = term in (group.text(0) + '/' + item.text(0)).casefold()
                item.setHidden(not match);visible |= match
            group.setHidden(not visible)
            group.setExpanded(bool(term) and visible)

    def browse(self):
        folder = QFileDialog.getExistingDirectory(self, 'Choose Texture Folder', str(self.folder or ''))
        if folder:
            self.folder = Path(folder)
            try:self.populate()
            except (OSError, ValueError) as error:QMessageBox.warning(self, 'Texture folder', str(error))

    def choose(self):
        item = self.tree.currentItem()
        record = item.data(0, Qt.UserRole) if item else None
        if not record:
            return
        try:image = load_chosen_texture(record, self.index)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, 'Texture could not be loaded', str(error));return
        slot = self.slot.currentData()
        self.choices[slot] = (image, str(record['relative']))
        self.status.setText('Slot %d: %s' % (slot, record['relative']))

    def reset(self):
        self.choices.pop(self.slot.currentData(), None)
        self.status.setText('This slot will use its automatic material texture.')
