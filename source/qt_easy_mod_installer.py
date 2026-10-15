"""Drag/drop reviewed mod import; stages only via the existing ModProfiles worker."""
from __future__ import annotations

from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFileDialog,
    QComboBox, QMessageBox, QInputDialog
)

from easy_mod_install import review_package, import_reviewed_mod, enabled_target_conflicts
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
        self.setWindowTitle("Install Mod — Review & Stage")
        self.setObjectName("easyModInstaller")
        self.setAcceptDrops(True)
        self.resize(675, 445)
        self.setMinimumWidth(565)
        layout = QVBoxLayout(self)
        self.drop_hint = QLabel("Drop a signed .pak or a ZIP containing one .pak here")
        self.drop_hint.setObjectName("easyModDropZone")
        self.drop_hint.setAlignment(Qt.AlignCenter)
        self.drop_hint.setMinimumHeight(68)
        self.drop_hint.setWordWrap(True)
        layout.addWidget(self.drop_hint)
        row = QHBoxLayout()
        self.browse_button = QPushButton("Browse…")
        self.browse_button.clicked.connect(self.browse)
        row.addWidget(self.browse_button)
        self.selected_label = QLabel("No package selected")
        self.selected_label.setWordWrap(True)
        row.addWidget(self.selected_label, 1)
        layout.addLayout(row)
        layout.addWidget(QLabel(
            "Packages are checked against the verified signing stage. "
            "Unrecognized, ambiguous, unsigned, or multi-PAK ZIP packages are rejected. "
            "The game installation is not modified."))
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
        layout.addWidget(self.summary, 1)
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

    def choose(self, path):
        self.selected_path = Path(path)
        self.review = None
        self.install_button.setEnabled(False)
        self.summary.setText("Select Review Mod to validate the selected package.")
        self.selected_label.setText(self.selected_path.name)

    def inspect(self):
        if not self.selected_path:
            QMessageBox.information(self, "Mod Installer", "Select a mod package first.")
            return
        if exports_busy(self):
            return
        source = self.selected_path
        stage = self.manager.stage_path
        self.preview_button.setEnabled(False)
        def complete(review):
            self.preview_button.setEnabled(True)
            if source != self.selected_path:
                return
            self.review = review
            self.summary.setText(
                f"Signed target: {review.target}\n"
                f"Package: {review.source.name}\n"
                f"Changed assets: {len(review.changed_entries)}\n"
                f"Size: {review.size / 1048576:.1f} MiB\n"
                f"SHA256: {review.sha256[:20]}…\n"
                + "\n".join("  " + entry for entry in review.changed_entries[:6])
                + ("\n  …additional changes" if len(review.changed_entries) > 6 else ""))
            self.install_button.setEnabled(True)
        def failed(message):
            self.preview_button.setEnabled(True)
            self.review = None
            self.install_button.setEnabled(False)
            QMessageBox.warning(self, "Cannot import this mod", message)
        begin_file_operation(
            self, "Verify signed Evolve PAK", "Inspecting package and signed PAK contents…",
            lambda _report, cancelled: review_package(source, stage) if not cancelled()
            else (_ for _ in ()).throw(InterruptedError("Cancelled")),
            on_success=complete, on_error=failed)

    def import_and_stage(self):
        review = self.review
        pid = self.profile_choice.currentData()
        if review is None:
            QMessageBox.information(self, "Mod Installer", "Review the package first.")
            return
        try:
            profile = next((p for p in self.store.list_profiles() if p["id"] == pid), None)
            if pid and profile is None:
                raise ValueError("Selected Mods Collection was removed. Refresh the list.")
            conflicts = enabled_target_conflicts(profile, review.target) if profile else ()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Mod Installer", str(exc))
            return
        changes = "\n".join("• " + x for x in review.changed_entries[:12])
        note = ("An enabled mod already targets this PAK. It will be disabled "
                "in this collection; its files will be retained.\n\n" if conflicts else "")
        if QMessageBox.question(
                self, "Review & Stage Mod",
                f"{note}Validated target: {review.target}\n"
                f"Modified entries: {len(review.changed_entries)}\n"
                f"{changes}\n\n"
                "Import to your Mods Collection, update any conflicting enabled "
                "entry, then stage using verified backups? Game files are not changed.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        # The first install creates a sensible default collection only
        # after approval, so new users don't need a separate setup wizard.
        if pid is None:
            try:
                pid = self.store.create("My Mods")
                self.reload_profiles(pid)
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "Mod Installer", str(exc))
                return
        self.install_button.setEnabled(False)
        def complete(imported):
            try:
                # Disable collisions only after an immutable import succeeds,
                # and before any staging worker starts. Never overwrite files.
                for entry in conflicts:
                    self.store.set_enabled(pid, entry["id"], False)
                self.accept()
                self.manager.apply_mod_profile(pid)
            except Exception as exc:
                QMessageBox.warning(
                    self, "Mod saved, staging requires attention",
                    str(exc) + "\nThe imported mod remains in its collection.")
        def failed(message):
            self.install_button.setEnabled(True)
            QMessageBox.warning(self, "Mod Installer", message)
        begin_file_operation(
            self, "Import signed mod", "Saving verified mod to local library…",
            lambda _report, cancelled: import_reviewed_mod(
                review, self.manager.stage_path, self.profiles_root, pid)
            if not cancelled() else (_ for _ in ()).throw(InterruptedError("Cancelled")),
            on_success=complete, on_error=failed)
