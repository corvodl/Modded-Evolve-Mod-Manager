"""Unreal Engine-inspired, dockable Level Editor 1.0 for Evolve Stage 2.

Viewport uses 3D-projected OBJECT POSITION PROXIES, not actual world terrain.
Only CryEngine .lyr placed-object transforms are editable. No live game or
navigation/collision/lighting recompilation is implied.
"""
from __future__ import annotations

from collections import defaultdict
from threading import Event, Thread
from types import SimpleNamespace
import math
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QPointF, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QBrush, QFont, QAction
from PySide6.QtWidgets import (
    QMainWindow, QApplication, QWidget, QFrame, QLabel, QVBoxLayout,
    QHBoxLayout, QSplitter, QPushButton, QToolButton, QTreeWidget,
    QTreeWidgetItem, QLineEdit, QDoubleSpinBox, QComboBox, QFormLayout,
    QGroupBox, QMessageBox, QScrollArea, QSizePolicy, QFileDialog,
    QCompleter, QInputDialog)

from level_editor_core import LevelProject, PlacedObject
from qt_level_scene_render import LevelGLViewport


class LevelSceneViewport(QWidget):
    """Interactive camera/selection proxy view; no fake 3D terrain mesh."""
    selected = Signal(str, int)
    moved = Signal(str, int, object)

    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.project = project
        self.setMinimumSize(300, 260)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setObjectName("levelSceneViewport")
        self.camera = [0., 0., 0.]
        self.yaw = math.radians(38)
        self.pitch = math.radians(32)
        self.zoom = 1.
        self.mode = "select"
        self.active = None
        self._press = None
        self._press_world = None
        self._mouse = None
        self._visible_points = []
        self._move_preview = None
        self.frame_all()

    def frame_all(self):
        points = self.project.objects
        if not points:
            return
        stride = max(1, len(points) // 4000)
        xx = sorted(p.pos[0] for p in points[::stride])
        yy = sorted(p.pos[1] for p in points[::stride])
        zz = sorted(p.pos[2] for p in points[::stride])
        middle = len(xx)//2
        self.camera = [xx[middle], yy[middle], zz[middle]]
        def width(seq):
            # Ignore extreme outliers so the majority of the scene is visible.
            return max(1., seq[min(len(seq)-1,int(len(seq)*.9))] -
                       seq[min(len(seq)-1,int(len(seq)*.1))])
        span = max(width(xx), width(yy), width(zz), 1.)
        self.zoom = min(10., max(.00004, min(max(300,self.width()), max(240,self.height()))/(span*1.7)))
        self.update()

    def focus_object(self, obj):
        self.camera = list(obj.pos)
        self.zoom = max(self.zoom, .05) if self.zoom < .05 else self.zoom
        self.update()

    def _project(self, point):
        dx,dy,dz = (point[i] - self.camera[i] for i in range(3))
        ca,sa = math.cos(self.yaw), math.sin(self.yaw)
        cp,sp = math.cos(self.pitch), math.sin(self.pitch)
        x = dx*ca - dy*sa
        y = dx*sa*sp + dy*ca*sp - dz*cp
        return QPointF(self.width()*.5 + x*self.zoom,
                       self.height()*.55 + y*self.zoom)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#34363d"))
        self._draw_grid(painter)
        self._visible_points.clear()
        # A large map can have tens of thousands of objects. Bounded proxy
        # rendering keeps the GUI responsive without dropping XML data.
        objects = self.project.objects
        stride = max(1, math.ceil(len(objects)/6000))
        visible = 0
        for obj in objects[::stride]:
            point = self._project(obj.pos)
            if point.x() < -12 or point.x() > self.width()+12 or point.y() < -12 or point.y() > self.height()+12:
                continue
            is_selected = self.active == (obj.layer,obj.index)
            color = QColor("#f5ac43" if is_selected else
                           "#75b8cb" if "light" in obj.kind.lower() else
                           "#93b58a" if "vegetation" in obj.kind.lower() else
                           "#c8ced3")
            painter.setPen(QPen(QColor("#16171a"), 1))
            painter.setBrush(QBrush(color))
            size = 5 if not is_selected else 10
            painter.drawEllipse(point, size/2, size/2)
            self._visible_points.append((obj,point))
            visible += 1
        if self.active:
            for obj in objects:
                if (obj.layer,obj.index) == self.active:
                    current = obj
                    if self._move_preview is not None:
                        current = PlacedObject(obj.layer,obj.index,obj.name,obj.kind,
                                               obj.geometry,self._move_preview,obj.rotate,obj.scale)
                    p = self._project(current.pos)
                    self._draw_gizmo(painter,p)
                    painter.setPen(QColor("#ffe0a9"))
                    painter.drawText(p+QPointF(14,-17),obj.name[:40])
                    break
        painter.setPen(QColor("#e7e7e9"))
        painter.setFont(QFont("Segoe UI", 10))
        painter.fillRect(10,10,290,52,QColor(23,23,27,205))
        painter.drawText(20,28,"Perspective  ·  Lit  ·  Object Positions")
        painter.setPen(QColor("#bebfc8"))
        painter.setFont(QFont("Segoe UI", 8))
        painter.drawText(20,47,"CryEngine placement preview  •  Terrain not rendered")
        painter.fillRect(10,self.height()-34,300,25,QColor(23,23,27,205))
        painter.setPen(QColor("#d4d2db"))
        painter.drawText(18,self.height()-17,f"{visible:,} visible markers / {len(objects):,} objects")
        painter.end()

    def _draw_grid(self,painter):
        scale = max(.00001,self.zoom)
        # Keep grid spacing between ~40 and 130 px at any world scale.
        step = 10**math.floor(math.log10(70/scale))
        for mul in (1,2,5,10):
            if step*mul*scale > 35:
                step*=mul
                break
        painter.setPen(QPen(QColor("#51535a"),1))
        cx = round(self.camera[0]/step)*step
        cy = round(self.camera[1]/step)*step
        extent = 12*step
        for i in range(-12,13):
            x,y = cx+i*step,cy+i*step
            p1=self._project((x,cy-extent,0))
            p2=self._project((x,cy+extent,0))
            painter.drawLine(p1,p2)
            a=self._project((cx-extent,y,0))
            b=self._project((cx+extent,y,0))
            painter.drawLine(a,b)
        painter.setPen(QPen(QColor("#a25c66"),2))
        painter.drawLine(self._project((cx-extent,0,0)),
                         self._project((cx+extent,0,0)))
        painter.setPen(QPen(QColor("#659b74"),2))
        painter.drawLine(self._project((0,cy-extent,0)),
                         self._project((0,cy+extent,0)))

    def _draw_gizmo(self,painter,point):
        for dx,dy,color,label in ((34,0,"#e47175","X"),(-14,-26,"#81b78d","Y"),(0,32,"#81a9e2","Z")):
            tip = point+QPointF(dx,dy)
            painter.setPen(QPen(QColor(color),3))
            painter.drawLine(point,tip)
            painter.setPen(QColor(color))
            painter.drawText(tip+QPointF(3,-3),label)

    def mousePressEvent(self,event):
        self._press=event.position()
        self._mouse=event.position()
        self._move_preview=None
        if event.button()==Qt.LeftButton:
            candidates=[(abs(p.x()-self._mouse.x())+abs(p.y()-self._mouse.y()),obj)
                        for obj,p in self._visible_points]
            if candidates:
                dist,obj=min(candidates,key=lambda item:item[0])
                if dist<22:
                    self.active=(obj.layer,obj.index)
                    self._press_world=obj.pos
                    self.selected.emit(obj.layer,obj.index)
                    self.update()
                    return
        super().mousePressEvent(event)

    def mouseMoveEvent(self,event):
        if self._mouse is None:
            return
        now=event.position()
        dx,dy=now.x()-self._mouse.x(),now.y()-self._mouse.y()
        self._mouse=now
        if event.buttons() & Qt.RightButton:
            self.yaw += dx*.006
            self.pitch=max(.12,min(1.45,self.pitch+dy*.006))
        elif event.buttons() & Qt.MiddleButton:
            ca,sa=math.cos(self.yaw),math.sin(self.yaw)
            self.camera[0] -= (dx*ca+dy*sa)/max(.00001,self.zoom)
            self.camera[1] -= (-dx*sa+dy*ca)/max(.00001,self.zoom)
        elif (event.buttons() & Qt.LeftButton and self.mode=="move"
              and self.active and self._press_world is not None):
            dpx=now.x()-self._press.x()
            dpy=now.y()-self._press.y()
            ca,sa=math.cos(self.yaw),math.sin(self.yaw)
            cp=max(.15,math.sin(self.pitch))
            x=(dpx*ca+dpy*sa/cp)/max(.00001,self.zoom)
            y=(-dpx*sa+dpy*ca/cp)/max(.00001,self.zoom)
            self._move_preview=(self._press_world[0]+x,
                                self._press_world[1]+y,self._press_world[2])
        self.update()

    def mouseReleaseEvent(self,event):
        if (event.button()==Qt.LeftButton and self._move_preview is not None
                and self.active is not None):
            self.moved.emit(self.active[0],self.active[1],self._move_preview)
        self._move_preview=None
        self._press=None
        self._press_world=None
        self._mouse=None
        self.update()

    def wheelEvent(self,event):
        steps=event.angleDelta().y()/120.
        self.zoom=max(.00001,min(3000.,self.zoom*(1.13**steps)))
        self.update()


class UnrealStyleLevelEditor(QMainWindow):
    """UE4/5-like placement workspace for extracted Evolve Stage 2 levels."""
    mapCatalogReady = Signal(int,object)
    mapCopyReady = Signal(int,object,object)
    levelExportReady = Signal(object,object)

    def __init__(self,parent,workspace):
        super().__init__(parent)
        self.project=LevelProject(workspace)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setWindowTitle("Level Editor 3D Preview — "+self.project.level_name+" — Evolve")
        self.resize(1510,900)
        self.setMinimumSize(880,540)
        self.setObjectName("evolveLevelEditor")
        self.setStyleSheet("""
          QMainWindow#evolveLevelEditor, QWidget { background:#202126; color:#dbdfe5; }
          QTreeWidget, QLineEdit, QDoubleSpinBox, QComboBox {
            background:#2b2d32; color:#ebedf0; border:1px solid #43464b; }
          QTreeWidget::item:selected {background:#b16c35; color:white}
          QPushButton, QToolButton {padding:6px 10px; background:#31333a;
            border:1px solid #494a51; border-radius:3px; color:#e6e7ec}
          QPushButton:hover,QToolButton:hover {background:#55545a}
          QPushButton:checked,QToolButton:checked {background:#ad7135}
          QLabel#dockHeading { font-weight:600; color:#eeeeef; }
          QSplitter::handle { background:#393a41; }
        """)
        self.selected=None
        self._refreshing=False
        self._map_catalog_generation=0
        self._map_copy_generation=0
        self._map_copy_cancel=Event()
        self.mapCatalogReady.connect(self._apply_map_catalog)
        self.mapCopyReady.connect(self._complete_map_copy)
        self._export_cancel=Event()
        self._export_running=False
        self.levelExportReady.connect(self._level_export_complete)
        self._build_toolbar()
        self._build_workspace()
        self._populate()
        self.refresh_maps()
        self.statusBar().showMessage(
            "Loading source terrain and static meshes • tile textures and vegetation remain unsupported")

    def _action(self,label,callback,tip=None,checkable=False):
        action=QAction(label,self)
        action.setCheckable(checkable)
        if tip:action.setToolTip(tip)
        action.triggered.connect(callback)
        return action

    def _build_toolbar(self):
        menu=self.menuBar()
        file=menu.addMenu("File")
        file.addAction(self._action("Save Level Layers",self.save,"Ctrl+S"))
        file.addAction(self._action("Open Extracted Project…",self.open_project))
        export_menu=menu.addMenu("Export")
        export_menu.addAction(self._action("Entire Map to Blender (.glb)…",self.export_level))
        export_menu.addAction(self._action("Entire Map to Native Blender (.blend)…",lambda: self.export_level(native=True)))
        export_menu.addAction(self._action("Selected Object to Blender (.glb)…",lambda: self.export_level(selected_only=True)))
        maps=menu.addMenu("Maps")
        maps.addAction(self._action("Choose Extracted Map Project…", self.open_map_picker))
        edit=menu.addMenu("Edit")
        edit.addAction(self._action("Undo",self.undo))
        edit.addAction(self._action("Redo",self.redo))
        view=menu.addMenu("View")
        view.addAction(self._action("Frame All Objects",self.frame_all))
        view.addAction(self._action("Frame Selected",self.frame_selected))
        view.addAction(self._action("Perspective 3D", lambda: self._projection("Perspective")))
        view.addAction(self._action("Orthographic 3D", lambda: self._projection("Orthographic")))
        view.addAction(self._action("Scene Coverage / Diagnostics", self.scene_diagnostics))
        view.addAction(self._action("Terrain / Vegetation Source Metadata", self.inspect_level_sources))
        help_menu=menu.addMenu("Help")
        help_menu.addAction(self._action("Level Editor Limitations",self.limitations))
        toolbar=self.addToolBar("Main")
        toolbar.setMovable(False)
        for label,callback in (
            ("Save Current",self.save),("Undo",self.undo),("Redo",self.redo),
            ("Focus",self.frame_selected),("Frame All",self.frame_all)):
            tool=toolbar.addAction(label)
            tool.triggered.connect(callback)
        toolbar.addSeparator()
        for mode in ("select","move"):
            a=toolbar.addAction("Select" if mode=="select" else "Move (XY)")
            a.setCheckable(True)
            a.setChecked(mode=="select")
            a.triggered.connect(lambda checked=False,m=mode:self._set_mode(m))
            if not hasattr(self,"mode_buttons"): self.mode_buttons={}
            self.mode_buttons[mode]=a
        toolbar.addSeparator()
        info=toolbar.addWidget(QLabel("  Perspective  ▾     Lit  ▾    Show  ▾"))
        self.tool_label=info
        toolbar.addSeparator()
        toolbar.addWidget(QLabel(" Map: "))
        self.map_selector=QComboBox(self)
        self.map_selector.setObjectName("levelMapSelector")
        self.map_selector.setEditable(True)
        self.map_selector.setInsertPolicy(QComboBox.NoInsert)
        self.map_selector.setMinimumWidth(210)
        self.map_selector.activated.connect(self._map_selected)
        toolbar.addWidget(self.map_selector)
        toolbar.addAction("Refresh Maps",self.refresh_maps)
        toolbar.addSeparator()
        toolbar.addWidget(QLabel("   RENDER  •  Source-size depth-tested mesh instances   "))

    def _panel(self,title):
        wrapper=QFrame()
        lay=QVBoxLayout(wrapper)
        lay.setContentsMargins(5,5,5,5)
        lay.setSpacing(4)
        label=QLabel(title)
        label.setObjectName("dockHeading")
        lay.addWidget(label)
        return wrapper,lay

    def _build_workspace(self):
        vertical=QSplitter(Qt.Vertical)
        horizontal=QSplitter(Qt.Horizontal)
        vertical.addWidget(horizontal)
        self.setCentralWidget(vertical)

        left,lay=self._panel("MODES")
        self.left_tools={}
        for name,label in (("select","Select Objects"),("move","Move (XY)")):
            button=QToolButton()
            button.setText(label)
            button.setCheckable(True)
            button.setChecked(name=="select")
            button.clicked.connect(lambda checked=False,m=name:self._set_mode(m))
            lay.addWidget(button)
            self.left_tools[name]=button
        lay.addWidget(QLabel("LAYERS"))
        self.layers_list=QTreeWidget()
        self.layers_list.setHeaderHidden(True)
        self.layers_list.itemClicked.connect(self._choose_layer)
        lay.addWidget(self.layers_list,1)
        tip=QLabel("Right-drag: orbit\nMiddle-drag: pan\nWheel: dolly\nLeft-click: pick mesh\nMove: XY drag (local root objects only)\nRotation, scale and material: Details.")
        tip.setWordWrap(True)
        lay.addWidget(tip)
        horizontal.addWidget(left)

        from app_runtime import DATA_HOME
        manager = self.parent()
        manager_game = getattr(manager, "_find_game_root", None)
        game_root = manager_game() if callable(manager_game) else None
        stage_root = getattr(manager, "stage_path", None)
        self.viewport=LevelGLViewport(
            self.project, game=game_root, stage=stage_root,
            cache_root=DATA_HOME / "LevelDependencyCache")
        self.viewport.selected.connect(self.select_object)
        self.viewport.moved.connect(self._viewport_moved)
        self.viewport.statusChanged.connect(self._scene_status)
        self.viewport.materialsReady.connect(self._materials_ready)
        horizontal.addWidget(self.viewport)

        right=QSplitter(Qt.Vertical)
        outliner,outlay=self._panel("WORLD OUTLINER")
        self.filter=QLineEdit()
        self.filter.setPlaceholderText("Search objects and layers…")
        self.filter.textChanged.connect(self._populate_outliner)
        outlay.addWidget(self.filter)
        self.outliner=QTreeWidget()
        self.outliner.setHeaderLabels(("Label","Type"))
        self.outliner.setColumnWidth(0,250)
        self.outliner.itemSelectionChanged.connect(self._selected_outliner)
        outlay.addWidget(self.outliner,1)
        self.out_count=QLabel("")
        outlay.addWidget(self.out_count)
        right.addWidget(outliner)

        details,details_lay=self._panel("DETAILS")
        self.details_name=QLabel("No object selected")
        details_lay.addWidget(self.details_name)
        self.details_kind=QLabel("")
        self.details_kind.setWordWrap(True)
        details_lay.addWidget(self.details_kind)
        details_lay.addWidget(QLabel("TRANSFORM"))
        self.boxes={}
        for key,dims,default in (("Pos",3,(0,0,0)),("Rotate",4,(0,0,0,1)),
                                 ("Scale",3,(1,1,1))):
            row=QHBoxLayout()
            row.addWidget(QLabel({"Pos":"Location","Rotate":"Rotation (quat)",
                                  "Scale":"Scale"}[key]))
            spins=[]
            for i in range(dims):
                box=QDoubleSpinBox()
                box.setDecimals(4)
                box.setRange(-1e8,1e8)
                box.setSingleStep(1 if key=="Pos" else .1)
                box.setValue(default[i])
                box.setMinimumWidth(50)
                row.addWidget(box,1)
                spins.append(box)
            details_lay.addLayout(row)
            self.boxes[key]=spins
        commit=QPushButton("Apply Transform")
        commit.clicked.connect(self.apply_details)
        details_lay.addWidget(commit)
        self.geometry_label=QLabel("Model reference: —")
        self.geometry_label.setWordWrap(True)
        details_lay.addWidget(self.geometry_label)
        details_lay.addWidget(QLabel("MATERIAL  •  exact game-relative path"))
        self.material_override=QLineEdit()
        self.material_override.setPlaceholderText("objects/…/material  (optional .mtl)")
        details_lay.addWidget(self.material_override)
        self.material_hint=QLabel("Choose a verified .mtl from imported map/shared assets; type to filter.")
        self.material_hint.setWordWrap(True)
        details_lay.addWidget(self.material_hint)
        change_material=QPushButton("Apply Material Override")
        change_material.clicked.connect(self.apply_material_override)
        details_lay.addWidget(change_material)
        self.scene_info = QLabel("Scene hierarchy: unavailable")
        self.scene_info.setWordWrap(True)
        details_lay.addWidget(self.scene_info)
        details_lay.addStretch()
        right.addWidget(details)
        right.setSizes([470,320])
        horizontal.addWidget(right)
        horizontal.setSizes([188,960,348])

        browser,browser_layout=self._panel("CONTENT BROWSER")
        controls=QHBoxLayout()
        self.path_label=QLabel("Extracted workspace  /  "+self.project.level_name)
        controls.addWidget(self.path_label)
        controls.addStretch()
        for name,fn in (("Save All",self.save),("Open Folder",self.open_folder),
                        ("Help",self.limitations)):
            b=QPushButton(name);b.clicked.connect(fn);controls.addWidget(b)
        browser_layout.addLayout(controls)
        self.content=QTreeWidget()
        self.content.setHeaderLabels(("Content","Type","Location"))
        self.content.itemDoubleClicked.connect(self._open_browser_asset)
        self.content.setAlternatingRowColors(True)
        browser_layout.addWidget(self.content,1)
        vertical.addWidget(browser)
        vertical.setSizes([680,175])

    def _scene_status(self, status):
        self.statusBar().showMessage(status, 0)

    def _materials_ready(self, paths):
        completer=QCompleter(list(paths), self.material_override)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        completer.setCompletionMode(QCompleter.PopupCompletion)
        self.material_override.setCompleter(completer)
        self.material_hint.setText(
            f"{len(paths):,} unique MTL paths indexed. "
            "Type to choose an exact game-relative material; blank resets to source.")

    def _available_projects(self):
        """Discover extracted editable maps, without following symlinks."""
        parent=self.project.workspace.parent
        projects={self.project.workspace.name:self.project.workspace}
        try:
            for item in sorted(parent.iterdir(),key=lambda p:p.name.casefold()):
                if item.is_dir() and not item.is_symlink() and LevelProject.contains_levels(item):
                    projects[item.name]=item
        except OSError:
            pass
        return projects

    def refresh_maps(self):
        """Show extracted projects immediately; discover game/library maps off-thread."""
        self._map_projects=self._available_projects()
        self.map_selector.blockSignals(True)
        self.map_selector.clear()
        for name,path in sorted(self._map_projects.items(),key=lambda pair:pair[0].casefold()):
            self.map_selector.addItem(name+"  ·  Ready",str(path))
        self.map_selector.setCurrentIndex(
            self.map_selector.findData(str(self.project.workspace)))
        self.map_selector.blockSignals(False)
        self._set_map_completer()
        self._map_catalog_generation += 1
        token = self._map_catalog_generation
        game = getattr(self.viewport,"game_root",None)
        workspaces = tuple(self._map_projects.values())
        def scan():
            try:
                from app_runtime import DATA_HOME
                from asset_library import LIBRARY_HOME
                from level_dependency_loader import discover_map_status
                status = discover_map_status(
                    game,library_roots=(LIBRARY_HOME,DATA_HOME/"LevelDependencyCache"),
                    projects=workspaces)
            except (OSError, ValueError) as exc:
                status = {"_error":str(exc)[:180]}
            try:
                self.mapCatalogReady.emit(token,status)
            except RuntimeError:
                pass
        Thread(target=scan,name="Evolve-Map-Catalog",daemon=True).start()

    def _set_map_completer(self):
        completer=QCompleter(
            [self.map_selector.itemText(i) for i in range(self.map_selector.count())],
            self.map_selector)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        self.map_selector.setCompleter(completer)

    def _apply_map_catalog(self,token,status):
        if token != self._map_catalog_generation:
            return
        if "_error" in status:
            self.statusBar().showMessage("Map catalog: "+status["_error"],8000)
            return
        known = {self.map_selector.itemData(i) for i in range(self.map_selector.count())}
        current = str(self.project.workspace)
        self.map_selector.blockSignals(True)
        for map_id,entry in status.items():
            label = map_id.replace("_"," ").title()
            workspace = entry.get("workspace")
            target = workspace or "map:"+map_id
            if target in known:
                continue
            state = entry.get("status","unavailable").title()
            self.map_selector.addItem(label+"  ·  "+state,target)
            known.add(target)
        self.map_selector.setCurrentIndex(self.map_selector.findData(current))
        self.map_selector.blockSignals(False)
        self._set_map_completer()

    def _map_selected(self,index):
        target=self.map_selector.itemData(index)
        if not target:
            return
        if target.startswith("map:"):
            self._load_library_map(target[4:])
        else:
            self._map_copy_cancel.set()
            self._map_copy_generation += 1
            self.switch_map(Path(target))

    def _load_library_map(self,map_id):
        """Import/make an editable copy on a worker; retain previous map on failure."""
        from app_runtime import DATA_HOME
        from asset_library import LIBRARY_HOME
        from level_dependency_loader import ensure_map_archives,copy_editable_map_from_library
        self._map_copy_cancel.set()
        self._map_copy_cancel=Event()
        self._map_copy_generation += 1
        token, cancel = self._map_copy_generation,self._map_copy_cancel
        game,stage=self.viewport.game_root,self.viewport.stage_root
        destination=self.project.workspace.parent/("Level-"+map_id)
        self.statusBar().showMessage("Preparing editable map "+map_id+" in background…")
        self.map_selector.setEnabled(False)
        def job():
            try:
                if game is not None and stage is not None:
                    context=SimpleNamespace(
                        level_name=map_id,workspace=self.project.workspace,scene=())
                    ensure_map_archives(context,game,stage,
                                        DATA_HOME/"LevelDependencyCache",
                                        imported_root=LIBRARY_HOME,cancel=cancel)
                if cancel.is_set():
                    return
                folder=copy_editable_map_from_library(
                    map_id,destination,
                    library_roots=(LIBRARY_HOME,DATA_HOME/"LevelDependencyCache"),
                    cancel=cancel)
                prepared=LevelProject(folder)
                self.mapCopyReady.emit(token,prepared,None)
            except (OSError,ValueError,RuntimeError,InterruptedError) as exc:
                try:
                    self.mapCopyReady.emit(token,None,str(exc)[:250])
                except RuntimeError:
                    pass
        Thread(target=job,name="Evolve-Map-Project-Prep",daemon=True).start()

    def _complete_map_copy(self,token,project,error):
        if token != self._map_copy_generation:
            return
        self.map_selector.setEnabled(True)
        if error:
            QMessageBox.warning(self,"Cannot load discovered map",error)
            self.refresh_maps()
            return
        self.switch_map(project.workspace,prepared=project)

    def switch_map(self,workspace,*,prepared=None):
        """Swap projects in place with saved-edit and loader-generation protection."""
        try:
            target=Path(workspace).resolve(strict=True)
            if target==self.project.workspace:
                return True
            next_project=prepared if prepared is not None else LevelProject(target)
        except (OSError,ValueError) as exc:
            QMessageBox.warning(self,"Map load failed",str(exc))
            self.refresh_maps()
            return False
        if self.project.dirty_layers():
            choice=QMessageBox.question(
                self,"Unsaved level edits",
                "Save changed layers before switching maps?",
                QMessageBox.Save|QMessageBox.Discard|QMessageBox.Cancel,
                QMessageBox.Cancel)
            if choice==QMessageBox.Cancel:
                self.refresh_maps()
                return False
            if choice==QMessageBox.Save:
                try:
                    self.project.save()
                except (OSError,ValueError) as exc:
                    QMessageBox.critical(self,"Map save failed",str(exc))
                    self.refresh_maps()
                    return False
        self.project=next_project
        self.selected=None
        self.viewport.active=None
        self.viewport.project=next_project
        self.viewport.reload_scene()
        self.viewport.frame_all()
        self.filter.clear()
        self.material_override.clear()
        self.material_override.setCompleter(None)
        self.details_name.setText("No object selected")
        self.details_kind.clear()
        self.geometry_label.setText("Model reference: —")
        self.scene_info.setText("Scene hierarchy data unavailable")
        self.material_hint.setText("Indexing selected map materials…")
        self._populate()
        self._dirty_state()
        self.refresh_maps()
        return True

    def open_map_picker(self):
        self.refresh_maps()
        self.map_selector.setFocus()
        self.map_selector.showPopup()

    def export_level(self,selected_only=False,native=False):
        """Export assembled source/compiled/shared map parts without UI stalls."""
        if self._export_running:
            QMessageBox.information(self,"Export in progress","Wait for the active level export.")
            return
        selection=None
        if selected_only:
            if self.selected is None:
                QMessageBox.information(self,"Nothing selected","Select an object first.")
                return
            layer,idx=self.selected
            record=self.project._node(layer,idx)
            guid=(record.get("Id") or record.get("EntityId")
                  or f"{layer}#{idx}").casefold()
            selection={guid}
        ext=".blend" if native else ".glb"
        suggested=str(self.project.workspace.parent/(self.project.level_name+ext))
        chosen,_=QFileDialog.getSaveFileName(
            self,"Export Blender Level Scene",suggested,
            "Blender Project (*.blend)" if native else "glTF binary (*.glb)")
        if not chosen:
            return
        source=Path(chosen)
        if source.suffix.casefold()!=ext:
            source=source.with_suffix(ext)
        self._export_cancel=Event()
        cancel=self._export_cancel
        self._export_running=True
        self.statusBar().showMessage("Assembling Blender scene from map PAK parts…")
        game,stage,cache=(self.viewport.game_root,self.viewport.stage_root,
                          self.viewport.cache_root)
        project=self.project
        def job():
            try:
                from level_blender_export import export_level_glb
                if native:
                    from blender_native_project import convert_glb_to_blend
                    from tempfile import TemporaryDirectory
                    with TemporaryDirectory(prefix="evolve-scene-",dir=source.parent) as temp:
                        intermediate=Path(temp)/"LevelScene.glb"
                        result=export_level_glb(
                            project,intermediate,game=game,stage=stage,
                            cache_root=cache,cancel=cancel,selection=selection)
                        if cancel.is_set():
                            raise InterruptedError("Level export cancelled.")
                        convert_glb_to_blend(intermediate,source)
                else:
                    result=export_level_glb(
                        project,source,game=game,stage=stage,
                        cache_root=cache,cancel=cancel,selection=selection)
                result["format"]="blend" if native else "glb"
                self.levelExportReady.emit(result,None)
            except (OSError,ValueError,InterruptedError,RuntimeError) as exc:
                self.levelExportReady.emit(None,str(exc)[:500])
        Thread(target=job,name="Evolve-Level-GLB-Export",daemon=True).start()

    def _level_export_complete(self,result,error):
        self._export_running=False
        if error:
            QMessageBox.warning(self,"Level export failed",error)
            return
        omitted=len(result.get("omitted",()))
        message=(f"Exported {result['objects']} placed mesh instances, "
                 f"{result['mesh_resources']} unique mesh resources and "
                 f"{'terrain' if result['terrain'] else 'no terrain'}.\n"
                 f"{omitted} referenced objects were not exported. "
                 "This GLB is a supported-geometry scene, not an entire "
                 "CryEngine level rebuild.")
        self.statusBar().showMessage("Blender scene GLB exported",8000)
        QMessageBox.information(self,"Level GLB exported",message)

    def _projection(self, mode):
        self.viewport.projection_mode = mode
        self.viewport.update()
        self.statusBar().showMessage("3D projection: " + mode, 4000)

    def inspect_level_sources(self):
        """Inspect metadata-only map sources in this editable project."""
        from level_source_metadata import inspect_workspace_sources
        import json
        try:
            sections=inspect_workspace_sources(self.project.workspace)
        except (ValueError,OSError) as exc:
            QMessageBox.warning(self,"Source inspection failed",str(exc))
            return
        if not sections:
            message=("This extracted workspace does not contain Heightmap.dat, "
                     "VegetationMap.dat or a .cry editor archive. They may live "
                     "in its complementary map PAK. Import it into the Asset Library "
                     "or open the appropriate copied map project.")
        else:
            message=json.dumps(sections,ensure_ascii=False,indent=2)[:13000]
        QMessageBox.information(
            self, "Level terrain / vegetation source metadata",
            message + "\\n\\nVerified source height samples can now be previewed "
            "as read-only terrain. Vegetation instances, cooked terrain tiles "
            "and terrain painting remain unsupported.")

    def scene_diagnostics(self):
        decoded = len(self.viewport.assets)
        coverage = sum(1 for item in self.project.scene
                       if (item.model.casefold(), item.material.casefold())
                       in self.viewport.assets)
        missing = "\n".join(f"• {reason}: {count}" for reason,count
                            in sorted(self.viewport._issues.items(),
                                      key=lambda x:-x[1])[:25])
        terrain=getattr(self.viewport,"terrain",None)
        terrain_status=(f"{terrain.width}×{terrain.height} source elevations; "
                        f"{len(terrain.indices)//3:,} triangles (stride {terrain.stride}); "
                        f"source: {terrain.source}"
                        if terrain is not None else "no supported decoded source terrain")
        dep=getattr(self.viewport,"_dependency_report",{})
        archive_status=(f"{len(dep.get('imported',()))} imported; "
                        f"{len(dep.get('reused',()))} reused; "
                        f"{len(dep.get('ambiguous',()))} ambiguous; "
                        f"{len(dep.get('missing',()))} missing"
                        if dep else "not checked")
        dep_missing=("\n".join("• "+v for v in (
                     dep.get('missing',())[:12] + dep.get('ambiguous',())[:8] +
                     dep.get('errors',())[:8])) if dep else "")
        QMessageBox.information(
            self, "Rendered scene diagnostics",
            f"Map: {self.project.level_name}\n"
            f"Placed scene nodes: {len(self.project.scene):,}\n"
            f"Rendered instances: {coverage:,}\n"
            f"Decoded material variants: {decoded:,}\n"
            f"Hierarchy warnings: {len(self.project.scene_diagnostics):,}\n"
            f"Terrain: {terrain_status}\n"
            f"PAK dependencies: {archive_status}\n"
            f"Render/lookup problems:\n{missing or '(none reported)'}\n\n"
            f"Missing/conflicting providers:\n{dep_missing or '(none reported)'}\n\n"
            "Source terrain is rendered read-only without authored terrain tile "
            "textures. Unsupported vegetation, roads, prefabs and compiled "
            "terrain are not replaced by fabricated geometry.")

    def apply_material_override(self):
        if self.selected is None:
            return
        layer,index = self.selected
        try:
            self.project.apply_material(
                layer, index, self.material_override.text())
            self.viewport.reload_scene()
            self.select_object(layer,index)
            self._dirty_state()
        except (ValueError,OSError) as exc:
            QMessageBox.warning(self, "Material override invalid", str(exc))

    def _set_mode(self,mode):
        self.viewport.mode=mode
        for key,action in self.mode_buttons.items():
            action.setChecked(key==mode)
        for key,button in self.left_tools.items():
            button.setChecked(key==mode)
        self.statusBar().showMessage(
            "Move: drag a selected marker in the viewport XY plane." if mode=="move"
            else "Select placed objects in the viewport or World Outliner.")

    def _populate(self):
        self.layers_list.clear()
        for name in (self.project.known_layers or sorted(self.project.layers)):
            row=QTreeWidgetItem([Path(name).stem])
            row.setToolTip(0,name)
            row.setData(0,Qt.UserRole,name)
            self.layers_list.addTopLevelItem(row)
        self._populate_outliner()
        self._populate_content()
        self.viewport.update()

    def _populate_outliner(self):
        self.outliner.blockSignals(True)
        self.outliner.clear()
        needle=self.filter.text().casefold()
        group={}
        count=0
        max_items=25000
        for obj in self.project.objects:
            if count>=max_items:
                break
            if needle and needle not in (obj.name+" "+obj.kind+" "+obj.layer+" "+obj.geometry).casefold():
                continue
            node = self.project._node(obj.layer,obj.index)
            real_layer = node.get("Layer") or Path(obj.layer).stem
            group_key = (obj.layer,real_layer)
            if group_key not in group:
                header=QTreeWidgetItem([real_layer,"Layer"])
                header.setToolTip(0,obj.layer)
                self.outliner.addTopLevelItem(header)
                group[group_key]=header
            item=QTreeWidgetItem([obj.name[:90],obj.kind[:45]])
            item.setData(0,Qt.UserRole,(obj.layer,obj.index))
            group[group_key].addChild(item)
            count+=1
            if self.selected==(obj.layer,obj.index):
                item.setSelected(True)
        if needle or len(group)<15:
            self.outliner.expandAll()
        self.out_count.setText(
            f"{count:,} visible / {len(self.project.objects):,} placed objects")
        self.outliner.blockSignals(False)

    def _populate_content(self):
        # Unlike the World Outliner (placed entities), Content Browser
        # reflects actual files and folders from the editable level workspace.
        from evolve_pak_workspace import load_workspace, normalized_name
        self.content.clear()
        nodes={}
        catalog=load_workspace(self.project.workspace)
        filenames=sorted(
            (normalized_name(entry["path"]) for entry in catalog["entries"]),
            key=str.casefold)
        for relative in filenames[:22000]:
            parts=relative.split("/")
            parent=None
            for n,name in enumerate(parts[:-1]):
                key="/".join(parts[:n+1])
                if key not in nodes:
                    folder=QTreeWidgetItem([name,"Folder",key])
                    if parent is None:
                        self.content.addTopLevelItem(folder)
                    else:
                        parent.addChild(folder)
                    nodes[key]=folder
                parent=nodes[key]
            extension=Path(relative).suffix.casefold()
            label=("CryEngine Layer" if extension==".lyr" else
                   "CryEngine Map" if extension==".cry" else
                   "Texture" if extension in (".dds",".tif") else
                   "Map Metadata" if extension in (".xml",".dat",".lua") else
                   extension.lstrip(".").upper() or "File")
            row=QTreeWidgetItem([parts[-1],label,relative])
            row.setData(0,Qt.UserRole,relative)
            if parent is None:
                self.content.addTopLevelItem(row)
            else:
                parent.addChild(row)
        self.path_label.setText(
            f"Extracted workspace / {self.project.level_name} • "
            f"{len(filenames):,} files • {len(self.project.layers)} layers • "
            f"{len(self.project.objects):,} objects • "
            f"{sum(bool(x.model) for x in self.project.scene):,} model references")

    def _open_browser_asset(self,item,column=0):
        relative=item.data(0,Qt.UserRole)
        if isinstance(relative,str) and relative.casefold().endswith(".lyr"):
            self.filter.setText(Path(relative).stem)
            self.statusBar().showMessage(
                "Filtered World Outliner to " + Path(relative).stem)

    def _choose_layer(self,item):
        name=item.data(0,Qt.UserRole)
        self.filter.setText(Path(name).stem)

    def _selected_outliner(self):
        for item in self.outliner.selectedItems():
            data=item.data(0,Qt.UserRole)
            if isinstance(data,(tuple,list)) and len(data)==2:
                self.select_object(*data)
                break

    def select_object(self,layer,index):
        self.selected=(layer,index)
        self.viewport.active=self.selected
        obj=self.project.get(layer,index)
        self.details_name.setText(obj.name)
        node = self.project._node(layer,index)
        self.details_kind.setText(
            "Type: "+obj.kind+"  •  Layer: "+(node.get("Layer") or Path(layer).stem))
        self.geometry_label.setText("Model reference: "+(obj.geometry or "Not specified"))
        self.material_override.setText(node.get("Material") or "")
        guid = (node.get("Id") or node.get("EntityId") or f"{layer}#{index}").casefold()
        scene = next((n for n in self.project.scene if n.id == guid), None)
        if scene:
            pos = ", ".join(f"{v:.3f}" for v in scene.world.pos)
            self.scene_info.setText(
                "Hierarchy: " + (scene.parent_id or "root") +
                "\\nWorld position: " + pos +
                "\\nMaterial override: " + (scene.material or "(source default)") +
                "\\nRender status: " + scene.status)
        else:
            self.scene_info.setText("Scene hierarchy data unavailable")
        self._refreshing=True
        for key,values in (("Pos",obj.pos),("Rotate",obj.rotate or (0,0,0,1)),
                           ("Scale",obj.scale or (1,1,1))):
            for box,val in zip(self.boxes[key],values):
                box.setValue(val)
        self._refreshing=False
        self.viewport.update()

    def _viewport_moved(self,layer,index,position):
        try:
            node = self.project._node(layer,index)
            if node.get("Parent") or node.get("Group"):
                raise ValueError("Parented objects have local transforms; edit position in the Details panel.")
            self.project.apply(layer,index,"Pos",position)
            self.select_object(layer,index)
            self._dirty_state()
        except ValueError as exc:
            QMessageBox.warning(self,"Move failed",str(exc))

    def apply_details(self):
        if self.selected is None:
            return
        layer,index=self.selected
        try:
            for key,boxes in self.boxes.items():
                vals=tuple(box.value() for box in boxes)
                if key=="Scale" and any(abs(x)<.000001 for x in vals):
                    raise ValueError("Object scale cannot contain zero.")
                self.project.apply(layer,index,key,vals)
            self.select_object(layer,index)
            self._dirty_state()
        except (ValueError,OSError) as exc:
            QMessageBox.warning(self,"Transform invalid",str(exc))

    def _dirty_state(self):
        if self.project.dirty_layers():
            self.setWindowTitle("Level Editor 1.0 — "+self.project.level_name+
                                " * — Evolve")
        else:
            self.setWindowTitle("Level Editor 1.0 — "+self.project.level_name+" — Evolve")
        self.viewport.update()

    def undo(self):
        if self.project.undo():
            self.viewport.reload_scene()
            self._refresh_selected()
            self._dirty_state()

    def redo(self):
        if self.project.redo():
            self.viewport.reload_scene()
            self._refresh_selected()
            self._dirty_state()

    def _refresh_selected(self):
        if self.selected:
            self.select_object(*self.selected)

    def frame_all(self):
        self.viewport.frame_all()

    def frame_selected(self):
        if self.selected:
            self.viewport.focus_object(self.project.get(*self.selected))
        else:
            self.frame_all()

    def save(self):
        try:
            names=self.project.dirty_layers()
            if not names:
                self.statusBar().showMessage("No level-object changes to save.")
                return
            if QMessageBox.question(
                self,"Save edited level layers?",
                f"Save {len(names)} changed .lyr layer(s) in the EXTRACTED PROJECT?\n\n"
                "Original signed game PAKs remain unchanged. No terrain, "
                "navigation mesh or collision data is rebuilt. Backups "
                "are kept under .level-editor-backups.",
                QMessageBox.Yes | QMessageBox.No,QMessageBox.No
            ) != QMessageBox.Yes:
                return
            saved=self.project.save()
            self._dirty_state()
            self.statusBar().showMessage(
                f"Saved {len(saved)} editable .lyr layers. Build/repack from the manager "
                "and test offline; compatibility is not verified.")
        except (OSError,ValueError) as exc:
            QMessageBox.critical(self,"Level save failed",str(exc))

    def open_folder(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.project.root)))

    def open_project(self):
        folder=QFileDialog.getExistingDirectory(
            self,"Open extracted level PAK project",str(self.project.workspace.parent))
        if not folder:
            return
        try:
            other=UnrealStyleLevelEditor(self.parent(),folder)
            other.show()
        except (ValueError,OSError) as exc:
            QMessageBox.warning(self,"Cannot open level project",str(exc))

    def limitations(self):
        QMessageBox.information(
            self, "Level Editor • supported scope",
            "Real depth-tested geometry from verified static CryEngine .cgf/.skinm/.chrm streams "
            "is shown with world transforms and decoded diffuse materials when available. "
            "Unresolved/unsupported content is listed in Scene Diagnostics.\\n\\n"
            "Edit source-layer transforms and explicit material overrides in a copied workspace; "
            "undo/redo and safe atomic layer saves retain original backups.\\n\\n"
            "Not yet implemented: terrain tiles/painting, vegetation instances, procedural roads, "
            "animated CDF characters, source-designer remeshing, game runtime collision, "
            "lighting/navigation rebuilds or engine-ready export. The editor must not be treated "
            "as a CryEngine map compiler.")

    def closeEvent(self,event):
        if self.project.dirty_layers():
            answer=QMessageBox.question(
                self,"Unsaved level edits",
                "Discard unsaved object transforms? Select No to return and save.",
                QMessageBox.Yes | QMessageBox.No,QMessageBox.No)
            if answer!=QMessageBox.Yes:
                event.ignore()
                return
        self._map_copy_cancel.set()
        self._export_cancel.set()
        self._map_catalog_generation += 1
        event.accept()
