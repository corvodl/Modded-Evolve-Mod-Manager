"""25,000-entry Qt virtual tree remains searchable without widget rows."""
import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtWidgets import QApplication

from qt_virtual_tree import TreeNode, VirtualTreeModel


class VirtualTreeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_paginated_25k_entries_preserve_exact_relative_paths(self):
        root = TreeNode('')
        group = TreeNode('Maps', 'Folder')
        root.append(group)
        for i in range(25000):
            virtual = 'levels/map_%05d/objects/asset%d.cgf' % (i, i)
            group.append(TreeNode('asset%d.cgf' % i, 'Mesh', virtual))
        model = VirtualTreeModel(labels=('Name', 'Type'), batch=128)
        model.reset_root(root)
        self.assertEqual(model.rowCount(QModelIndex()), 1)
        folder = model.index(0, 0)
        self.assertTrue(model.canFetchMore(folder))
        self.assertEqual(model.rowCount(folder), 0)
        model.fetchMore(folder)
        self.assertEqual(model.rowCount(folder), 128)
        self.assertEqual(model.index(127, 0, folder).data(Qt.UserRole),
                         'levels/map_00127/objects/asset127.cgf')
        # Reveal a record beyond the first 128 without constructing Qt
        # widgets or truncating the original 25,000-entry catalog.
        sought = model.materialize_row(folder, 24999)
        self.assertTrue(sought.isValid())
        self.assertEqual(sought.data(Qt.UserRole),
                         'levels/map_24999/objects/asset24999.cgf')
        self.assertIs(model.parent(sought).internalPointer(), group)
        self.assertEqual(len(group.children), 25000)

    def test_search_root_reset_invalidates_stale_indices(self):
        root = TreeNode('')
        folder = TreeNode('Hunters', 'Folder')
        root.append(folder)
        folder.append(TreeNode('tech.skinm', 'Model',
                               'characters/hunters/merc_tech/tech.skinm'))
        model = VirtualTreeModel(batch=32)
        model.reset_root(root)
        group = model.index(0, 0)
        model.fetchMore(group)
        first = model.index(0, 0, group)
        self.assertIn('tech.skinm', first.data(Qt.UserRole))
        next_root = TreeNode('')
        next_root.append(TreeNode('Only matched result', 'File', 'filtered.xml'))
        model.reset_root(next_root)
        self.assertEqual(model.rowCount(), 1)
        self.assertEqual(model.index(0, 0).data(Qt.UserRole), 'filtered.xml')

    def test_model_row_insert_signals_are_bounded(self):
        root = TreeNode('')
        for i in range(1000):
            root.append(TreeNode(str(i), 'File', 'file/%d' % i))
        model = VirtualTreeModel(batch=64)
        model.reset_root(root)
        calls = []
        model.rowsInserted.connect(lambda parent, first, last:
                                  calls.append((first, last)))
        while model.canFetchMore():
            model.fetchMore()
        self.assertEqual(model.rowCount(), 1000)
        self.assertTrue(calls)
        self.assertTrue(all((last - first + 1) <= 64 for first, last in calls))


if __name__ == '__main__':
    unittest.main()
