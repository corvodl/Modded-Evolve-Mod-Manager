"""Qt dialogs for project cleanup, PAK diff, asset dependencies, and validation.

Only backup-verified workspace data is examined. No automatic destructive
cleanup occurs without two explicit UI confirmations.
"""
from __future__ import annotations

from pathlib import Path
from datetime import datetime
import difflib

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
 QDialog, QLabel, QVBoxLayout, QHBoxLayout, QPushButton, QComboBox,
 QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QMessageBox,
 QPlainTextEdit, QInputDialog, QGraphicsView, QGraphicsScene,
 QGraphicsTextItem, QFileDialog,
)
from PySide6.QtGui import QPen, QColor

from project_lifecycle import find_old_projects, delete_abandoned_project
from asset_intelligence import (
 compare_projects, text_difference, dependency_report, texture_suggestions,
 revisions, restore_revision, validate_project,
)
from evolve_pak_workspace import load_workspace


def button(label, action):
    b=QPushButton(label)
    b.clicked.connect(lambda checked=False: action())
    return b


def table(headers, height=450):
    t=QTableWidget(0,len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.setAlternatingRowColors(True)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setSelectionMode(QAbstractItemView.ExtendedSelection)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    t.verticalHeader().hide()
    t.setMinimumHeight(height)
    return t


def chosen_rows(t):
    return sorted({item.row() for item in t.selectedItems()})


def dialog_text(parent,title,value):
    modal=QDialog(parent)
    modal.setWindowTitle(title)
    modal.resize(900,600)
    lay=QVBoxLayout(modal)
    editor=QPlainTextEdit()
    editor.setPlainText(value)
    editor.setReadOnly(True)
    lay.addWidget(editor)
    lay.addWidget(button('Close',modal.accept))
    modal.exec()


class ProjectCleanupDialog(QDialog):
    def __init__(self, manager):
        super().__init__(manager)
        self.manager=manager
        self.setWindowTitle('Projects — Disk Space Cleanup')
        self.resize(970,650)
        layout=QVBoxLayout(self)
        layout.addWidget(QLabel(
            'Only old, unmodified, inactive extracted projects are eligible. '
            'Signed PAKs, installed game files, mod profiles, and projects with '
            'unsaved/changed assets are never automatically removed.'))
        controls=QHBoxLayout()
        controls.addWidget(QLabel('Unused for at least'))
        self.days=QComboBox()
        for d in (30,90,14,7,0):
            self.days.addItem('%d days'%d,d)
        controls.addWidget(self.days)
        controls.addWidget(button('Scan Projects',self.scan))
        controls.addStretch()
        layout.addLayout(controls)
        self.table=table(('Project','Source PAK / Batch','Size','Age','Safety'),390)
        layout.addWidget(self.table,1)
        self.info=QLabel('Scan to find abandoned projects; no deletion occurs automatically.')
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        bottom=QHBoxLayout()
        bottom.addWidget(button('Open Projects Folder',manager.open_projects))
        bottom.addStretch()
        bottom.addWidget(button('Delete Selected Eligible Projects…',self.delete_selected))
        bottom.addWidget(button('Close',self.accept))
        layout.addLayout(bottom)
        self.records=()
        self.scan()

    def _active(self):
        active=[self.manager.current_workspace,self.manager.batch_root]
        active.extend(str(editor.workspace) for editor in self.manager.editors
                      if editor.isVisible() and hasattr(editor,'workspace'))
        return [p for p in active if p]

    def scan(self):
        try:
            self.records=find_old_projects(
                self.manager.projects_path,active=self._active(),
                min_age_days=self.days.currentData())
            self.table.setRowCount(len(self.records))
            for row,record in enumerate(self.records):
                fields=(record.path.name,record.archive,
                        '%.1f MiB'%(record.bytes/1048576),
                        '%.0f days'%record.age_days,
                        'Eligible' if record.eligible else record.reason)
                for col,value in enumerate(fields):
                    cell=QTableWidgetItem(value)
                    if not record.eligible:
                        cell.setForeground(QColor('#999999'))
                    self.table.setItem(row,col,cell)
            count=sum(r.eligible for r in self.records)
            size=sum(r.bytes for r in self.records if r.eligible)
            self.info.setText(
                '%d eligible project(s), estimated %.1f MiB reclaimable. '
                'Select rows explicitly to delete.'%(count,size/1048576))
        except (OSError,ValueError) as exc:
            QMessageBox.warning(self,'Project scan failed',str(exc))

    def delete_selected(self):
        selected=[self.records[i] for i in chosen_rows(self.table)]
        if not selected:
            QMessageBox.information(self,'Project cleanup','Select project rows first.')
            return
        unsafe=[r.path.name for r in selected if not r.eligible]
        if unsafe:
            QMessageBox.warning(self,'Project cleanup',
                'These projects have edits, are active, or do not meet the age '
                'requirement:\n'+'\n'.join(unsafe[:12]))
            return
        size=sum(r.bytes for r in selected)
        if QMessageBox.question(
                self,'Permanently delete extracted projects?',
                'Delete %d verified project folder(s) (about %.1f MiB)?\n\n'
                'This is permanent. Originals and signed PAKs are not affected, '
                'but extracted project folders cannot be recovered without '
                'extracting again.'%(len(selected),size/1048576),
                QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:
            return
        phrase,ok=QInputDialog.getText(
            self,'Confirm permanent deletion',
            'Type DELETE to permanently remove these extracted projects:')
        if not ok or phrase!='DELETE':
            return
        removed=[]
        for record in selected:
            try:
                delete_abandoned_project(self.manager.projects_path,record.path,
                    active=self._active(),min_age_days=self.days.currentData())
                removed.append(record.path.name)
            except (ValueError,OSError) as exc:
                QMessageBox.warning(self,'Project cleanup',
                                    'Could not delete '+record.path.name+': '+str(exc))
        self.manager._load_batch()
        self.manager.refresh_archives()
        self.scan()
        self.info.setText('Deleted %d unused project(s).'%len(removed))


def available_workspaces(manager):
    """Enumerate complete single and batch project manifests, without guessing."""
    from multi_pak_assets import load_collection, BATCH_FILE
    root=Path(manager.projects_path)
    result={}
    if not root.is_dir():
        return result
    for item in root.iterdir():
        if item.is_symlink() or not item.is_dir():
            continue
        if (item/'.evolve-pak-workspace.json').is_file():
            try:
                load_workspace(item)
                result[item.name]=item.resolve()
            except (ValueError,OSError):
                pass
        elif (item/BATCH_FILE).is_file():
            try:
                batch=load_collection(item)
                for record in batch['archives']:
                    workspace=(item/record['workspace']).resolve()
                    result[item.name+' / '+record['archive']]=workspace
            except (ValueError,OSError):
                pass
    return result


class PakDiffDialog(QDialog):
    def __init__(self,manager):
        super().__init__(manager)
        self.setWindowTitle('PAK Difference Inspector — extracted projects')
        self.resize(990,670)
        self.workspaces=available_workspaces(manager)
        layout=QVBoxLayout(self)
        layout.addWidget(QLabel('Compare files inside two extracted, verified PAK projects. '
                                'Raw encrypted PAK contents must be unpacked first.'))
        row=QHBoxLayout()
        row.addWidget(QLabel('Original'))
        self.first=QComboBox()
        self.first.addItems(list(self.workspaces))
        row.addWidget(self.first,1)
        row.addWidget(QLabel('Modified'))
        self.second=QComboBox()
        self.second.addItems(list(self.workspaces))
        if self.second.count()>1:
            self.second.setCurrentIndex(1)
        row.addWidget(self.second,1)
        row.addWidget(button('Compare',self.compare))
        layout.addLayout(row)
        self.results=()
        self.changes=table(('Status','Virtual asset path'),410)
        layout.addWidget(self.changes,1)
        row=QHBoxLayout()
        row.addWidget(button('Show XML/Text Differences',self.inspect_text))
        row.addStretch()
        row.addWidget(button('Close',self.accept))
        layout.addLayout(row)
        self.summary=QLabel('')
        layout.addWidget(self.summary)
        if len(self.workspaces)>=2:
            self.compare()

    def compare(self):
        try:
            a=self.workspaces[self.first.currentText()]
            b=self.workspaces[self.second.currentText()]
            self.results=compare_projects(a,b)
            self.changes.setRowCount(len(self.results))
            for i,(state,relative) in enumerate(self.results):
                self.changes.setItem(i,0,QTableWidgetItem(state))
                self.changes.setItem(i,1,QTableWidgetItem(relative))
            self.summary.setText('%d changed/added/removed file(s).'%len(self.results))
        except (ValueError,OSError,KeyError) as exc:
            QMessageBox.warning(self,'Comparison unavailable',str(exc))

    def inspect_text(self):
        rows=chosen_rows(self.changes)
        if len(rows)!=1:
            QMessageBox.information(self,'View XML diff','Choose one changed file.')
            return
        state,relative=self.results[rows[0]]
        if state!='Modified':
            QMessageBox.information(self,'View XML diff',
                                    'Text comparison requires the file in both projects.')
            return
        try:
            result=text_difference(self.workspaces[self.first.currentText()],
                                   self.workspaces[self.second.currentText()],relative)
            dialog_text(self,'Differences: '+relative,result or 'No textual difference.')
        except (ValueError,OSError,UnicodeError) as exc:
            QMessageBox.warning(self,'Text comparison unavailable',str(exc))


class DependencyDialog(QDialog):
    def __init__(self,editor):
        super().__init__(editor)
        if editor.mode!='models' or not editor.current:
            raise ValueError('Open a model to view its dependencies.')
        self.setWindowTitle('Asset Dependency Graph — '+Path(editor.current).name)
        self.resize(1080,710)
        layout=QVBoxLayout(self)
        layout.addWidget(QLabel('Verified material and animation references. '
                                'Missing paths remain marked; no files are modified.'))
        edges,report=dependency_report(editor.workspace,editor.current,
            editor.manager.projects_path,editor.manager.stage_path)
        scene=QGraphicsScene(self)
        root=editor.current
        branches={}
        for _,target,kind in edges[:160]:
            branches.setdefault(kind,[]).append(target)
        scene.addText(root).setPos(20,20)
        y=90
        for kind,names in branches.items():
            group=scene.addText(kind+': '+str(len(names)))
            group.setDefaultTextColor(QColor('#e36b75'))
            group.setPos(70,y)
            scene.addLine(37,48,82,y+13,QPen(QColor('#98525b')))
            y+=34
            for name in names[:24]:
                child=scene.addText(Path(name).name+'  ('+name[:110]+')')
                child.setDefaultTextColor(QColor('#dadada'))
                child.setPos(140,y)
                scene.addLine(93,y-17,144,y+12,QPen(QColor('#52535a')))
                y+=28
            if len(names)>24:
                scene.addText('…and %d more'% (len(names)-24)).setPos(140,y)
                y+=28
            y+=18
        view=QGraphicsView(scene)
        layout.addWidget(view,1)
        layout.addWidget(button('Close',self.accept))


class TextureResolverDialog(QDialog):
    def __init__(self,editor):
        super().__init__(editor)
        if editor.mode!='models' or not editor.current:
            raise ValueError('Open a model to inspect missing textures.')
        self.setWindowTitle('Missing Texture Resolver')
        self.resize(1030,610)
        layout=QVBoxLayout(self)
        layout.addWidget(QLabel(
            'Search for likely DDS matches across extracted PAKs. Candidate names '
            'are diagnostic suggestions and are not applied to materials automatically.'))
        self.entries=texture_suggestions(editor.workspace,editor.current,
            editor.manager.projects_path,editor.manager.stage_path)
        self.table=table(('Missing reference','Status','Possible sources'),400)
        self.table.setRowCount(len(self.entries))
        for row,(name,state,matches) in enumerate(self.entries):
            for col,val in enumerate((name,state,', '.join(matches[:5]) or 'None found')):
                self.table.setItem(row,col,QTableWidgetItem(val))
        layout.addWidget(self.table)
        layout.addWidget(QLabel(
            'Resolve ambiguities by extracting the referenced TS PAK or by selecting '
            'the correct texture folder. Changes to .mtl files are not guessed.'))
        layout.addWidget(button('Close',self.accept))


class ProjectHistoryDialog(QDialog):
    def __init__(self,editor):
        super().__init__(editor)
        if not editor.current:
            raise ValueError('Select a project file to view its history.')
        self.editor=editor
        self.setWindowTitle('Project History — '+Path(editor.current).name)
        self.resize(850,590)
        root=QVBoxLayout(self)
        root.addWidget(QLabel('Review previous saved versions. Restoring creates a new '
                              'backup of the current file first.'))
        self.entries=revisions(editor.workspace,editor.current)
        self.table=table(('Backup','Saved','Size'),350)
        self.table.setRowCount(len(self.entries))
        for i,(path,when,size) in enumerate(self.entries):
            for col,val in enumerate((path.parent.name,
               datetime.fromtimestamp(when).strftime('%Y-%m-%d %H:%M'),str(size)+' bytes')):
                self.table.setItem(i,col,QTableWidgetItem(val))
        root.addWidget(self.table)
        controls=QHBoxLayout()
        controls.addWidget(button('Compare with Current',self.preview))
        controls.addWidget(button('Restore Selected…',self.restore))
        controls.addStretch()
        controls.addWidget(button('Close',self.accept))
        root.addLayout(controls)

    def _selected(self):
        rows=chosen_rows(self.table)
        if len(rows)!=1:
            raise ValueError('Select one saved version.')
        return self.entries[rows[0]][0]

    def preview(self):
        try:
            previous=self._selected()
            current=Path(self.editor.workspace)/'files'/self.editor.current
            a=previous.read_bytes().decode('utf-8-sig').splitlines()
            b=current.read_bytes().decode('utf-8-sig').splitlines()
            output='\n'.join(difflib.unified_diff(
                a,b,fromfile='Backup',tofile='Current',lineterm=''))
            dialog_text(self,'Backup comparison',output or 'No textual differences.')
        except (ValueError,OSError,UnicodeError) as exc:
            QMessageBox.warning(self,'History comparison',str(exc))

    def restore(self):
        try:
            prev=self._selected()
            if not self.editor._confirm_buffer():
                return
            if QMessageBox.question(
                self,'Restore project revision',
                'Restore this saved revision to the project and back up current '
                'bytes first? Original game files remain unchanged.',
                QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:
                return
            restore_revision(self.editor.workspace,self.editor.current,prev)
            self.editor.load(self.editor.current)
            self.accept()
        except (ValueError,OSError) as exc:
            QMessageBox.warning(self,'Restore failed',str(exc))


class ValidationDialog(QDialog):
    def __init__(self,editor):
        super().__init__(editor)
        self.setWindowTitle('Mod Validation Center')
        self.resize(890,650)
        root=QVBoxLayout(self)
        root.addWidget(QLabel('Checks modified extracted assets for supported XML, '
                              'DDS and model formats before you build a signed mod.'))
        try:
            modified,problems=validate_project(editor.workspace)
        except (ValueError,OSError) as exc:
            modified,problems=(),(str(exc),)
        text='Changed files: %d\nDetected issues: %d\n\n'%(len(modified),len(problems))
        text+='Modified:\n'+'\n'.join(modified[:100])
        if problems:
            text+='\n\nProblems:\n'+'\n'.join(problems[:100])
        else:
            text+='\n\nNo format problems detected. Signing and runtime '
            text+='compatibility still require the normal Build Mod checks.'
        field=QPlainTextEdit(text)
        field.setReadOnly(True)
        root.addWidget(field,1)
        root.addWidget(button('Close',self.accept))
