"""Open the studio briefly and verify a real catalog/model. No game writes.

Run from repository root: .venv/bin/python tools/smoke_studio.py /path/to/data
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication
from gtasa_editor.studio import Studio
from gtasa_editor.texdb import discover

app = QApplication([])
window = Studio()
root = Path(sys.argv[1]).resolve()
window.load_paths(discover(root), root)
window.show()
stage = 0


def poll():
    global stage
    if window.tasks:
        QTimer.singleShot(100, poll)
        return
    if stage == 0:
        assert window.catalog and window.models
        print(f'{len(window.catalog)} texturas, {len(window.models)} modelos, {len(window.issues)} avisos')
        for index in range(window.model_list.count()):
            model = window.model_list.item(index).data(Qt.UserRole)
            if model.name == 'huntley.dff' and model.path.stem == 'lr_cars':
                window.library_tabs.setCurrentIndex(1)
                window.model_list.setCurrentRow(index)
                break
        else:
            raise AssertionError('Fixture huntley.dff não encontrada em lr_cars.img')
        stage = 1
        QTimer.singleShot(100, poll)
    else:
        assert 'triângulos' in window.materials.toPlainText(), window.materials.toPlainText()
        if window.viewer:
            assert window.viewer.isValid(), 'OpenGL indisponível'
            assert window.viewer.meshes
            print(f'OpenGL OK: {len(window.viewer.images)} texturas vinculadas')
            window.viewer.grabFramebuffer().save('/tmp/gtasa-studio-model.png')
        window.grab().save('/tmp/gtasa-studio-window.png')
        print(window.materials.toPlainText())
        window.close()
        app.quit()


def guarded_poll():
    try:
        original_poll()
    except Exception:
        import traceback
        traceback.print_exc()
        app.exit(1)


original_poll = poll
poll = guarded_poll
QTimer.singleShot(100, poll)
sys.exit(app.exec())
