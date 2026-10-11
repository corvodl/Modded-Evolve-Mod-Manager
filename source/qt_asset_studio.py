"""Compact Qt workbench: numerical CryXML controls, DDS image adjustments, and asset search.

The workbench works entirely on extracted project files. All writes use the
manager's verified replace/save code paths, with backups and hash checks.
"""
from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import tempfile

from PIL import Image, ImageEnhance
from PySide6.QtCore import Qt, QTimer, QThreadPool
from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QTabWidget,
    QLabel, QLineEdit, QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QComboBox, QFileDialog, QMessageBox, QSlider, QCheckBox,
    QAbstractItemView, QPlainTextEdit,
)

from studio_tools import numeric_fields, apply_numeric_edits, asset_search, batch_export
from workspace_editor import save_text, digest, MAX_TEXT
from workspace_file_actions import checked_path
from dds_texture import preview_dds, parse_dds
from dds_png_import import encode_png_as_dds
from dds_texture import is_split_dds, is_dds


def _button(label, callback):
    b = QPushButton(label)
    b.clicked.connect(lambda checked=False: callback())
    return b


class GameplayValuesDialog(QDialog):
    """Edit existing numeric attributes, not arbitrary XML structure."""
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        if editor.mode != 'files' or not editor.current:
            raise ValueError('Select an extracted XML gameplay file first.')
        if Path(editor.current).suffix.lower() != '.xml':
            raise ValueError('Choose an XML file, such as an ability configuration.')
        self.path = editor.current
        self.original = editor.raw
        if len(self.original) > MAX_TEXT:
            raise ValueError('This XML exceeds the visual editor size limit.')
        self.original_text = editor.views['files'].toPlainText()
        if self.original_text != editor.loaded_text:
            raise ValueError('Save or discard pending text edits before using the visual editor.')
        self.fields = numeric_fields(self.original_text)
        self.setWindowTitle('Visual Gameplay Editor — ' + Path(self.path).name)
        self.resize(875, 620)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            'Edit existing numeric CryXML attributes only. This preserves the XML structure, '
            'supports undo through backups, and does not assume every number affects gameplay.'))
        bar = QHBoxLayout()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText('Filter fields by tag, attribute or value…')
        self.filter.textChanged.connect(self._filter)
        bar.addWidget(self.filter,1)
        bar.addWidget(_button('Reset edits', self._reset))
        layout.addLayout(bar)
        self.table = QTableWidget(len(self.fields),4)
        self.table.setHorizontalHeaderLabels(('XML element','Property','Original','New value'))
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(0,QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1,QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3,QHeaderView.Stretch)
        for row, field in enumerate(self.fields):
            for column, value in enumerate((field.tag,field.attribute,field.value,field.value)):
                item = QTableWidgetItem(value)
                if column != 3:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(row,column,item)
        layout.addWidget(self.table,1)
        self.summary = QLabel('%d numeric fields found. Only changed values are saved.' % len(self.fields))
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        controls = QHBoxLayout()
        controls.addWidget(_button('Preview XML changes',self._preview))
        controls.addStretch()
        controls.addWidget(_button('Save with backup',self._save))
        controls.addWidget(_button('Close',self.reject))
        layout.addLayout(controls)

    def _filter(self, value):
        token=value.casefold().strip()
        for row, field in enumerate(self.fields):
            hay=(field.tag+' '+field.attribute+' '+field.value).casefold()
            self.table.setRowHidden(row,bool(token and token not in hay))

    def _reset(self):
        for i,field in enumerate(self.fields):
            self.table.item(i,3).setText(field.value)

    def _updated(self):
        changes = {}
        for i,field in enumerate(self.fields):
            item=self.table.item(i,3)
            text=item.text().strip() if item is not None else field.value
            if text != field.value:
                changes[field.id]=text
        return apply_numeric_edits(self.original_text,changes),changes

    def _preview(self):
        try:
            edited, changes = self._updated()
            dialog = QDialog(self)
            dialog.setWindowTitle('Gameplay changes — preview')
            dialog.resize(780,510)
            lay=QVBoxLayout(dialog)
            lay.addWidget(QLabel('%d changed XML values — project copy only.' % len(changes)))
            view=QPlainTextEdit()
            view.setReadOnly(True)
            view.setPlainText('\n'.join(
                '%s / %s: %s → %s' %
                (self.fields[i].tag,self.fields[i].attribute,
                 self.fields[i].value,new)
                for i,new in changes.items()) or 'No changed values.')
            lay.addWidget(view)
            lay.addWidget(_button('Close',dialog.accept))
            dialog.exec()
        except (ValueError,OSError) as exc:
            QMessageBox.warning(self,'Invalid gameplay value',str(exc))

    def _save(self):
        try:
            edited,changes=self._updated()
            if not changes:
                self.accept()
                return
            if self.editor.manager.busy:
                raise RuntimeError('Wait for the manager to finish its current task.')
            if QMessageBox.question(
                self,'Save gameplay edits',
                'Save %d changed attributes to the extracted project? '
                'The previous contents will be backed up.' % len(changes),
                QMessageBox.Yes|QMessageBox.No,QMessageBox.No) != QMessageBox.Yes:
                return
            fresh=save_text(self.editor.workspace,self.path,edited,
                            digest(self.original),self.original,
                            self.editor.records[self.path].get('format')=='cryxml')
            self.editor.load(self.path)
            self.editor.status.setText(
                'Saved %d gameplay properties with EditorBackups retained.' % len(changes))
            self.accept()
        except (ValueError,OSError,RuntimeError) as exc:
            QMessageBox.critical(self,'Cannot save gameplay edits',str(exc))


class TextureAdjustDialog(QDialog):
    """Non-destructive interactive image preview; uses original DDS encoder."""
    def __init__(self,editor):
        super().__init__(editor)
        self.editor=editor
        if editor.mode!='images' or not editor.current:
            raise ValueError('Select a DDS texture before opening the texture editor.')
        self.original=editor.raw
        info=parse_dds(self.original)
        if info.width*info.height>16_000_000:
            raise ValueError('Texture too large for interactive adjustment; use PNG export.')
        with Image.open(BytesIO(self.original)) as im:
            im.load()
            self.image=im.convert('RGBA')
        self.setWindowTitle('Texture Editor — '+Path(editor.current).name)
        self.resize(810,680)
        lay=QVBoxLayout(self)
        lay.addWidget(QLabel('%d × %d • %s. Preview adjustments before encoding to the existing DDS format.'
                             %(info.width,info.height,info.description)))
        self.preview=QLabel()
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(280)
        lay.addWidget(self.preview,1)
        self.adjust={}
        form=QFormLayout()
        for key,title in [('brightness','Brightness'),('contrast','Contrast'),
                          ('saturation','Saturation'),('sharpness','Sharpness')]:
            slider=QSlider(Qt.Horizontal)
            slider.setRange(0,200)
            slider.setValue(100)
            slider.valueChanged.connect(self.update_preview)
            self.adjust[key]=slider
            form.addRow(title,slider)
        lay.addLayout(form)
        actions=QHBoxLayout()
        actions.addWidget(_button('Reset adjustments',self.reset))
        actions.addWidget(_button('Export adjusted PNG…',self.export_png))
        actions.addStretch()
        actions.addWidget(_button('Import into project',self.apply))
        actions.addWidget(_button('Cancel',self.reject))
        lay.addLayout(actions)
        self.update_preview()

    def adjusted(self):
        image=self.image
        for name,enhancer in [('brightness',ImageEnhance.Brightness),
                              ('contrast',ImageEnhance.Contrast),
                              ('saturation',ImageEnhance.Color),
                              ('sharpness',ImageEnhance.Sharpness)]:
            value=self.adjust[name].value()/100
            if value!=1:
                image=enhancer(image).enhance(value)
        return image

    def update_preview(self, *_):
        from qt_workspace_editor import _image_pixmap
        image=self.adjusted()
        image.thumbnail((720,450),Image.Resampling.LANCZOS)
        self.preview.setPixmap(_image_pixmap(image))

    def reset(self):
        for slider in self.adjust.values():
            slider.setValue(100)

    def export_png(self):
        target,_=QFileDialog.getSaveFileName(self,'Export adjusted PNG',
                      Path(self.editor.current).stem+'-adjusted.png','PNG (*.png)')
        if target:
            try:
                self.adjusted().save(target,'PNG')
            except (ValueError,OSError) as exc:
                QMessageBox.warning(self,'PNG export failed',str(exc))

    def apply(self):
        if self.editor.manager.busy:
            QMessageBox.warning(self,'Texture Editor','Wait for the active manager task.')
            return
        if QMessageBox.question(
            self,'Replace project texture',
            'Encode adjusted pixels into the ORIGINAL DDS format and replace the '
            'extracted project texture with a backup? The installed game is unchanged.',
            QMessageBox.Yes|QMessageBox.No,QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            with tempfile.TemporaryDirectory(prefix='evolve-texture-edit-') as folder:
                png=Path(folder)/'adjusted.png'
                converted=Path(folder)/'replacement.dds'
                self.adjusted().save(png,'PNG')
                converted.write_bytes(encode_png_as_dds(self.original,png))
                self.editor._replace_texture(converted)
            self.accept()
        except (OSError,ValueError,RuntimeError) as exc:
            QMessageBox.warning(self,'Texture adjustment failed',str(exc))


class AssetSearchDialog(QDialog):
    """Cross-workspace manifest search, dependency inspection and batch export."""
    def __init__(self,editor):
        super().__init__(editor)
        self.editor=editor
        self.workspaces=dict(editor.archive_map) or {'Current PAK':editor.workspace}
        if editor.workspace not in self.workspaces.values():
            self.workspaces['Current PAK']=editor.workspace
        self.results=()
        self.setWindowTitle('Asset Search & Batch Export')
        self.resize(1080,710)
        layout=QVBoxLayout(self)
        layout.addWidget(QLabel(
            'Search extracted archives by virtual path. Select files to export; '
            'original PAKs and game files will not be modified.'))
        filters=QHBoxLayout()
        self.query=QLineEdit()
        self.query.setPlaceholderText('Path, folder, character name or asset filename')
        self.query.returnPressed.connect(self.search)
        filters.addWidget(self.query,1)
        self.suffix=QComboBox()
        self.suffix.addItem('All files','')
        for label,suffix in [('Models (.skinm)','.skinm'),
                             ('Materials (.mtl)','.mtl'),('XML (.xml)','.xml'),
                             ('DDS (.dds)','.dds'),('CAF (.caf)','.caf')]:
            self.suffix.addItem(label,suffix)
        filters.addWidget(self.suffix)
        filters.addWidget(_button('Search',self.search))
        layout.addLayout(filters)
        self.table=QTableWidget(0,4)
        self.table.setHorizontalHeaderLabels(('Archive','Virtual path','Size','Status'))
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1,QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3,QHeaderView.ResizeToContents)
        layout.addWidget(self.table,1)
        self.status=QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        bottom=QHBoxLayout()
        bottom.addWidget(_button('Open selected asset',self.open_asset))
        bottom.addWidget(_button('Inspect model dependencies',self.dependencies))
        bottom.addStretch()
        self.kind=QComboBox()
        for label,value in [('Original files','original'),
                            ('DDS as PNG','png'),('Models as GLB','glb')]:
            self.kind.addItem(label,value)
        bottom.addWidget(self.kind)
        bottom.addWidget(_button('Export selected…',self.export_selected))
        bottom.addWidget(_button('Close',self.reject))
        layout.addLayout(bottom)
        self.search()

    def search(self):
        try:
            self.results=asset_search(self.workspaces,self.query.text(),
                suffix=self.suffix.currentData(),limit=2500)
            self.table.setRowCount(len(self.results))
            for i,(label,root,rel,size) in enumerate(self.results):
                for j,value in enumerate((str(label),rel,str(size),'Extracted')):
                    self.table.setItem(i,j,QTableWidgetItem(value))
            self.status.setText('%d matched assets; results limited to 2,500.'%len(self.results))
        except (ValueError,OSError,TypeError) as exc:
            QMessageBox.warning(self,'Asset search failed',str(exc))

    def selected(self):
        chosen=sorted({item.row() for item in self.table.selectedItems()})
        return tuple(self.results[i] for i in chosen if 0<=i<len(self.results))

    def open_asset(self):
        selected=self.selected()
        if len(selected)!=1:
            QMessageBox.information(self,'Open asset','Select exactly one asset.')
            return
        label,root,relative,_=selected[0]
        archive=next((name for name,location in self.editor.archive_map.items()
                      if location==root),None)
        if root!=self.editor.workspace and archive:
            self.editor.switch_archive(archive)
        elif root!=self.editor.workspace:
            QMessageBox.warning(self,'Open asset',
                                'This asset belongs to a different archive; switch archives first.')
            return
        self.editor.load(relative)
        section=self.editor._category(relative)
        self.editor.tabs.setCurrentIndex(self.editor.TABS.index(section))
        self.accept()

    def dependencies(self):
        selected=self.selected()
        if len(selected)!=1:
            QMessageBox.information(self,'Dependencies','Select one model.')
            return
        _,root,rel,_=selected[0]
        from multi_pak_assets import model_material_links
        try:
            links=model_material_links(root,rel,
                projects_root=self.editor.manager.projects_path,
                stage_root=self.editor.manager.stage_path)
            lines=['Material: '+str(links.get('material')),
                   'Status: '+str(links.get('status'))]
            for value in links.get('textures',[]):
                lines.append('%s: %s (%s)'%(value['map'],value['reference'],value['status']))
            QMessageBox.information(self,'Referenced asset dependencies','\n'.join(lines))
        except (ValueError,OSError) as exc:
            QMessageBox.warning(self,'Dependency lookup failed',str(exc))

    def export_selected(self):
        selected=self.selected()
        if not selected:
            QMessageBox.information(self,'Batch export','Select files to export.')
            return
        folder=QFileDialog.getExistingDirectory(self,'Choose export destination')
        if not folder:
            return
        if QMessageBox.question(
            self,'Batch export',
            'Export %d selected project assets as %s? Files at existing destinations '
            'will be skipped.'%(len(selected),self.kind.currentText()),
            QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:
            return
        try:
            report=batch_export(self.workspaces,
                [(label,root,rel) for label,root,rel,_ in selected], folder,
                kind=self.kind.currentData(),
                projects_root=self.editor.manager.projects_path)
            summary={
                'format': 'Evolve Mod Manager batch export report v1',
                'items': list(report),
            }
            dest=Path(folder)/'evolve-batch-export-report.json'
            if not dest.exists():
                dest.write_text(json.dumps(summary,indent=2),encoding='utf-8')
            passed=sum(item['status']=='exported' for item in report)
            QMessageBox.information(self,'Batch export complete',
                '%d exported, %d skipped.\n%s'%(passed,len(report)-passed,folder))
        except (ValueError,OSError) as exc:
            QMessageBox.critical(self,'Batch export failed',str(exc))


class ModelCompareDialog(QDialog):
    """Two independent GPU previews with optional synchronized cameras."""
    def __init__(self,editor):
        super().__init__(editor)
        if editor.mode!='models' or editor.mesh is None:
            raise ValueError('Open a model in the GPU viewer first.')
        from qt_gpu_viewer import QtGLModelCanvas, _NativeResult
        self.editor=editor
        self.setWindowTitle('Model Viewer 2.0 — Side-by-Side Comparison')
        self.resize(1250,730)
        self.setMinimumSize(760,450)
        root=QVBoxLayout(self)
        controls=QHBoxLayout()
        controls.addWidget(QLabel('Compare with'))
        self.choice=QComboBox()
        self.choice.addItems(sorted((p for p in editor.records if editor._category(p)=='models'),
                             key=str.casefold))
        self.choice.setMaxVisibleItems(25)
        controls.addWidget(self.choice,1)
        controls.addWidget(_button('Load Comparison',self.load_compare))
        self.sync=QCheckBox('Sync cameras')
        self.sync.setChecked(True)
        controls.addWidget(self.sync)
        controls.addWidget(_button('Save Comparison PNG…',self.screenshot))
        controls.addWidget(_button('Close',self.accept))
        root.addLayout(controls)
        captions=QHBoxLayout()
        self.left_label=QLabel('Current: '+editor.current)
        self.right_label=QLabel('Choose a second model')
        captions.addWidget(self.left_label,1)
        captions.addWidget(self.right_label,1)
        root.addLayout(captions)
        views=QHBoxLayout()
        self.left=QtGLModelCanvas(editor.mesh,editor.materials,self,
                                 prepared_geometry=editor._prepared_geometry)
        self.right=QtGLModelCanvas(editor.mesh,editor.materials,self,
                                  prepared_geometry=editor._prepared_geometry)
        views.addWidget(self.left,1)
        views.addWidget(self.right,1)
        root.addLayout(views,1)
        self.status=QLabel('Select a second model. Hold and drag to rotate; mouse wheel zooms.')
        root.addWidget(self.status)
        self._token=0
        self._pool=QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._signals=_NativeResult(self)
        self._signals.completed.connect(self._ready)
        self._timer=QTimer(self)
        self._timer.timeout.connect(self._sync_views)
        self._timer.start(70)
        if editor.current in [self.choice.itemText(i) for i in range(self.choice.count())]:
            self.choice.setCurrentText(editor.current)

    def _sync_views(self):
        if not self.sync.isChecked():
            return
        if (self.left.rotation!=self.right.rotation or
            self.left.pan!=self.right.pan or self.left.zoom!=self.right.zoom):
            self.right.rotation=list(self.left.rotation)
            self.right.pan=list(self.left.pan)
            self.right.zoom=self.left.zoom
            self.right.update()

    def load_compare(self):
        from async_model_resolve import resolve_model
        from qt_gpu_viewer import _NativeConversion
        rel=self.choice.currentText()
        if not rel:
            return
        workspace=self.editor.workspace
        projects=self.editor.manager.projects_path
        stage=self.editor.manager.stage_path
        textures=self.editor.texture_folder
        index=self.editor.preview_index
        ani=self.editor.loose_animation_index
        self._token+=1
        token=self._token
        self.status.setText('Loading '+rel+' in the background…')

        def resolve(_):
            return resolve_model(workspace,rel,projects_root=projects,
                stage_root=stage,texture_folder=textures,
                animation_index=ani,index=index),None

        self._pool.start(_NativeConversion(resolve,None,token,self._signals))

    def _ready(self,token,result,error):
        if token!=self._token:
            return
        if error:
            self.status.setText('Comparison model unavailable: '+error[:200])
            return
        loaded,_=result
        self.right.set_mesh(loaded['mesh'],loaded['materials'],
                            prepared_geometry=loaded['geometry'])
        self.right_label.setText('Comparison: '+self.choice.currentText())
        self.status.setText('Use Sync cameras to match the current model viewpoint.')

    def screenshot(self):
        filename,_=QFileDialog.getSaveFileName(
            self,'Save model comparison','Evolve-Model-Comparison.png',
            'PNG (*.png)')
        if not filename:
            return
        if not self.grab().save(filename,'PNG'):
            QMessageBox.warning(self,'Model comparison','Could not save PNG.')
