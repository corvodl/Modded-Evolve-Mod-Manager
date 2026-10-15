"""Lazy Qt tree adapter for large browsable map/extracted-asset inventories.

Only QAbstractItemModel indices for visible rows are constructed. Source
objects are immutable metadata, not QTreeWidgetItems, and stay fully searchable
even when a directory is collapsed. All mutations occur on the GUI thread.
"""
from __future__ import annotations
from dataclasses import dataclass, field

from PySide6.QtCore import QAbstractItemModel, QModelIndex, Qt


@dataclass
class TreeNode:
    label: str
    kind: str = ''
    value: object = None
    tooltip: str = ''
    size: str = ''
    parent: object = field(default=None, repr=False)
    children: list = field(default_factory=list, repr=False)
    loaded: int = 0
    ordinal: int = 0

    def append(self, child):
        child.parent = self
        child.ordinal = len(self.children)
        self.children.append(child)


class VirtualTreeModel(QAbstractItemModel):
    """Tree with stable logical identities and batched fetchMore pagination."""

    def __init__(self, parent=None, *, labels=('Name', 'Type'), batch=256):
        super().__init__(parent)
        self.root = TreeNode('')
        self.labels = tuple(labels)
        self.batch = max(16, int(batch))
        self.reset_root(TreeNode(''))

    def reset_root(self, root):
        self.beginResetModel()
        self.root = root
        self.root.loaded = min(len(self.root.children), self.batch)
        for child in self.root.children:
            child.loaded = 0
        self.endResetModel()

    def _node(self, index):
        return index.internalPointer() if index.isValid() else self.root

    def columnCount(self, parent=QModelIndex()):
        return len(self.labels)

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid() and parent.column() > 0:
            return 0
        return self._node(parent).loaded

    def index(self, row, column, parent=QModelIndex()):
        if column < 0 or column >= len(self.labels):
            return QModelIndex()
        node = self._node(parent)
        if row < 0 or row >= node.loaded:
            return QModelIndex()
        return self.createIndex(row, column, node.children[row])

    def parent(self, index):
        if not index.isValid():
            return QModelIndex()
        node = index.internalPointer()
        parent = node.parent
        if parent is None or parent is self.root:
            return QModelIndex()
        return self.createIndex(parent.ordinal, 0, parent)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        node = index.internalPointer()
        if role == Qt.DisplayRole:
            if index.column() == 0:
                return node.label
            if index.column() == 1:
                return node.size or node.kind
            return node.kind
        if role == Qt.UserRole:
            return node.value
        if role == Qt.ToolTipRole:
            return node.tooltip or node.label
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if (role == Qt.DisplayRole and orientation == Qt.Horizontal and
                0 <= section < len(self.labels)):
            return self.labels[section]
        return None

    def flags(self, index):
        return (Qt.ItemIsSelectable | Qt.ItemIsEnabled
                if index.isValid() else Qt.NoItemFlags)

    def hasChildren(self, parent=QModelIndex()):
        return bool(self._node(parent).children)

    def canFetchMore(self, parent=QModelIndex()):
        return self._node(parent).loaded < len(self._node(parent).children)

    def fetchMore(self, parent=QModelIndex()):
        node = self._node(parent)
        first = node.loaded
        last = min(len(node.children), first + self.batch)
        if first >= last:
            return
        self.beginInsertRows(parent, first, last - 1)
        node.loaded = last
        self.endInsertRows()

    def materialize_row(self, parent, row):
        """Make a logical row visible without constructing all Qt items."""
        if row < 0 or row >= len(self._node(parent).children):
            return QModelIndex()
        while row >= self._node(parent).loaded:
            self.fetchMore(parent)
        return self.index(row, 0, parent)
