"""Fast native Qt launch dashboard; does not enumerate PAKs on first paint.

A real landing page rather than another instruction page. The navigation
tiles use the *same* page dispatcher as the drawer and icon rail, so the
Developer tools are deliberately absent from Home.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import (
    QFrame, QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QGridLayout, QScrollArea, QSizePolicy
)

from qt_navigation_icons import navigation_icon
from qt_ui_components import SurfacePanel

NAVIGATION = (
    ("Modding", "Modding", "Open a project, edit assets and sign a PAK."),
    ("Asset Library", "Asset Library", "Browse textures, models, animation and GFX."),
    ("Instructions", "Instructions", "First-time setup and safe modding workflow."),
    ("Settings", "Settings", "Appearance, paths, updates and maintenance."),
    ("What's New", "What's New", "Changes included in the latest release."),
    ("Credits", "Credits", "Project information and acknowledgments."),
)


class HomeDashboard(QScrollArea):
    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.setObjectName("homeDashboard")
        self.manager = manager
        self.setFrameShape(QFrame.NoFrame)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        wrapper = QWidget()
        wrapper.setObjectName("homeDashboardContent")
        outer = QVBoxLayout(wrapper)
        outer.setContentsMargins(18, 14, 18, 22)
        outer.setSpacing(16)

        hero = QFrame()
        hero.setObjectName("homeHero")
        header = QVBoxLayout(hero)
        header.setContentsMargins(28, 25, 28, 24)
        header.setSpacing(10)
        eyebrow = QLabel("EVOLVE STAGE 2  /  MODDING WORKSPACE")
        eyebrow.setObjectName("homeEyebrow")
        header.addWidget(eyebrow)
        title = QLabel("Make something different.")
        title.setObjectName("homeHeroTitle")
        header.addWidget(title)
        summary = QLabel(
            "A unified workspace for CryEngine assets, visual editing, "
            "signed PAK builds, and safe offline testing.")
        summary.setObjectName("homeHeroDescription")
        summary.setWordWrap(True)
        header.addWidget(summary)
        hero_actions = QHBoxLayout()
        hero_actions.setSpacing(8)
        for index, (label, page, primary) in enumerate((
                ("Modding", "Modding", True),
                ("Asset Library", "Asset Library", False),
                ("Instructions", "Instructions", False))):
            button = QPushButton(label)
            button.setObjectName("primary" if primary else "homeSecondaryAction")
            button.setIcon(navigation_icon(page, size=19))
            button.setIconSize(QSize(19, 19))
            button.setMinimumHeight(38)
            button.clicked.connect(
                lambda _checked=False, dest=page:
                manager.open_page(manager.section_index(dest)))
            hero_actions.addWidget(button)
        hero_actions.addStretch(1)
        header.addLayout(hero_actions)
        outer.addWidget(hero)

        # Launch shortcuts are visible without opening any other tab.
        launch_panel = SurfacePanel("PLAY EVOLVE")
        launch_panel.setObjectName("homeQuickPlay")
        launch_description = QLabel(
            "Choose an offline modded session or launch with original game files. "
            "Original files are restored automatically after a modded session.")
        launch_description.setWordWrap(True)
        launch_panel.content.addWidget(launch_description)
        play_actions = QHBoxLayout()
        for title, method, icon_name, primary in (
                ("Play with Mods", manager.launch, "Play with Mods", True),
                ("Play without Mods", manager.play_without_mods, "Play without Mods", False)):
            button = QPushButton(title)
            button.setObjectName("primary" if primary else "homeSecondaryAction")
            button.setAccessibleName(title)
            button.setIcon(navigation_icon(icon_name, size=20))
            button.setMinimumHeight(40)
            button.clicked.connect(method)
            play_actions.addWidget(button)
        play_actions.addStretch(1)
        launch_panel.content.addLayout(play_actions)
        outer.addWidget(launch_panel)

        # Client updates have their own clearly labelled Home section.
        client_card = SurfacePanel("EVOLVE CLIENT")
        client_card.setObjectName("homeClientSection")
        self.client_version_label = QLabel("Client executable not configured")
        self.client_version_label.setObjectName("homeStatusValue")
        client_card.content.addWidget(self.client_version_label)
        outer.addWidget(client_card)

        main = QHBoxLayout()
        main.setSpacing(15)
        workspace = QWidget()
        workspace.setObjectName("homeWorkspace")
        left = QVBoxLayout(workspace)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(11)
        head = QLabel("Your workspace")
        head.setObjectName("homeSectionTitle")
        left.addWidget(head)
        description = QLabel("Everything in the manager, one click away.")
        description.setObjectName("muted")
        left.addWidget(description)

        grid = QGridLayout()
        grid.setSpacing(10)
        self.tiles = {}
        for i, (page, label, note) in enumerate(NAVIGATION):
            tile = QPushButton()
            tile.setObjectName("homeTile")
            tile.setAccessibleName("Open " + page)
            tile.setToolTip(note)
            tile.setMinimumHeight(106)
            tile.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            box = QHBoxLayout(tile)
            box.setContentsMargins(14, 10, 14, 10)
            box.setSpacing(12)
            icon = QLabel()
            icon.setObjectName("homeTileIcon")
            icon.setPixmap(navigation_icon(page, size=27).pixmap(27, 27))
            icon.setFixedWidth(31)
            box.addWidget(icon, 0, Qt.AlignTop)
            text = QVBoxLayout()
            text.setSpacing(4)
            heading = QLabel(label)
            heading.setObjectName("homeTileTitle")
            text.addWidget(heading)
            detail = QLabel(note)
            detail.setObjectName("homeTileDescription")
            detail.setWordWrap(True)
            text.addWidget(detail)
            text.addStretch(1)
            box.addLayout(text, 1)
            # QPushButton handles the entire tile, not decorative child labels.
            tile.clicked.connect(
                lambda _checked=False, dest=page:
                manager.open_page(manager.section_index(dest)))
            grid.addWidget(tile, i // 2, i % 2)
            self.tiles[page] = tile
        left.addLayout(grid)
        main.addWidget(workspace, 5)

        sidebar = QWidget()
        sidebar.setObjectName("homeStatusColumn")
        right = QVBoxLayout(sidebar)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(11)
        heading = QLabel("At a glance")
        heading.setObjectName("homeSectionTitle")
        right.addWidget(heading)
        hint = QLabel("Installed game and client status; launch actions are separate.")
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        right.addWidget(hint)
        self.status = {"client_version": self.client_version_label}
        for key, title_text, fallback in (
                ("game", "Game installation", "Not selected"),
                ("game_version", "Game version", "Not detected"),
                ("stage", "Signing stage", "Not prepared"),
                ("project", "Editing project", "None selected"),
                ("output", "Last signed PAK", "No recent build")):
            box = SurfacePanel(title_text)
            box.setObjectName("homeStatusCard")
            label = QLabel(fallback)
            label.setObjectName("homeStatusValue")
            label.setWordWrap(True)
            box.content.addWidget(label)
            self.status[key] = label
            right.addWidget(box)
        right.addStretch(1)
        main.addWidget(sidebar, 2)
        outer.addLayout(main)
        outer.addStretch(1)
        # Qt requires the content layout to be installed before setWidget;
        # otherwise the initial dashboard may render blank until resized.
        self.setWidget(wrapper)

    def refresh_status(self):
        """Cheap file-existence checks only; no source PAK hashing/scanning."""
        manager = self.manager
        game = (manager.game_root or manager.settings.get("game_root", "")
                or manager._find_game_root())
        from installed_game_versions import installed_versions
        details = installed_versions(game, manager.swap_path)
        self.status["game_version"].setText(
            details["game"] or
            ("Version not reported" if details["game_found"]
             else "Game executable not found"))
        installed = details["client"]
        self.status["client_version"].setText(
            (installed or ("Version not reported" if details["client_found"]
                           else "Client executable not configured"))
            + (" · " + details["client_name"] if details["client_name"] else ""))
        self.status["game"].setText(
            "Configured" if game and Path(game).is_dir()
            else "Not configured — use Settings")
        stage = Path(manager.stage_path)
        self.status["stage"].setText(
            "Stage ready" if (stage / "rekey_plan.json").is_file()
            else "Setup required")
        workspace = Path(manager.current_workspace) if manager.current_workspace else None
        self.status["project"].setText(
            workspace.name if workspace and workspace.is_dir()
            else "No active project")
        output = Path(manager.output_pak) if manager.output_pak else None
        self.status["output"].setText(
            output.name if output and output.is_file()
            else "No recent signed build")

