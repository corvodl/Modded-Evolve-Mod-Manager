"""Qt project and editing workbench for v1.1.15.

Workflows operate only on extracted project copies; no executable injection,
remote protocol changes or PAK resigning is performed in these dialogs.
"""
from __future__ import annotations
import json
from pathlib import Path
from PySide6.QtCore import Qt,QTimer
from PySide6.QtWidgets import (QDialog,QWidget,QVBoxLayout,QHBoxLayout,QLabel,
    QPushButton,QComboBox,QTableWidget,QTableWidgetItem,QHeaderView,
    QFileDialog,QMessageBox,QLineEdit,QPlainTextEdit,QTabWidget,QCheckBox)
from qt_asset_intelligence import available_workspaces, chosen_rows
from workflow_lab import (workspace_summary,project_conflicts,
    cache_asset_manifest,preset_from_xml,apply_preset,search_text,
    replace_text_preview,apply_text_preview)
from workspace_editor import save_text,digest
from workspace_file_actions import checked_path
from material_editor_v2 import reference_fields,material_adjust
from quality_lab_116 import project_health, benchmark_workspace
from studio_tools import numeric_fields


def button(label,callback):
    widget=QPushButton(label)
    widget.clicked.connect(lambda checked=False:callback())
    return widget


def table(headers):
    view=QTableWidget(0,len(headers))
    view.setHorizontalHeaderLabels(headers)
    view.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    view.setSelectionBehavior(QTableWidget.SelectRows)
    view.setEditTriggers(QTableWidget.NoEditTriggers)
    view.setAlternatingRowColors(True)
    return view


def preview(parent,title,value):
    dlg=QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.resize(880,580)
    box=QVBoxLayout(dlg)
    field=QPlainTextEdit(value)
    field.setReadOnly(True)
    box.addWidget(field)
    box.addWidget(button('Close',dlg.accept))
    dlg.exec()


class ProjectDashboard(QDialog):
    def __init__(self,manager):
        super().__init__(manager)
        self.manager=manager
        self.setWindowTitle('Project Dashboard — Evolve Mod Manager')
        self.resize(1020,670)
        lay=QVBoxLayout(self)
        lay.addWidget(QLabel('Verified extracted PAK workspaces; source files and '
                             'installed game remain unchanged.'))
        actions=QHBoxLayout()
        actions.addWidget(button('Refresh Projects',self.refresh))
        actions.addWidget(button('Open Selected',self.open_selected))
        actions.addWidget(button('Compare Selected Projects',self.compare))
        actions.addWidget(button('Refresh Asset Cache',self.cache))
        actions.addWidget(button('Project Health',self.health))
        actions.addWidget(button('Benchmark',self.benchmark))
        actions.addStretch()
        lay.addLayout(actions)
        self.view=table(('Project','PAK','Files','Edited','Disk (MiB)','Last Activity'))
        lay.addWidget(self.view,1)
        self.info=QLabel()
        lay.addWidget(self.info)
        lay.addWidget(button('Close',self.accept))
        self.items=[]
        self.refresh()

    def refresh(self):
        self.items=[]
        workspace_map=available_workspaces(self.manager)
        errors=[]
        for title,ws in list(workspace_map.items())[:200]:
            try:
                data=workspace_summary(ws)
                self.items.append((title,ws,data))
            except (OSError,ValueError) as exc:
                errors.append(title)
        self.view.setRowCount(len(self.items))
        from datetime import datetime
        for i,(title,ws,data) in enumerate(self.items):
            values=(title,data['source'],str(data['files']),
                str(data['modified']),'%.1f'%(data['disk_bytes']/1048576),
                datetime.fromtimestamp(data['last_activity']).strftime('%Y-%m-%d %H:%M'))
            for j,value in enumerate(values):
                self.view.setItem(i,j,QTableWidgetItem(value))
        self.info.setText('%d projects, %d unreadable. Refresh scans project files.'%
                          (len(self.items),len(errors)))

    def selected(self):
        return [self.items[i] for i in chosen_rows(self.view)]

    def open_selected(self):
        selected=self.selected()
        if len(selected)!=1:
            QMessageBox.information(self,'Project Dashboard','Select one project.')
            return
        _,ws,_=selected[0]
        from qt_workspace_editor import QtWorkspaceEditor
        editor=QtWorkspaceEditor(self.manager,ws)
        editor.setAttribute(Qt.WA_DeleteOnClose,False)
        self.manager.editors.append(editor)
        editor.show()
        editor.raise_()
        self.accept()

    def compare(self):
        selected=self.selected()
        if len(selected)!=2:
            QMessageBox.information(self,'Compare conflicts','Select two projects.')
            return
        try:
            conflicts=project_conflicts(selected[0][1],selected[1][1])
            text='\n'.join('%s: %s • %s'%(x['path'],x['kind'],
                ', '.join('%s.%s'%p for p in x['properties']) or 'byte difference')
                for x in conflicts)
            preview(self,'File-level conflicts',text or 'No conflicting virtual file paths.')
        except (OSError,ValueError) as exc:
            QMessageBox.warning(self,'Conflict scan failed',str(exc))

    def health(self):
        selected=self.selected()
        if not selected:
            QMessageBox.information(self, 'Project Health', 'Select at least one project.')
            return
        reports=[]
        for title,ws,_ in selected[:20]:
            try:
                reports.append({'title':title, **project_health(ws)})
            except (OSError,ValueError) as exc:
                reports.append({'title':title,'error':str(exc)})
        preview(self,'Project Health Dashboard',json.dumps(reports,indent=2))

    def benchmark(self):
        selected=self.selected()
        if not selected:
            QMessageBox.information(self,'Regression Benchmarks', 'Select at least one project.')
            return
        reports=[]
        for title,ws,_ in selected[:10]:
            try:
                stats={'title':title,**benchmark_workspace(ws)}
                editor=next((e for e in getattr(self.manager,'editors',[])
                             if getattr(e,'workspace',None)==ws and hasattr(e,'last_populate_ms')),
                             None)
                stats['editor_refresh_ms']=getattr(editor,'last_populate_ms',None)
                stats['editor_refresh_note']=('Measured last populated editor tree' if editor else
                                             'Open this project in Edit Files to measure editor refresh')
                reports.append(stats)
            except (OSError,ValueError) as exc:
                reports.append({'title':title,'error':str(exc)})
        output=QFileDialog.getSaveFileName(self,'Save benchmark results (optional)',
                                           '', 'JSON (*.json)')[0]
        text=json.dumps({'version':1,'benchmarks':reports,
                         'last_real_signed_pak_extraction':self.manager.settings.get('last_extraction_benchmark')},indent=2)
        if output:
            try:
                Path(output).write_text(text,encoding='utf-8')
            except OSError as exc:
                QMessageBox.warning(self,'Export failed',str(exc))
        preview(self,'Read-only Regression Benchmarks',text)

    def cache(self):
        chosen=self.selected()
        if not chosen:
            QMessageBox.information(self,'Asset caching','Select one or more projects.')
            return
        stats=[]
        for title,ws,data in chosen:
            try:
                assets,hit=cache_asset_manifest(
                    ws,Path(self.manager.projects_path)/'.asset-cache'/'indexes')
                stats.append('%s: %d assets • %s'%(title,len(assets),
                    'reused' if hit else 'indexed'))
            except (OSError,ValueError) as exc:
                stats.append('%s: %s'%(title,str(exc)))
        preview(self,'Asset cache', '\n'.join(stats))


class GameplayPresetDialog(QDialog):
    def __init__(self,editor):
        super().__init__(editor)
        if editor.mode!='files' or not editor.current or not editor.current.lower().endswith('.xml'):
            raise ValueError('Open a gameplay XML asset before editing presets.')
        if editor._dirty():
            raise ValueError('Save or discard current unsaved XML edits first.')
        self.editor=editor
        self.original=editor.views['files'].toPlainText()
        self.fields=numeric_fields(self.original)
        self.setWindowTitle('Gameplay Presets — '+Path(editor.current).name)
        self.resize(870,650)
        layout=QVBoxLayout(self)
        layout.addWidget(QLabel('Save only existing numeric properties. Presets require an '
                                'exact matching XML baseline; never silently remap values.'))
        self.grid=QTableWidget(len(self.fields),4)
        self.grid.setHorizontalHeaderLabels(('Element','Attribute','Original','Preset Value'))
        self.grid.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        for i,field in enumerate(self.fields):
            for j,val in enumerate((field.tag,field.attribute,field.value,field.value)):
                item=QTableWidgetItem(val)
                if j!=3:item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.grid.setItem(i,j,item)
        layout.addWidget(self.grid,1)
        buttons=QHBoxLayout()
        buttons.addWidget(button('Export Preset…',self.export_preset))
        buttons.addWidget(button('Load & Preview Preset…',self.load_preset))
        buttons.addWidget(button('Close',self.accept))
        layout.addLayout(buttons)

    def _edits(self):
        return {i:self.grid.item(i,3).text().strip()
                for i,field in enumerate(self.fields)
                if self.grid.item(i,3).text().strip()!=field.value}

    def export_preset(self):
        path,_=QFileDialog.getSaveFileName(self,'Save gameplay preset',
            'Evolve-Gameplay-Preset.json','JSON (*.json)')
        if not path:return
        try:
            data=preset_from_xml(self.original,self._edits())
            target=Path(path)
            with target.open('x',encoding='utf-8') as output:
                json.dump(data,output,indent=2)
        except (OSError,ValueError) as exc:
            QMessageBox.warning(self,'Preset export rejected',str(exc))

    def load_preset(self):
        path,_=QFileDialog.getOpenFileName(
            self,'Open gameplay preset','','JSON (*.json)')
        if not path:return
        try:
            source=Path(path)
            if source.stat().st_size>128000:
                raise ValueError('Preset file exceeds size limit')
            changes=json.loads(source.read_text('utf-8'))
            result=apply_preset(self.original,changes)
            if QMessageBox.question(self,'Apply gameplay preset',
                '%d byte text changed. Apply this preset to the extracted project '
                'with an original backup?'%sum(a!=b for a,b in zip(self.original,result)),
                QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:
                return
            new=save_text(self.editor.workspace,self.editor.current,
                result,digest(self.editor.raw),self.editor.raw,
                self.editor.records[self.editor.current].get('format')=='cryxml')
            self.editor.load(self.editor.current)
            self.accept()
        except (OSError,ValueError,KeyError) as exc:
            QMessageBox.warning(self,'Preset rejected',str(exc))


class GlobalSearchDialog(QDialog):
    def __init__(self,editor):
        super().__init__(editor)
        self.editor=editor
        self.setWindowTitle('Global Search & Replace — Extracted PAK')
        self.resize(1000,720)
        layout=QVBoxLayout(self)
        top=QHBoxLayout()
        self.find=QLineEdit()
        self.find.setPlaceholderText('Search XML, materials, config and text')
        self.replace=QLineEdit()
        self.replace.setPlaceholderText('Optional replacement text')
        top.addWidget(self.find,2)
        top.addWidget(self.replace,2)
        top.addWidget(button('Find',self.search))
        layout.addLayout(top)
        self.results=table(('File','Line','Matching content'))
        layout.addWidget(self.results,1)
        self.status=QLabel('Search includes only text assets in the current extracted PAK.')
        layout.addWidget(self.status)
        actions=QHBoxLayout()
        actions.addWidget(button('Preview Selected Replacements',self.preview_replace))
        actions.addWidget(button('Apply with Backups…',self.apply))
        actions.addStretch()
        actions.addWidget(button('Close',self.accept))
        layout.addLayout(actions)
        self.matches=()
        self.plan=()

    def search(self):
        try:
            self.matches=search_text(self.editor.workspace,self.find.text())
            self.results.setRowCount(len(self.matches))
            for row,(name,number,line) in enumerate(self.matches):
                for col,value in enumerate((name,str(number),line)):
                    self.results.setItem(row,col,QTableWidgetItem(value))
            self.plan=()
            self.status.setText('%d matching lines. Choose rows to prepare replacements.'%
                                len(self.matches))
        except (ValueError,OSError) as exc:
            QMessageBox.warning(self,'Search unavailable',str(exc))

    def preview_replace(self):
        try:
            paths=sorted({self.matches[r][0] for r in chosen_rows(self.results)})
            if not paths:
                raise ValueError('Select one or more matching rows.')
            self.plan=replace_text_preview(self.editor.workspace,
                self.find.text(),self.replace.text(),paths)
            text='\n'.join('%s: %d → %d characters'%(rel,len(a),len(b))
                           for rel,a,b,_ in self.plan)
            preview(self,'Validated replacement preview',
                    text or 'Selected files contain no replacement changes.')
        except (ValueError,OSError) as exc:
            QMessageBox.warning(self,'Replacement rejected',str(exc))

    def apply(self):
        if not self.plan:
            QMessageBox.information(self,'Global replace','Preview a replacement first.')
            return
        if self.editor._dirty():
            QMessageBox.warning(self,'Unsaved edits','Save your active file first.')
            return
        if QMessageBox.question(self,'Apply global replacements',
            'Modify %d extracted project file(s), creating backups?'%len(self.plan),
            QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:
            return
        try:
            result=apply_text_preview(self.editor.workspace,self.plan)
            if self.editor.current in result:
                self.editor.load(self.editor.current)
            self.plan=()
            self.status.setText('Updated %d project file(s).'%len(result))
        except (ValueError,OSError) as exc:
            QMessageBox.warning(self,'Global replace interrupted',str(exc))


class MaterialEditorV2(QDialog):
    def __init__(self,editor):
        super().__init__(editor)
        if editor.mode!='files' or not editor.current or not editor.current.lower().endswith('.mtl'):
            raise ValueError('Open an extracted .mtl material file first.')
        if editor._dirty():
            raise ValueError('Save existing material text edits before using visual controls.')
        self.editor=editor
        self.original=editor.views['files'].toPlainText()
        self.numbers=numeric_fields(self.original)
        self.textures=reference_fields(self.original)
        self.setWindowTitle('CryEngine Material Editor 2.0')
        self.resize(950,700)
        layout=QVBoxLayout(self)
        layout.addWidget(QLabel('Edit material numeric properties and texture references. '
            'All other XML bytes are preserved and saved with backups. GPU preview '
            'will refresh when the material changes are saved.'))
        self.tabs=QTabWidget()
        layout.addWidget(self.tabs,1)
        num=QWidget(); box=QVBoxLayout(num)
        self.nums=QTableWidget(len(self.numbers),3)
        self.nums.setHorizontalHeaderLabels(('Property','Original','New value'))
        self.nums.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        for i,item in enumerate(self.numbers):
            for j,value in enumerate((item.tag+'.'+item.attribute,item.value,item.value)):
                cell=QTableWidgetItem(value)
                if j!=2:cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
                self.nums.setItem(i,j,cell)
        box.addWidget(self.nums)
        self.tabs.addTab(num,'Numeric Properties')
        tex=QWidget(); box=QVBoxLayout(tex)
        self.paths=QTableWidget(len(self.textures),2)
        self.paths.setHorizontalHeaderLabels(('Original texture','New relative DDS path'))
        self.paths.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        for i,item in enumerate(self.textures):
            for j,value in enumerate((item.value,item.value)):
                cell=QTableWidgetItem(value)
                if not j:cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
                self.paths.setItem(i,j,cell)
        box.addWidget(self.paths)
        self.tabs.addTab(tex,'Texture References')
        bottom=QHBoxLayout()
        bottom.addWidget(button('Save Material with Backup',self.apply))
        bottom.addWidget(button('Cancel',self.reject))
        layout.addLayout(bottom)

    def apply(self):
        try:
            numerical={i:self.nums.item(i,2).text().strip()
                       for i,row in enumerate(self.numbers)
                       if self.nums.item(i,2).text().strip()!=row.value}
            textures={i:self.paths.item(i,1).text().strip()
                      for i,row in enumerate(self.textures)
                      if self.paths.item(i,1).text().strip()!=row.value}
            adjusted=material_adjust(self.original,numerical,textures)
            if adjusted==self.original:
                self.accept(); return
            if QMessageBox.question(self,'Save material',
                'Save these material changes to the extracted project with '
                'a backup? Original game files remain unchanged.',
                QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:
                return
            save_text(self.editor.workspace,self.editor.current,adjusted,
                digest(self.editor.raw),self.editor.raw,False)
            self.editor.load(self.editor.current)
            self.accept()
        except (ValueError,OSError) as exc:
            QMessageBox.warning(self,'Material edit rejected',str(exc))
