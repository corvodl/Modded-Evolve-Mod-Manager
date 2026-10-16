"""Drag/drop reviewed mod import; stages only via the existing ModProfiles worker."""
from __future__ import annotations

from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFileDialog,
    QComboBox, QMessageBox, QInputDialog, QScrollArea
)

from easy_mod_install import enabled_target_conflicts
from easy_mod_bundle import (review_mod_bundle, review_legacy_folder,
                             import_reviewed_bundle)
from mod_install_receipts import write_import_receipt
from mod_install_safety import estimate_bundle_space
from mod_profiles import ModProfiles
from qt_background_exports import begin_file_operation, exports_busy


class EasyModInstaller(QDialog):
    def __init__(self, manager, profiles_root):
        super().__init__(manager)
        self.manager = manager
        self.store = ModProfiles(profiles_root)
        self.profiles_root = Path(profiles_root)
        self.selected_path = None
        self.review = None
        self.space_preview = None
        self.legacy_folder = False
        self.setWindowTitle("Install Mod — Review & Stage")
        self.setObjectName("easyModInstaller")
        self.setAcceptDrops(True)
        self.resize(675, 445)
        self.setMinimumWidth(565)
        layout = QVBoxLayout(self)
        self.drop_hint = QLabel("Drop signed PAKs or a ZIP bundle (up to 24 PAKs)")
        self.drop_hint.setObjectName("easyModDropZone")
        self.drop_hint.setAlignment(Qt.AlignCenter)
        self.drop_hint.setMinimumHeight(68)
        self.drop_hint.setWordWrap(True)
        layout.addWidget(self.drop_hint)
        row = QHBoxLayout()
        self.browse_button = QPushButton("Browse…")
        self.browse_button.clicked.connect(self.browse)
        row.addWidget(self.browse_button)
        legacy = QPushButton("Import Older Mods…")
        legacy.setObjectName("easyModLegacyImport")
        legacy.clicked.connect(self.browse_legacy)
        row.addWidget(legacy)
        self.selected_label = QLabel("No package selected")
        self.selected_label.setWordWrap(True)
        row.addWidget(self.selected_label, 1)
        layout.addLayout(row)
        layout.addWidget(QLabel(
            "Packages are checked against the verified signing stage. "
            "Unsigned, unknown or incompatible signed PAKs are rejected. "
            "Bundles must pass as a group; game files are never modified by import."))
        layout.addWidget(QLabel("Installed Mods collection"))
        profiles_row = QHBoxLayout()
        self.profile_choice = QComboBox()
        self.profile_choice.setObjectName("easyModProfileChoice")
        profiles_row.addWidget(self.profile_choice, 1)
        new = QPushButton("New Collection…")
        new.clicked.connect(self.new_profile)
        profiles_row.addWidget(new)
        layout.addLayout(profiles_row)
        self.summary = QLabel("Choose a mod to inspect.")
        self.summary.setObjectName("easyModReview")
        self.summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.summary.setWordWrap(True)
        details_scroll = QScrollArea()
        details_scroll.setWidgetResizable(True)
        details_scroll.setFrameShape(QScrollArea.NoFrame)
        details_scroll.setWidget(self.summary)
        details_scroll.setMinimumHeight(120)
        layout.addWidget(details_scroll, 1)
        self.space_label = QLabel("Space needed: review a package first.")
        self.space_label.setObjectName("modDiskSpacePreview")
        self.space_label.setWordWrap(True)
        layout.addWidget(self.space_label)
        actions = QHBoxLayout()
        self.preview_button = QPushButton("Review Mod")
        self.preview_button.setObjectName("easyModReviewButton")
        self.preview_button.clicked.connect(self.inspect)
        actions.addWidget(self.preview_button)
        self.install_button = QPushButton("Confirm & Stage Mod")
        self.install_button.setObjectName("easyModStageButton")
        self.install_button.setEnabled(False)
        self.install_button.clicked.connect(self.import_and_stage)
        actions.addWidget(self.install_button)
        actions.addStretch()
        library = QPushButton("Manage Installed Mods…")
        library.clicked.connect(self.open_library)
        actions.addWidget(library)
        cancel = QPushButton("Close")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        layout.addLayout(actions)
        self.reload_profiles()

    def reload_profiles(self, select=None):
        chosen = select or self.profile_choice.currentData()
        self.profile_choice.clear()
        for item in self.store.list_profiles():
            self.profile_choice.addItem(item["name"], item["id"])
        for i in range(self.profile_choice.count()):
            if self.profile_choice.itemData(i) == chosen:
                self.profile_choice.setCurrentIndex(i)
                break

    def new_profile(self):
        name, ok = QInputDialog.getText(self, "New Mods Collection", "Collection name:")
        if ok:
            try:
                pid = self.store.create(name)
                self.reload_profiles(pid)
            except Exception as error:
                QMessageBox.warning(self, "Mod Installer", str(error))

    def open_library(self):
        self.manager.open_mod_profiles()
        self.reload_profiles()

    def dragEnterEvent(self, event):
        files = event.mimeData().urls() if event.mimeData().hasUrls() else []
        if len(files) == 1 and files[0].isLocalFile() and Path(
                files[0].toLocalFile()).suffix.casefold() in (".pak", ".zip"):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        files = event.mimeData().urls()
        if len(files) == 1:
            self.choose(Path(files[0].toLocalFile()))
            event.acceptProposedAction()

    def browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select signed mod package", "",
            "Evolve Mods (*.pak *.zip)")
        if path:
            self.choose(Path(path))

    def browse_legacy(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Choose older manager Mods folder (not EvolveGame)")
        if folder:
            self.choose(Path(folder), legacy=True)

    def choose(self, path, *, legacy=False):
        self.selected_path = Path(path)
        self.legacy_folder = legacy
        self.review = None
        self.space_preview = None
        self.install_button.setEnabled(False)
        self.space_label.setText("Space needed: review a package first.")
        self.summary.setText("Select Review Mod to validate the selected package.")
        self.selected_label.setText(self.selected_path.name)

    def _review_summary(self, bundle):
        lines = ["Package: " + bundle.source.name,
                 f"Verified signed PAKs: {len(bundle.entries)}",
                 f"Modified assets: {sum(len(x.changed_entries) for x in bundle.entries)}"]
        reused = sum(
            (self.profiles_root / "ImportedMods" / entry.sha256 /
             entry.pak_filename).is_file() for entry in bundle.entries)
        if reused:
            lines.append(f"Existing library matches: {reused}  ·  hashes rechecked on import")
        for entry in bundle.entries:
            lines.append(
                f"Compatible target: {entry.target}  ·  {entry.size / 1048576:.1f} MiB"
                f"  ·  SHA256 {entry.sha256[:16]}…")
            lines.extend("    " + name for name in entry.changed_entries[:4])
            if len(entry.changed_entries) > 4:
                lines.append(f"    … {len(entry.changed_entries)-4} more asset(s)")
        return "\n".join(lines)

    def inspect(self):
        if not self.selected_path:
            QMessageBox.information(self, "Mod Installer", "Select a package first.")
            return
        if exports_busy(self):
            return
        source = self.selected_path
        legacy = self.legacy_folder
        stage = self.manager.stage_path
        game = (getattr(self.manager, 'game_root', None) or
                getattr(self.manager, '_find_game_root', lambda: '')())
        self.preview_button.setEnabled(False)
        def complete(result):
            self.preview_button.setEnabled(True)
            if source != self.selected_path or legacy != self.legacy_folder:
                return
            bundle, space = result
            self.review = bundle
            self.space_preview = space
            self.summary.setText(self._review_summary(bundle))
            volumes = " · ".join(
                f"{required / 1024**2:.0f} MiB needed / {free / 1024**2:.0f} MiB free"
                for _, required, free in space.volumes)
            self.space_label.setText(
                f"Additional verified library data: {space.library_bytes / 1024**2:.0f} MiB. "
                f"Recovery backups: {space.recovery_bytes / 1024**2:.0f} MiB. "
                f"Staging temporary space: {space.stage_temp_bytes / 1024**2:.0f} MiB.\n"
                + volumes + ("\n" + "\n".join(space.warnings) if space.warnings else
                             "\nSufficient estimated space."))
            self.install_button.setEnabled(space.enough)
        def failed(message):
            self.preview_button.setEnabled(True)
            self.review = None
            self.space_preview = None
            self.install_button.setEnabled(False)
            QMessageBox.warning(self, "Cannot import this mod", message)
        def review_and_estimate(_report, cancelled):
            if cancelled():
                raise InterruptedError("Cancelled")
            bundle = (review_legacy_folder(source, stage, game)
                      if legacy else review_mod_bundle(source, stage))
            if cancelled():
                raise InterruptedError("Cancelled")
            estimate = estimate_bundle_space(
                bundle, stage, self.manager.swap_path, self.profiles_root)
            return bundle, estimate
        begin_file_operation(
            self, "Verify signed Evolve mod bundle",
            "Checking PAK signatures, target build and archive safety…",
            review_and_estimate,
            on_success=complete, on_error=failed)

    def import_and_stage(self):
        bundle = self.review
        pid = self.profile_choice.currentData()
        if bundle is None:
            QMessageBox.information(self, "Mod Installer", "Review the package first.")
            return
        try:
            profile = next((p for p in self.store.list_profiles() if p["id"] == pid), None)
            if pid and profile is None:
                raise ValueError("Selected Mods Collection no longer exists.")
            conflicts = {}
            if profile:
                for entry in bundle.entries:
                    for existing in enabled_target_conflicts(profile, entry.target):
                        if existing["sha256"] != entry.sha256:
                            conflicts[existing["id"]] = existing
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Mod Installer", str(exc))
            return
        # A second check catches low-space and stale-profile state after
        # users leave the preview open; no files have been copied yet.
        try:
            space = estimate_bundle_space(
                bundle, self.manager.stage_path, self.manager.swap_path,
                self.profiles_root)
        except (OSError, ValueError, RuntimeError) as exc:
            QMessageBox.warning(self, "Installation Preflight", str(exc))
            return
        if not space.enough:
            QMessageBox.warning(self, "Not enough disk space",
                                "\n".join(space.warnings))
            self.install_button.setEnabled(False)
            return
        changes = sum(len(x.changed_entries) for x in bundle.entries)
        note = (f"{len(conflicts)} enabled conflicting mod(s) will be disabled "
                "in this collection, not removed.\n\n" if conflicts else "")
        targets = "\n".join("• " + x.target for x in bundle.entries)
        if QMessageBox.question(
                self, "Review & Stage Mod Bundle",
                f"{note}Verified PAKs: {len(bundle.entries)}\n"
                f"Changed entries: {changes}\n{targets}\n\n"
                f"Additional library storage: {space.library_bytes / 1024**2:.0f} MiB; "
                f"recovery backups: {space.recovery_bytes / 1024**2:.0f} MiB.\n"
                "Import verified files to this collection, then stage using "
                "the existing recoverable PAK backup journal? "
                "Installed game files are not changed.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        if pid is None:
            try:
                pid = self.store.create("My Mods")
                self.reload_profiles(pid)
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "Mod Installer", str(exc))
                return
        self.install_button.setEnabled(False)
        def complete(outcome):
            try:
                ids, paths, already = outcome
                receipt = write_import_receipt(
                    self.profiles_root, pid, bundle, ids, existing=already)
                self.manager.apply_mod_profile(pid, receipt_id=receipt)
                self.accept()
            except Exception as exc:
                self.install_button.setEnabled(True)
                QMessageBox.warning(
                    self, "Imported, but staging is not confirmed",
                    str(exc) + "\nThe package remains in the Mods Collection. "
                    "Review it before staging again.")
        def failed(message):
            self.install_button.setEnabled(True)
            QMessageBox.warning(self, "Mod Installer", message)
        begin_file_operation(
            self, "Import signed mod bundle", "Saving verified mod files…",
            lambda _report, cancelled: import_reviewed_bundle(
                bundle, self.manager.stage_path, self.profiles_root, pid,
                replace_conflicts=bool(conflicts))
            if not cancelled() else (_ for _ in ()).throw(InterruptedError("Cancelled")),
            on_success=complete, on_error=failed)
