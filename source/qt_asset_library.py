"""Qt Settings UI and background importer for the permanent extracted Asset Library."""
from __future__ import annotations
from pathlib import Path
import threading
import shutil
import json

from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QPushButton, QLabel, QHeaderView, QFileDialog, QMessageBox,
    QAbstractItemView, QProgressBar, QComboBox, QInputDialog)

from build_collections import (catalog, register_build, assign_archive,
                               remove_membership, update_location, compare_builds,
                               prepare_transfer)
from asset_library import (LIBRARY_HOME, import_pak, installed_defaults,
                           imported_archives, verify_library, remove_archive,
                           extracted_index, required_archive_status)


class AssetLibraryController(QObject):
    """One background operation at a time, surviving Settings dialog closure."""
    progress = Signal(str, int, int)
    finished = Signal(str, object, object)
    started = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.busy = False
        self.status = "Asset Library ready"
        self._cancel = threading.Event()
        self._mutex = threading.RLock()
        self.finished.connect(self._on_finished)
        self.progress.connect(self._on_progress)

    def _on_progress(self, name, current, total):
        self.status = "%s • %d / %d" % (name, current, total)

    def _on_finished(self, action, result, error):
        with self._mutex:
            self.busy = False
        self.status = (str(error) if error is not None else
                       "Asset Library " + action + " completed")

    def _start(self, action, callback):
        with self._mutex:
            if self.busy:
                return False
            self.busy = True
            self._cancel.clear()
        self.status = "Starting " + action + "…"
        self.started.emit(action)

        def work():
            try:
                data = callback()
                self.finished.emit(action, data, None)
            except Exception as exc:
                self.finished.emit(action, None, str(exc))
        threading.Thread(target=work, daemon=True,
                         name="AssetLibrary-" + action).start()
        return True

    def import_files(self, paths, stage, *, automatic=False, extra_keys=(), build_id=None):
        files = tuple(Path(p) for p in paths)
        if not files:
            return False

        def work():
            results = []
            for i, path in enumerate(files, 1):
                if self._cancel.is_set():
                    break
                self.progress.emit("Importing %d/%d: %s" %
                                   (i, len(files), path.name), 0, 1)
                try:
                    relative = None
                    if build_id is not None:
                        snapshot = catalog()["builds"][build_id]
                        if snapshot.get("location"):
                            try:
                                relative = path.resolve().relative_to(
                                    Path(snapshot["location"]).resolve()).as_posix()
                            except ValueError:
                                relative = None
                    result = import_pak(
                        path, stage=stage, extra_keys=extra_keys, build_id=build_id,
                        relative_archive_path=relative,
                        cancelled=self._cancel.is_set,
                        progress=lambda name, done, total:
                            self.progress.emit(name, done, total))
                    results.append(result)
                except Exception as exc:
                    results.append({"name": path.name, "status": "error",
                                    "error": str(exc)[:400]})
            return results

        return self._start("automatic-import" if automatic else "import", work)

    def import_all_game(self, scan, stage, *, build_id=None):
        # Reuse the same cancellable per-PAK importer. Completed archive
        # manifests are retained and unchanged ones are skipped on rerun.
        return self.import_files(scan.files, stage, automatic=False, build_id=build_id)

    def verify(self):
        return self._start(
            "verification",
            lambda: verify_library(cancelled=self._cancel.is_set,
                                   progress=lambda name, done, total:
                                       self.progress.emit(name, done, total)))

    def remove(self, identifier):
        return self._start("removal", lambda: remove_archive(identifier))

    def prepare_map_bundle(self, source_build, target_build, map_id, destination,
                           *, source_archive):
        from legacy_stage2_transfer import export_map_review_bundle
        return self._start(
            "map-review",
            lambda: export_map_review_bundle(
                source_build, target_build, map_id, destination,
                selected_archive=source_archive, cancelled=self._cancel.is_set,
                progress=lambda name, done, total:
                    self.progress.emit(name, done, total)))

    def export_legacy_build(self, source_id, target_id, destination, *,
                            source_archive=None, prefix="",
                            include_identical=False, plan_only=False,
                            protected_roots=()):
        """Run build-wide inventory or review export away from the UI thread."""
        from legacy_bulk_transfer import export_legacy_build_bundle
        return self._start(
            "build-transfer-review",
            lambda: export_legacy_build_bundle(
                source_id, target_id, destination,
                source_archive=source_archive, prefix=prefix,
                include_identical=include_identical, plan_only=plan_only,
                protected_roots=protected_roots,
                cancelled=self._cancel.is_set,
                progress=lambda title, done, total:
                    self.progress.emit(title, done, total)))

    def cancel(self):
        self._cancel.set()


class AssetLibraryDialog(QDialog):
    def __init__(self, parent, controller, stage, game=None):
        super().__init__(parent)
        self.controller = controller
        self.stage = Path(stage)
        self.game = Path(game) if game else None
        self.setWindowTitle("Extracted PAK Asset Library")
        self.resize(990, 560)
        lay = QVBoxLayout(self)
        info = QLabel(
            "PAKs are verified, fully extracted ONCE, and kept as ordinary "
            "read-only reference files. You may add any supported Evolve .pak. "
            "Character textures, skeletons, and CAF animations can then load "
            "without re-extracting the original archive.")
        info.setWordWrap(True)
        lay.addWidget(info)
        self.location = QLabel("Library: " + str(LIBRARY_HOME))
        self.location.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.location)
        self.build_filter = QComboBox()
        self.build_filter.currentIndexChanged.connect(self.refresh)
        self.register_button = QPushButton("Register Build…")
        self.register_button.clicked.connect(self.register_build_dialog)
        self.assign_button = QPushButton("Assign to Build")
        self.assign_button.clicked.connect(self.assign_selected)
        build_row = QHBoxLayout()
        build_row.addWidget(QLabel("Build:"))
        build_row.addWidget(self.build_filter, 1)
        build_row.addWidget(self.register_button)
        build_row.addWidget(self.assign_button)
        self.relocate_button = QPushButton("Update Location…")
        self.relocate_button.clicked.connect(self.relocate_build)
        self.detach_button = QPushButton("Remove Membership")
        self.detach_button.clicked.connect(self.detach_selected)
        self.compare_button = QPushButton("Compare Builds…")
        self.compare_button.clicked.connect(self.compare_snapshots)
        build_row.addWidget(self.relocate_button)
        build_row.addWidget(self.detach_button)
        build_row.addWidget(self.compare_button)
        lay.addLayout(build_row)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Archive", "Build", "Files", "Extracted Size", "Warnings", "Source"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        self.table.setSortingEnabled(True)
        lay.addWidget(self.table, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        lay.addWidget(self.progress)
        self.status = QLabel()
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        controls = QHBoxLayout()
        project_controls = QHBoxLayout()
        self.add_button = QPushButton("Add PAKs…")
        self.add_button.clicked.connect(self.add_paks)
        controls.addWidget(self.add_button)
        self.key_button = QPushButton("Add with Public Key…")
        self.key_button.setToolTip(
            "Import signed Evolve PAKs that use a different public verification key.")
        self.key_button.clicked.connect(self.add_with_key)
        controls.addWidget(self.key_button)
        self.all_button = QPushButton("Import ALL Game PAKs…")
        self.all_button.setToolTip(
            "Optional full EvolveGame import. Storage use can be very large; "
            "confirmation and cancel are provided. Existing imports are reused.")
        self.all_button.clicked.connect(self.import_all_game)
        controls.addWidget(self.all_button)
        self.default_button = QPushButton("Import shared + animation PAKs")
        self.default_button.clicked.connect(self.import_defaults)
        controls.addWidget(self.default_button)
        self.verify_button = QPushButton("Verify Library")
        self.verify_button.clicked.connect(self.verify)
        project_controls.addWidget(self.verify_button)
        self.project_button = QPushButton("Create Editable Mod Project…")
        self.project_button.setToolTip(
            "Make an independent mod project from this archive's verified "
            "custom-signed staging copy; the reference library stays unchanged.")
        self.project_button.clicked.connect(self.create_mod_project)
        project_controls.addWidget(self.project_button)
        self.map_button = QPushButton("Open Map in Level Editor…")
        self.map_button.clicked.connect(self.open_selected_map)
        project_controls.addWidget(self.map_button)
        self.transfer_button = QPushButton("Prepare Transfer to Stage 2…")
        self.transfer_button.setToolTip(
            "Write a reference-only transfer manifest for a selected asset. "
            "No archive or project files are changed.")
        self.transfer_button.clicked.connect(self.prepare_stage2_transfer)
        project_controls.addWidget(self.transfer_button)
        self.remove_button = QPushButton("Remove Selected…")
        self.remove_button.clicked.connect(self.remove_selected)
        project_controls.addWidget(self.remove_button)
        self.cancel_button = QPushButton("Cancel Import")
        self.cancel_button.clicked.connect(self.controller.cancel)
        project_controls.addWidget(self.cancel_button)
        controls.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        project_controls.addStretch(1)
        project_controls.addWidget(close)
        lay.addLayout(controls)
        lay.addLayout(project_controls)
        transfer_controls = QHBoxLayout()
        self.legacy_map_button = QPushButton("Prepare Legacy Map Review…")
        self.legacy_map_button.setToolTip(
            "Export a verified, map-scoped research bundle for a registered "
            "Legacy or early build. Does not mount, convert or deploy maps.")
        self.legacy_map_button.clicked.connect(self.prepare_legacy_map)
        transfer_controls.addWidget(self.legacy_map_button)
        self.model_transfer_button = QPushButton("Review Compatible Legacy Model…")
        self.model_transfer_button.setToolTip(
            "Optionally replace ONE matching model in an existing signed Stage 2 "
            "editing project after strict source/target hash and chunk checks.")
        self.model_transfer_button.clicked.connect(self.review_legacy_model)
        transfer_controls.addWidget(self.model_transfer_button)
        self.bulk_transfer_button = QPushButton("Export Legacy Build → Stage 2…")
        self.bulk_transfer_button.setToolTip(
            "Use ALL imported PAKs or one selected archive in a registered "
            "Legacy build to prepare a Stage 2-targeted review package. "
            "Never writes to the game or signs a PAK.")
        self.bulk_transfer_button.clicked.connect(self.export_legacy_build)
        transfer_controls.addWidget(self.bulk_transfer_button)
        transfer_controls.addStretch(1)
        lay.addLayout(transfer_controls)

        controller.progress.connect(self._progress)
        controller.finished.connect(self._finished)
        controller.started.connect(self._running)
        self.table.itemSelectionChanged.connect(self._sync)
        self.refresh()
        self._sync()

    def _selected_record(self):
        item = self.table.item(self.table.currentRow(), 0)
        if item is None:
            return None
        identifier = item.data(Qt.UserRole)
        return next((record for record in self.rows if record["id"] == identifier), None)

    def register_build_dialog(self):
        label, ok = QInputDialog.getText(self, "Register build", "Snapshot label:")
        if not ok or not label.strip():
            return
        family, ok = QInputDialog.getItem(
            self, "Build family", "Family:", ["early", "legacy", "stage2", "unknown"], 0, False)
        if ok:
            try:
                location = QFileDialog.getExistingDirectory(
                    self, "Optional archive collection directory (Cancel to skip)")
                identifier = register_build(label, family, location=location or None)
                self.refresh()
                self.build_filter.setCurrentIndex(self.build_filter.findData(identifier))
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "Build registration", str(exc))

    def assign_selected(self):
        record = self._selected_record()
        if record is None:
            return
        builds = catalog()["builds"]
        options = [(identifier, entry["label"]) for identifier, entry in builds.items()
                   if identifier != "unassigned"]
        if not options:
            QMessageBox.information(self, "Register a build",
                                    "Register a build snapshot before assigning archives.")
            return
        labels = [label + " [" + identifier[:8] + "]" for identifier, label in options]
        selected, ok = QInputDialog.getItem(self, "Assign archive to build",
                                             "Destination build:", labels, 0, False)
        if not ok:
            return
        build_id = options[labels.index(selected)][0]
        try:
            assign_archive(build_id, record["id"])
            self.refresh()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Build membership", str(exc))

    def relocate_build(self):
        identifier = self.build_filter.currentData()
        if identifier in (None, "all", "unassigned"):
            QMessageBox.information(self, "Choose build", "Select a registered build.")
            return
        location = QFileDialog.getExistingDirectory(
            self, "Choose new archive collection location (metadata only)")
        if location:
            try:
                update_location(identifier, location)
                self.refresh()
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "Build relocation", str(exc))

    def detach_selected(self):
        record = self._selected_record()
        identifier = self.build_filter.currentData()
        if record is None or identifier in (None, "all", "unassigned"):
            QMessageBox.information(self, "Choose membership",
                "Select a registered build and one of its archives.")
            return
        if QMessageBox.question(
            self, "Remove build membership?",
            "Detach this archive from the selected build? Its extracted files "
            "will remain available to other builds.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            remove_membership(identifier, record["id"])
            self.refresh()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Remove membership", str(exc))

    def compare_snapshots(self):
        builds = [(bid, entry["label"]) for bid, entry in catalog()["builds"].items()
                  if bid != "unassigned"]
        if len(builds) < 2:
            QMessageBox.information(self, "Compare builds",
                "Register at least two snapshots to compare assets.")
            return
        options = [label + " [" + bid[:8] + "]" for bid, label in builds]
        first, ok = QInputDialog.getItem(
            self, "Compare builds", "Source:", options, 0, False)
        if not ok:
            return
        second, ok = QInputDialog.getItem(
            self, "Compare builds", "Target:", options, 1, False)
        if not ok:
            return
        source = builds[options.index(first)][0]
        target = builds[options.index(second)][0]
        if source == target:
            QMessageBox.information(self, "Compare builds", "Choose two distinct builds.")
            return
        destination, _ = QFileDialog.getSaveFileName(
            self, "Export build comparison", str(Path.home() / "build-comparison.json"),
            "JSON files (*.json)")
        if not destination:
            return
        try:
            output = Path(destination).resolve()
            if output.exists() or output.is_relative_to(Path(LIBRARY_HOME).resolve()):
                raise ValueError("Choose a new comparison file outside the Asset Library.")
            rows = compare_builds(source, target)
            output.write_text(json.dumps({
                "source_build": source, "target_build": target,
                "hash_type": "normalized-extracted-sha256", "assets": rows
            }, indent=2), encoding="utf-8")
            QMessageBox.information(self, "Comparison exported",
                "%d asset paths compared. Review identical, changed, added, removed "
                "and unknown statuses in the JSON file." % len(rows))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Build comparison", str(exc))

    def refresh(self):
        builds = catalog()["builds"]
        previous = self.build_filter.currentData()
        self.build_filter.blockSignals(True)
        self.build_filter.clear()
        self.build_filter.addItem("All Builds (inventory)", "all")
        for build_id, data in builds.items():
            self.build_filter.addItem(data["label"] + " (" + data["family"] + ")", build_id)
        selected_index = self.build_filter.findData(previous)
        self.build_filter.setCurrentIndex(max(0, selected_index))
        self.build_filter.blockSignals(False)
        selected = self.build_filter.currentData()
        archives = imported_archives()
        self.rows = tuple(a for a in archives if selected == "all"
                          or a["id"] in builds[selected]["members"])
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self.rows))
        for row, data in enumerate(self.rows):
            names = [build["label"] for build in builds.values()
                     if data["id"] in build["members"]]
            values = (data["name"], ", ".join(names) or "Unassigned",
                      str(data["entries"]), "%.1f MiB" % (data["bytes"] / 1048576),
                      str(data["warnings"]), data["source"])
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if col == 0:
                    cell.setData(Qt.UserRole, data["id"])
                from pak_browser import archive_description
                cell.setToolTip(data["source"] + "\\n" + archive_description(data["name"]))
                self.table.setItem(row, col, cell)
        self.table.setSortingEnabled(True)
        total_bytes = sum(a["bytes"] for a in self.rows)
        coverage = required_archive_status(
            build_id=selected if selected != "all" else None)
        self.status.setText(
            "%d visible PAKs • %.1f MiB • Shared %d/6 • Animations %d/3 • %s" %
            (len(self.rows), total_bytes / 1048576,
             coverage["shared"], coverage["animations"], self.controller.status))
        self._sync()

    def _sync(self):
        running = self.controller.busy
        for button in (self.add_button, self.key_button, self.all_button,
                       self.default_button, self.verify_button,
                       self.remove_button, self.project_button, self.map_button,
                       self.register_button, self.assign_button, self.transfer_button,
                       self.relocate_button, self.detach_button, self.compare_button,
                       self.legacy_map_button, self.model_transfer_button,
                       self.bulk_transfer_button):
            button.setEnabled(not running)
        self.remove_button.setEnabled(not running and self._selected_record() is not None)
        self.project_button.setEnabled(not running and self._selected_record() is not None)
        self.map_button.setEnabled(not running and self._selected_record() is not None)
        self.cancel_button.setEnabled(running)

    def _import_build(self):
        selected = self.build_filter.currentData()
        return selected if selected not in (None, "all", "unassigned") else None

    def add_paks(self):
        names, _ = QFileDialog.getOpenFileNames(
            self, "Select Evolve PAKs to add",
            str(self.game or Path.home()), "Evolve PAKs (*.pak);;All files (*)")
        if names:
            self.controller.import_files(names, self.stage, build_id=self._import_build())

    def add_with_key(self):
        names, _ = QFileDialog.getOpenFileNames(
            self, "Select Evolve PAKs to add",
            str(self.game or Path.home()), "Evolve PAKs (*.pak)")
        if not names:
            return
        key, _ = QFileDialog.getOpenFileName(
            self, "Choose RSA public verification key (never a private key)",
            str(self.game or Path.home()), "RSA Public Key (*.bin *.der)")
        if key:
            self.controller.import_files(names, self.stage, extra_keys=(key,), build_id=self._import_build())

    def import_defaults(self):
        files = installed_defaults(self.game)
        if not files:
            QMessageBox.warning(self, "Game not found",
                "Select an installed EvolveGame with the shared / animation PAKs.")
            return
        self.controller.import_files(files, self.stage, build_id=self._import_build())

    def import_all_game(self):
        from asset_library_bulk import discover_collection_paks
        builds = catalog()["builds"]
        existing = [(bid, entry) for bid, entry in builds.items()
                    if bid != "unassigned"]
        choices = [entry["label"] + " [" + bid[:8] + "] (" + entry["family"] + ")"
                   for bid, entry in existing]
        choices.append("Register new collection…")
        selected_id = self.build_filter.currentData()
        default_index = next((i for i, (bid, _) in enumerate(existing)
                              if bid == selected_id), len(choices) - 1)
        selected, ok = QInputDialog.getItem(
            self, "Source build collection", "Import into build:",
            choices, default_index, False)
        if not ok:
            return
        if selected == choices[-1]:
            label, ok = QInputDialog.getText(
                self, "New build snapshot", "Editable snapshot label:")
            if not ok or not label.strip():
                return
            family, ok = QInputDialog.getItem(
                self, "Build family", "Source family:",
                ["stage2", "legacy", "early", "unknown"], 0, False)
            if not ok:
                return
            build_id = None
            new_snapshot = (label.strip(), family)
            default = str(self.game or Path.home())
        else:
            build_id, record = existing[choices.index(selected)]
            new_snapshot = None
            default = record.get("location") or str(self.game or Path.home())
        source = QFileDialog.getExistingDirectory(
            self, "Select SOURCE directory containing PAK archives", default)
        if not source:
            return
        try:
            scan = discover_collection_paks(source)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Collection scan", str(exc))
            return
        if not scan.files:
            QMessageBox.information(self, "Empty collection",
                "No .pak files found under the selected directory.")
            return
        if (build_id and record.get("location") and
                Path(record["location"]).resolve() != Path(source).resolve()):
            QMessageBox.warning(self, "Snapshot source differs",
                "This snapshot belongs to another source directory. "
                "Register a new build instead of silently changing its archive inventory.")
            return
        if QMessageBox.question(
            self, "Import all archives from chosen source?",
            "Source (read only):\\n%s\\n\\n%d PAKs found (%d already imported). "
            "%.2f GiB compressed input, %.2f GiB free library space. "
            "Archives are verified individually and unsupported files reported. "
            "The current game/deploy location will NOT change. Continue?" %
            (source, len(scan.files), scan.already_imported,
             scan.packed_bytes / (1024 ** 3),
             scan.available_bytes / (1024 ** 3)),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            if new_snapshot:
                build_id = register_build(*new_snapshot, location=source)
            elif not record.get("location"):
                update_location(build_id, source)
            self.refresh()
            idx = self.build_filter.findData(build_id)
            if idx >= 0:
                self.build_filter.setCurrentIndex(idx)
            self.controller.import_all_game(scan, self.stage, build_id=build_id)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Collection import", str(exc))

    def prepare_stage2_transfer(self):
        """Guided reference-only planning; no game archives are written."""
        selected = self._selected_record()
        source_id = self.build_filter.currentData()
        if selected is None or source_id in (None, "all", "unassigned"):
            QMessageBox.information(self, "Choose a source",
                "Select a registered historical build and a source archive.")
            return
        builds = catalog()["builds"]
        if builds[source_id]["family"] == "stage2":
            QMessageBox.information(self, "Historical source required",
                "Select an early or Legacy source build, not the Stage 2 target.")
            return
        targets = [(bid, entry["label"]) for bid, entry in builds.items()
                   if entry["family"] == "stage2" and bid != source_id]
        if not targets:
            QMessageBox.information(self, "Register target",
                "Register a separate Stage 2 build before preparing a transfer.")
            return
        labels = [label + " [" + bid[:8] + "]" for bid, label in targets]
        choice, ok = QInputDialog.getItem(
            self, "Target Stage 2 build", "Target snapshot:", labels, 0, False)
        if not ok:
            return
        target_id = targets[labels.index(choice)][0]
        from asset_library_preview import archive_entries
        from evolve_pak_workspace import normalized_name
        choices = {name.casefold(): name for name in archive_entries(selected["id"])}
        example = next(iter(choices.values()), "")
        pasted, ok = QInputDialog.getMultiLineText(
            self, "Select Legacy assets",
            "Enter one or more exact source paths from this PAK (one per line).\n"
            "Dependencies are reviewed automatically. Originals stay read-only.",
            example)
        if not ok or not pasted.strip():
            return
        try:
            requested = []
            for line in pasted.splitlines():
                if line.strip():
                    path = normalized_name(line.strip())
                    if path.casefold() not in choices:
                        raise ValueError("Path not found in this source PAK: " + path)
                    requested.append(choices[path.casefold()])
            if len(requested) > 256 or not requested:
                raise ValueError("Select between 1 and 256 source asset paths.")
            requested = list(dict.fromkeys(requested))
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid selection", str(exc)[:900])
            return
        target_project = QFileDialog.getExistingDirectory(
            self, "Select existing Stage 2 target project for review context")
        if not target_project:
            return
        baseline, ok = QInputDialog.getText(
            self, "Expected target baseline",
            "Expected target baseline SHA-256 (64 hex digits; not yet verified):")
        if not ok:
            return
        destination, _ = QFileDialog.getSaveFileName(
            self, "Save new transfer review manifest",
            str(Path.home() / "stage2-transfer-plan.json"), "JSON files (*.json)")
        if not destination:
            return
        try:
            output = Path(destination).absolute()
            verified_output = output.resolve(strict=False)
            if (output.exists() or output.is_symlink()
                    or any(part.is_symlink() for part in output.parents)
                    or verified_output.is_relative_to(Path(target_project).resolve())
                    or verified_output.is_relative_to(Path(LIBRARY_HOME).resolve())):
                raise ValueError("Choose a new external manifest outside projects and the Asset Library.")
            plan = prepare_transfer(
                source_id, target_id, requested,
                target_baseline_sha256=baseline.strip() or None,
                target_project=target_project)
            diagnostics = plan["dependency_diagnostics"]
            counts = {}
            for item in plan["mappings"]:
                counts[item["action"]] = counts.get(item["action"], 0) + 1
            description = (
                "%d virtual assets examined, including discovered references.\n"
                "Add review: %d | Replace review: %d | Reuse: %d\n"
                "Missing source: %d | Conflicts: %d\n"
                "Dependency diagnostics: %d incomplete / %d missing / %d ambiguous\n\n"
                "All statuses are reference-only. No archive has been signed, "
                "installed, or tested in Stage 2. Save this review manifest?"
            ) % (
                len(plan["mappings"]), counts.get("review-add", 0),
                counts.get("review-replace", 0), counts.get("reuse-candidate", 0),
                counts.get("missing-source", 0), counts.get("conflict", 0),
                len(diagnostics["incomplete"]), len(diagnostics["missing"]),
                len(diagnostics["conflicts"]))
            if QMessageBox.question(
                self, "Review transfer dependencies", description,
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
            with output.open("x", encoding="utf-8") as handle:
                json.dump(plan, handle, indent=2)
            if counts.get("missing-source", 0) or counts.get("conflict", 0):
                QMessageBox.warning(self, "Review saved — unresolved dependencies",
                    "The manifest is saved, but missing or conflicting source assets "
                    "block candidate export. Import and review those dependencies first.")
                return
            export = QMessageBox.question(
                self, "Export detached candidate files?",
                "Save a separate bundle of verified source file copies for review?\n"
                "This is NOT a signed PAK or importable Stage 2 mod, and does "
                "not modify either original game or library.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if export != QMessageBox.Yes:
                return
            directory = QFileDialog.getExistingDirectory(
                self, "Choose external parent directory for candidate bundle")
            if not directory:
                return
            from uuid import uuid4
            from transfer_candidates import export_candidate
            name = "stage2-review-" + uuid4().hex[:12]
            destination = Path(directory) / name
            # Large candidates must be copied off the GUI thread.
            if self.controller._start(
                "transfer-candidate",
                lambda: export_candidate(plan, destination,
                    cancelled=self.controller._cancel.is_set)):
                self.status.setText(
                    "Exporting verified copies into " + str(destination) +
                    ". Manifest is read-only; no Stage 2 package was built.")
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Transfer planning", str(exc)[:1200])

    def _historical_target(self, *, require_archive=True):
        """Require explicit build identities; never use the All Builds index."""
        source_id = self.build_filter.currentData()
        selected = self._selected_record()
        builds = catalog()["builds"]
        if (source_id in (None, "all", "unassigned")
                or source_id not in builds
                or builds[source_id]["family"] not in ("legacy", "early")):
            raise ValueError("Choose a registered Legacy/early source build in the Build filter.")
        if require_archive and (selected is None or
                selected["id"] not in builds[source_id]["members"]):
            raise ValueError("Select an imported Legacy PAK in that source build.")
        targets = [(bid, item["label"]) for bid, item in builds.items()
                   if item["family"] == "stage2" and bid != source_id]
        if not targets:
            raise ValueError("Register a separate Stage 2 target build first.")
        choices = [label + " [" + bid[:8] + "]" for bid, label in targets]
        choice, ok = QInputDialog.getItem(
            self, "Target Stage 2", "Target build:", choices, 0, False)
        if not ok:
            return None
        return source_id, targets[choices.index(choice)][0], selected

    def export_legacy_build(self):
        """Whole-build or selected-PAK source exports for registered builds.

        Exports are not signed or installed. A source library with 380 PAKs
        can be inventoried without asking users to type 270,000 file paths.
        """
        try:
            selection = self._historical_target(require_archive=False)
            if selection is None:
                return
            source_id, target_id, selected = selection
            from build_collections import catalog
            builds = catalog()["builds"]
            scope, ok = QInputDialog.getItem(
                self, "Export Legacy build → Stage 2",
                "Source scope:", [
                    "All imported PAKs in this Legacy build",
                    "Only the selected Legacy PAK",
                ], 0, False)
            if not ok:
                return
            archive_id = None
            if scope.startswith("Only"):
                if selected is None or selected["id"] not in builds[source_id]["members"]:
                    raise ValueError("Select the exact Legacy PAK you want to export.")
                archive_id = selected["id"]
            prefix, ok = QInputDialog.getText(
                self, "Optional path filter",
                "Virtual folder prefix (leave blank to include all assets):")
            if not ok:
                return
            choice, ok = QInputDialog.getItem(
                self, "Review export type", "Output:",
                ["Compare entire selection (manifest only, no files)",
                 "Export changed / absent-in-target files for review",
                 "Export ALL selected files for review (including identical)"],
                0, False)
            if not ok:
                return
            plan_only = choice.startswith("Compare")
            include_identical = choice.startswith("Export ALL")
            parent = QFileDialog.getExistingDirectory(
                self, "Choose output parent OUTSIDE both EvolveGame folders",
                str(Path.home()))
            if not parent:
                return
            scope_name = "all-paks" if archive_id is None else "pak-" + archive_id[:8]
            folder = ("Legacy-to-Stage2-Review-" +
                      source_id[:8] + "-" + target_id[:8] + "-" + scope_name)
            output = Path(parent) / folder
            if output.exists():
                raise ValueError("Output already exists; choose a different destination.")
            message = (
                "Source: %s\nTarget: %s\nScope: %s\n\n"
                "The operation compares ONLY imported build snapshots.\n"
                "A full-build payload export can copy many gigabytes. "
                "Files remain in a separate, verified review folder, "
                "not the game or signing stage.\n\n"
                "No source PAKs, game files, game modes or signed Stage 2 "
                "archives will be modified. No Legacy map is made playable.\n\n"
                "Continue?" %
                (builds[source_id]["label"], builds[target_id]["label"], scope)
            )
            if QMessageBox.question(
                self, "Confirm research-only transfer export", message,
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            ) != QMessageBox.Yes:
                return
            if not self.controller.export_legacy_build(
                source_id, target_id, output,
                source_archive=archive_id, prefix=prefix.strip(),
                include_identical=include_identical, plan_only=plan_only,
                protected_roots=(self.stage,)):
                raise ValueError("Finish the current import/export operation first.")
        except (ValueError, OSError, KeyError, RuntimeError) as exc:
            QMessageBox.warning(self, "Legacy build export", str(exc))

    def prepare_legacy_map(self):
        """Prepare compiled map research content only; no map registration."""
        try:
            context = self._historical_target()
            if context is None:
                return
            source_id, target_id, selected = context
            from level_asset_mount import level_from_source
            from legacy_stage2_transfer import plan_compiled_map
            map_id = level_from_source(selected["source"])
            if not map_id:
                map_id, ok = QInputDialog.getText(
                    self, "Legacy compiled map", "Exact map ID (for example Desert_Harsh):")
                if not ok or not map_id.strip():
                    return
                map_id = map_id.strip()
            plan = plan_compiled_map(
                source_id, target_id, map_id, selected_archive=selected["id"])
            if QMessageBox.question(
                self, "Prepare Legacy map review only?",
                ("%s: %d imported map PAKs, %d extracted files (%.2f GiB).\n\n"
                 "This copies read-only map research files outside the game. "
                 "It DOES NOT create a runnable Stage 2 map, add archives, "
                 "compile terrain or restore Legacy modes. Continue?" %
                 (map_id, len(plan["archives"]), plan["extracted_file_count"],
                  plan["extracted_bytes"] / (1024 ** 3))),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            ) != QMessageBox.Yes:
                return
            parent = QFileDialog.getExistingDirectory(
                self, "Choose an output parent outside both game installations",
                str(Path.home()))
            if not parent:
                return
            destination = Path(parent) / ("Legacy_" + map_id + "_Stage2_Review")
            if destination.exists():
                raise ValueError("Review output already exists; select another parent.")
            if not self.controller.prepare_map_bundle(
                    source_id, target_id, map_id, destination,
                    source_archive=selected["id"]):
                raise RuntimeError("Finish the current Asset Library operation first.")
        except (OSError, ValueError, RuntimeError, KeyError) as exc:
            QMessageBox.warning(self, "Legacy map review", str(exc))

    def review_legacy_model(self):
        """Explicit opt-in to an isolated, verified Stage 2 project replacement."""
        try:
            context = self._historical_target()
            if context is None:
                return
            source_id, target_id, selected = context
            virtual, ok = QInputDialog.getText(
                self, "Legacy model reference",
                "Exact existing-path model (.cgf/.chr/.skin etc.) from the selected PAK:")
            if not ok or not virtual.strip():
                return
            from asset_library_preview import archive_entries
            if virtual.casefold() not in {
                    v.casefold() for v in archive_entries(selected["id"])}:
                raise ValueError("Selected Legacy PAK does not contain that exact model.")
            project = QFileDialog.getExistingDirectory(
                self, "Select the EXISTING editable Stage 2 project", str(Path.home()))
            if not project:
                return
            from legacy_model_transfer import (
                review_model_replacement, apply_reviewed_model)
            preview = review_model_replacement(
                source_id, target_id, virtual, project, self.stage,
                source_archive=selected["id"])
            if QMessageBox.question(
                self, "Approve one compatible model replacement?",
                ("Compatible CryEngine model table and hashes were verified.\n"
                 "Model: %s\nTarget PAK: %s\n"
                 "Only the Stage 2 project file changes, and EditorBackups "
                 "keeps its original. No archive is built, installed or tested "
                 "in the game. Rigs, materials, collisions and runtime behavior "
                 "are NOT verified. Apply this replacement?" %
                 (preview["path"], preview["staged_archive"])),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            ) != QMessageBox.Yes:
                return
            apply_reviewed_model(preview, self.stage)
            QMessageBox.information(
                self, "Project file updated",
                "Compatible model imported into your isolated Stage 2 project. "
                "Inspect it, review changes, then use the ordinary Build Mod "
                "and offline testing workflow. Live game PAKs are unchanged.")
        except (OSError, ValueError, RuntimeError, KeyError) as exc:
            QMessageBox.warning(self, "Legacy model review", str(exc))


    def create_mod_project(self):
        record = self._selected_record()
        if record is None:
            return
        try:
            from asset_library_bulk import stage_source_for_import
            relative, staged = stage_source_for_import(
                record["id"], self.stage, self.game, build_id=self._import_build())
        except (OSError, ValueError, RuntimeError) as exc:
            QMessageBox.warning(
                self, "Archive is reference-only",
                str(exc) + "\n\nOnly PAKs with a unique custom-signed copy "
                "in the current staging plan can be built and signed.")
            return
        if QMessageBox.question(
            self, "Create editable mod project?",
            "Create an independent editable copy of %s from:\n%s\n\n"
            "The Asset Library stays read-only. Edits are built and signed "
            "using the existing manager Build Mod command and the custom "
            "staging key. The project must have changed files before it can "
            "be built.\n\nContinue?" % (relative, staged),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self.accept()
        self.parent().create_mod_project_from_asset_library(
            record["id"], source_build_id=self._import_build())

    def open_selected_map(self):
        record = self._selected_record()
        if record is None:
            return
        from library_map_actions import library_map_identity
        if not library_map_identity(record):
            QMessageBox.information(self, "Open map",
                "Select a PAK whose original source belongs to a map folder. "
                "Unverified historical maps remain reference-only.")
            return
        self.accept()
        self.parent().open_library_map_from_archive(
            record['id'], build_id=self._import_build())

    def verify(self):
        self.controller.verify()

    def remove_selected(self):
        record = self._selected_record()
        if record is None:
            return
        if QMessageBox.question(
            self, "Remove extracted archive?",
            "Remove extracted reference files for " + record["name"] +
            "?\nThis does NOT remove the original PAK or game assets. "
            "Other previews will no longer see these references.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self.controller.remove(record["id"])

    def _running(self, action):
        self.status.setText("Asset Library " + action + " in progress…")
        self.progress.setValue(0)
        self._sync()

    def _progress(self, name, current, total):
        self.status.setText(name + " • %d / %d files" % (current, total))
        self.progress.setValue(int(current * 100 / max(1, total)))

    def _finished(self, action, result, error):
        self.refresh()
        if error:
            QMessageBox.warning(self, "Asset Library", str(error)[:1200])
        elif action == "build-transfer-review" and isinstance(result, dict):
            QMessageBox.information(
                self, "Legacy → Stage 2 review exported",
                ("Compared %d virtual paths and exported %d files.\n\n"
                 "Review package: %s\n\n"
                 "This is NOT an installed Stage 2 mod or playable map. "
                 "The manifest identifies missing entries and compatibility "
                 "questions for future mod projects." %
                 (result["file_count"], result["files_exported"], result["output"])))
        elif action == "map-review" and isinstance(result, dict):
            QMessageBox.information(
                self, "Legacy map review exported",
                "Map research files and transfer-review.json were exported to:\n" +
                result.get("bundle_output", "") +
                "\n\nThis is NOT a compiled or deployable Stage 2 map.")
        elif action == "transfer-candidate":
            QMessageBox.information(
                self, "Review bundle created",
                "Verified reference copies saved to:\n" + str(result)
                + "\n\nNot a signed Stage 2 package or tested game mod.")
        elif isinstance(result, list):
            legacy = [item for item in result if item.get("status") == "legacy-project"]
            failures = [item for item in result if item.get("status") == "error"]
            if legacy:
                names = ", ".join(item["name"] for item in legacy[:5])
                message = (
                    f"{len(legacy)} ordinary ZIP PAK(s) were indexed as independent "
                    "legacy editing projects, not Stage 2 signed references.\n\n"
                    f"{names}\n\nNo game files were modified. "
                    "Open a project from Modding → File → Open Legacy PAK Project."
                )
                choice = QMessageBox.question(
                    self, "Historical PAKs ready to edit",
                    message + "\n\nOpen the first legacy project now?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
                if choice == QMessageBox.Yes:
                    self.accept()
                    self.parent()._show_legacy_pak_editor(legacy[0]["folder"])
            if failures:
                summary = "\n".join(item["name"] + ": " + item["error"]
                                    for item in failures[:8])
                QMessageBox.warning(
                    self, f"{len(failures)} PAK(s) could not be imported",
                    summary[:1500] + ("\n…additional failures omitted"
                                     if len(failures) > 8 else ""))
        elif isinstance(result, dict) and result.get("corrupt", []) + result.get("missing", []):
            QMessageBox.warning(self, "Asset Library integrity",
                "%d damaged and %d missing files. Remove and re-import "
                "the affected archives to repair them." %
                (len(result["corrupt"]), len(result["missing"])))
        self._sync()
