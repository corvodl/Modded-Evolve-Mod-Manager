"""Qt view for existing safe QProcess task engine, with serialized queued jobs.

No direct game file mutation or QProcess termination during swap/restore.
"""
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
 QDialog,QVBoxLayout,QHBoxLayout,QLabel,QListWidget,QPushButton,QMessageBox)


class TaskQueueDialog(QDialog):
    def __init__(self,manager):
        super().__init__(manager)
        self.manager=manager
        self.setWindowTitle('Background Task Manager')
        self.resize(770,500)
        layout=QVBoxLayout(self)
        layout.addWidget(QLabel(
            'Queue safe extraction/review/build actions. Long-running tasks '
            'continue in the manager worker process with Activity logs. '
            'Running signing, swap or restore operations cannot be interrupted.'))
        self.status=QLabel()
        layout.addWidget(self.status)
        self.entries=QListWidget()
        layout.addWidget(self.entries,1)
        actions=QHBoxLayout()
        for name in ('Review Changes','Unpack PAK','Build Mod'):
            item=QPushButton('Queue '+name)
            item.clicked.connect(lambda checked=False,n=name: self.enqueue(n))
            actions.addWidget(item)
        layout.addLayout(actions)
        actions=QHBoxLayout()
        clear=QPushButton('Clear Pending Tasks')
        clear.clicked.connect(self.clear)
        actions.addWidget(clear)
        logs=QPushButton('Activity / Logs')
        logs.clicked.connect(lambda:self.manager.toggle_activity())
        actions.addWidget(logs)
        close=QPushButton('Close')
        close.clicked.connect(self.accept)
        actions.addWidget(close)
        layout.addLayout(actions)
        self.poll=QTimer(self)
        self.poll.timeout.connect(self.refresh)
        self.poll.start(500)
        self.refresh()

    def enqueue(self,name):
        try:
            self.manager.enqueue_task_action(name)
            self.refresh()
        except (ValueError,OSError,RuntimeError) as exc:
            QMessageBox.warning(self,'Cannot queue task',str(exc))

    def clear(self):
        self.manager.task_queue.clear()
        self.refresh()

    def refresh(self):
        self.status.setText('Running: %s   |   Pending: %d'%
            (getattr(self.manager,'_task_label','Idle') if self.manager.busy else 'Idle',
             len(self.manager.task_queue)))
        self.entries.clear()
        for entry in self.manager.task_queue:
            self.entries.addItem('%s — %s'%(entry['name'],entry['archive']))
