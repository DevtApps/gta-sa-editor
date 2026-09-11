import sys
from pathlib import Path
from PIL import Image

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap, QPainter, QColor
from PySide6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QPushButton, QScrollArea, QSplitter, QVBoxLayout, QWidget, QDialog)

from .texdb import Bank, discover, export_png
from .writer import save_copy, replacement_block


class Preview(QLabel):
    def paintEvent(self, event):
        painter = QPainter(self)
        for y in range(0, self.height(), 20):
            for x in range(0, self.width(), 20):
                color = '#dddddd' if (x // 20 + y // 20) % 2 else '#f2f2f2'
                painter.fillRect(x, y, 20, 20, QColor(color))
        painter.end()
        super().paintEvent(event)


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('GTA SA • Navegador de texturas')
        self.resize(1200, 780)
        self.bank = None
        self.current_image = None
        self.current_entry = None
        self.source_root = None
        container = QWidget()
        layout = QVBoxLayout(container)
        actions = QHBoxLayout()
        for title, callback in [('Abrir arquivo…', self.open_file), ('Explorar pasta…', self.open_folder)]:
            button = QPushButton(title)
            button.clicked.connect(callback)
            actions.addWidget(button)
        actions.addStretch()
        actions.addWidget(QLabel('Edição em cópia • DXT1 / DXT5 / RGBA 4444'))
        layout.addLayout(actions)
        self.origin = QLabel('Escolha um arquivo DAT, TOC ou TXT, ou uma pasta de bancos.')
        self.origin.setWordWrap(True)
        self.origin.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.origin)
        splitter = QSplitter()
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel('Bancos • origem e variante'))
        self.banks = QListWidget()
        self.banks.currentItemChanged.connect(self.select_bank)
        left_layout.addWidget(self.banks)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Buscar textura pelo nome…')
        self.search.textChanged.connect(self.filter_textures)
        left_layout.addWidget(self.search)
        self.textures = QListWidget()
        self.textures.currentItemChanged.connect(self.select_texture)
        left_layout.addWidget(self.textures, 3)
        splitter.addWidget(left)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        self.details = QLabel('Selecione uma textura para visualizar.')
        self.details.setWordWrap(True)
        self.details.setTextFormat(Qt.PlainText)
        self.details.setTextInteractionFlags(Qt.TextSelectableByMouse)
        right_layout.addWidget(self.details)
        self.preview = Preview()
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setStyleSheet('color: #222; padding: 12px;')
        scroll = QScrollArea()
        scroll.setWidget(self.preview)
        scroll.setWidgetResizable(True)
        right_layout.addWidget(scroll, 1)
        self.export = QPushButton('Exportar PNG…')
        self.export.setEnabled(False)
        self.export.clicked.connect(self.save_png)
        right_layout.addWidget(self.export)
        self.import_button = QPushButton('Importar PNG e salvar cópia…')
        self.import_button.setEnabled(False)
        self.import_button.clicked.connect(self.import_png)
        right_layout.addWidget(self.import_button)
        splitter.addWidget(right)
        splitter.setSizes([420, 780])
        layout.addWidget(splitter, 1)
        self.setCentralWidget(container)

    def open_file(self):
        filename, _ = QFileDialog.getOpenFileName(self, 'Abrir banco de texturas', '',
            'Bancos de texturas (*.dat *.toc *.txt)')
        if not filename:
            return
        path = Path(filename).resolve()
        paths = sorted(path.parent.glob(path.stem + '.*.toc')) if path.suffix == '.txt' else [path.with_suffix('.toc')]
        self.load_paths(paths, path.parent)

    def open_folder(self):
        folder = QFileDialog.getExistingDirectory(self, 'Escolher pasta do jogo, texdb ou banco')
        if folder:
            root = Path(folder).resolve()
            self.load_paths(discover(root), root)

    def load_paths(self, paths, root):
        self.source_root = root
        self.banks.clear()
        self.textures.clear()
        self.clear_preview()
        for path in paths:
            item = QListWidgetItem(str(path.relative_to(root)))
            item.setData(Qt.UserRole, path)
            item.setToolTip(str(path))
            self.banks.addItem(item)
        self.origin.setText(str(root))
        self.statusBar().showMessage(f'{len(paths)} banco(s) encontrado(s).')
        if paths:
            self.banks.setCurrentRow(0)
        else:
            self.details.setText('Nenhum banco com TOC encontrado nesta seleção.')

    def clear_preview(self):
        self.current_image = None
        self.current_entry = None
        self.preview.clear()
        self.export.setEnabled(False)
        self.import_button.setEnabled(False)

    def select_bank(self, item, previous=None):
        self.bank = None
        self.textures.clear()
        self.clear_preview()
        if item is None:
            return
        path = item.data(Qt.UserRole)
        self.origin.setText(str(path))
        try:
            self.bank = Bank(path)
        except (OSError, ValueError) as exc:
            self.details.setText(f'Não foi possível abrir o banco: {exc}')
            return
        self.textures.setUpdatesEnabled(False)
        for entry in self.bank.textures:
            row = QListWidgetItem(f'{entry.name}  •  {entry.format if entry.offset >= 0 else "Alias"}')
            row.setData(Qt.UserRole, entry)
            row.setToolTip(entry.error or f'{entry.width} × {entry.height} • {entry.format}')
            self.textures.addItem(row)
        self.textures.setUpdatesEnabled(True)
        self.filter_textures(self.search.text())
        self.details.setText('Selecione uma textura. A codificação é lida do cabeçalho de cada bloco.')
        errors = sum(bool(entry.error) for entry in self.bank.textures)
        self.statusBar().showMessage(f'{len(self.bank.textures)} entradas • {errors} aliases ou entradas indisponíveis')
        for index in range(self.textures.count()):
            row = self.textures.item(index)
            if not row.isHidden() and row.data(Qt.UserRole).supported:
                self.textures.setCurrentItem(row)
                break
        else:
            self.preview.setText('Nenhuma prévia disponível nesta seleção.\n'
                'Selecione uma entrada para consultar o formato ou tente a variante .dxt do banco.')

    def filter_textures(self, query):
        for index in range(self.textures.count()):
            item = self.textures.item(index)
            item.setHidden(query.casefold() not in item.data(Qt.UserRole).name.casefold())

    def select_texture(self, item, previous=None):
        self.clear_preview()
        if item is None or self.bank is None:
            return
        entry = item.data(Qt.UserRole)
        self.details.setText(f'{entry.name}\n{entry.width} × {entry.height} • {entry.format} • '
            f'{"Sem mipmaps" if entry.no_mip else "Mipmaps sinalizados"}\n'
            + '  '.join(f'{key}={value}' for key, value in entry.props.items()))
        try:
            image = self.bank.image(entry)
            qimage = QImage(image.tobytes(), image.width, image.height, QImage.Format_RGBA8888).copy()
            self.preview.setPixmap(QPixmap.fromImage(qimage))
            self.current_image = image
            self.current_entry = entry
            self.export.setEnabled(True)
            self.import_button.setEnabled(entry.editable)
            self.import_button.setToolTip('' if entry.editable else f'{entry.format}: prévia e exportação disponíveis; importação ainda não implementada.')
            if not entry.editable:
                self.details.setText(self.details.text() + '\nPrévia e exportação disponíveis. Importação neste formato ainda não implementada.')
        except (OSError, ValueError, RuntimeError) as exc:
            self.preview.setText(str(exc))

    def import_png(self):
        if self.current_entry is None:
            return
        filename, _ = QFileDialog.getOpenFileName(self, 'Escolher PNG substituto', '', 'PNG (*.png)')
        if not filename:
            return
        entry, bank = self.current_entry, self.bank
        try:
            with Image.open(filename) as source:
                if source.format != 'PNG' or source.size != (entry.width, entry.height):
                    raise ValueError(f'Escolha um PNG de {entry.width} × {entry.height} pixels.')
                imported = source.convert('RGBA')
            block = replacement_block(bank, entry, imported)
            from .texdb import dxt_image
            encoded = dxt_image(entry.encoding, entry.width, entry.height, block[16:])
        except (OSError, ValueError, RuntimeError) as exc:
            QMessageBox.warning(self, 'Importação não realizada', str(exc))
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(f'Substituir {entry.name}')
        dialog.resize(650, 550)
        layout = QVBoxLayout(dialog)
        description = QLabel(f'{entry.name} • {entry.width} × {entry.height} • {entry.format}\n'
            'Prévia após conversão. Os mipmaps serão regenerados.\n'
            'Será criada uma pasta nova com o banco. Compatibilidade no Android ainda não validada.')
        description.setTextFormat(Qt.PlainText)
        description.setWordWrap(True)
        layout.addWidget(description)
        preview = Preview()
        preview.setAlignment(Qt.AlignCenter)
        qimage = QImage(encoded.tobytes(), encoded.width, encoded.height, QImage.Format_RGBA8888).copy()
        preview.setPixmap(QPixmap.fromImage(qimage))
        scroll = QScrollArea()
        scroll.setWidget(preview)
        scroll.setWidgetResizable(True)
        layout.addWidget(scroll)
        button = QPushButton('Salvar banco em nova pasta…')
        layout.addWidget(button)

        def save():
            filename, _ = QFileDialog.getSaveFileName(dialog, 'Nome da NOVA pasta do banco',
                str(Path.home() / (bank.dat.parent.name + '-editado')))
            if not filename:
                return
            try:
                result = save_copy(bank, entry, imported, Path(filename), self.source_root)
            except (OSError, ValueError, RuntimeError) as exc:
                QMessageBox.warning(dialog, 'Cópia não salva', str(exc))
                return
            dialog.accept()
            self.statusBar().showMessage(f'Banco salvo: {result}')
            QMessageBox.information(self, 'Cópia salva', f'Banco editado salvo em:\n{result.parent}\n\n'
                'Abra essa cópia para continuar editando. O banco de origem foi preservado.')

        button.clicked.connect(save)
        dialog.exec()

    def save_png(self):
        if self.current_image is None:
            return
        safe_name = ''.join(c if c.isalnum() or c in '-_.' else '_' for c in self.current_entry.name)
        filename, _ = QFileDialog.getSaveFileName(self, 'Exportar textura PNG', str(Path.home() / (safe_name + '.png')), 'PNG (*.png)')
        if not filename:
            return
        path = Path(filename)
        if path.suffix.lower() != '.png':
            path = path.with_suffix('.png')
        try:
            export_png(self.current_image, path, self.source_root)
            self.statusBar().showMessage(f'PNG exportado: {path}')
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, 'Exportação não realizada', str(exc))


def main():
    app = QApplication(sys.argv)
    from .studio import Studio
    window = Studio()
    if len(sys.argv) > 1:
        path = Path(sys.argv[1]).resolve()
        if path.is_dir():
            window.load_paths(discover(path), path)
        else:
            paths = sorted(path.parent.glob(path.stem + '.*.toc')) if path.suffix == '.txt' else [path.with_suffix('.toc')]
            window.load_paths(paths, path.parent)
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
