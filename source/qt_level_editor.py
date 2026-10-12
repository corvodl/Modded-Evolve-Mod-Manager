"""Unreal Engine-inspired, dockable Level Editor 1.0 for Evolve Stage 2.

Viewport uses 3D-projected OBJECT POSITION PROXIES, not actual world terrain.
Only CryEngine .lyr placed-object transforms are editable. No live game or
navigation/collision/lighting recompilation is implied.
"""
from __future__ import annotations

from collections import defaultdict
import math
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QPointF
from PySide6.QtGui import QColor, QPainter, QPen, QBrush, QFont, QAction
from PySide6.QtWidgets import (
    QMainWindow, QApplication, QWidget, QFrame, QLabel, QVBoxLayout,
    QHBoxLayout, QSplitter, QPushButton, QToolButton, QTreeWidget,
    QTreeWidgetItem, QLineEdit, QDoubleSpinBox, QComboBox, QFormLayout,
    QGroupBox, QMessageBox, QScrollArea, QSizePolicy, QFileDialog)

from level_editor_core import LevelProject, PlacedObject


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

    def __init__(self,parent,workspace):
        super().__init__(parent)
        self.project=LevelProject(workspace)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setWindowTitle("Level Editor 1.0 — "+self.project.level_name+" — Evolve")
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
        self._build_toolbar()
        self._build_workspace()
        self._populate()
        self.statusBar().showMessage(
            "Level Editor 1.0  |  .lyr transforms only  |  Terrain, collision and navigation not compiled")

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
        edit=menu.addMenu("Edit")
        edit.addAction(self._action("Undo",self.undo))
        edit.addAction(self._action("Redo",self.redo))
        view=menu.addMenu("View")
        view.addAction(self._action("Frame All Objects",self.frame_all))
        view.addAction(self._action("Frame Selected",self.frame_selected))
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
        toolbar.addWidget(QLabel("   PREVIEW  •  Object placement proxies   "))

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
        tip=QLabel("Right-drag: orbit\nMiddle-drag: pan\nWheel: zoom\n\nMove tool: drag an object's marker on XY.\nRotation and scale: Details.")
        tip.setWordWrap(True)
        lay.addWidget(tip)
        horizontal.addWidget(left)

        self.viewport=LevelSceneViewport(self.project)
        self.viewport.selected.connect(self.select_object)
        self.viewport.moved.connect(self._viewport_moved)
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
        max_items=7000
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
            self._refresh_selected()
            self._dirty_state()

    def redo(self):
        if self.project.redo():
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
            self,"Level Editor 1.0",
            "Unreal-style layout for CryEngine level placement edits.\n\n"
            "Supported: browse .lyr layers, select placed objects, inspect object "
            "attributes, move XY position markers, change XYZ position, "
            "quaternion rotation and XYZ scale, undo/redo, and safely save "
            "changed layers to an extracted PAK workspace.\n\n"
            "Not yet supported: terrain rendering/sculpting, model placement "
            "gizmos with collision-aware snapping, compiled physics, "
            "navigation meshes, or lighting builds. The viewport shows "
            "object-location proxies, not fully reconstructed geometry.")

    def closeEvent(self,event):
        if self.project.dirty_layers():
            answer=QMessageBox.question(
                self,"Unsaved level edits",
                "Discard unsaved object transforms? Select No to return and save.",
                QMessageBox.Yes | QMessageBox.No,QMessageBox.No)
            if answer!=QMessageBox.Yes:
                event.ignore()
                return
        event.accept()
