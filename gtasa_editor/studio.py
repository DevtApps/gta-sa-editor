from collections import Counter, defaultdict
from pathlib import Path

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import (QApplication, QComboBox, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QPushButton, QSplitter, QTabWidget,
    QTextEdit, QVBoxLayout, QWidget, QScrollArea, QFileDialog)

from .__main__ import Window, Preview
from .catalog import CATEGORIES, scan
from .models import discover_models, load_model
from .model_io import export_model, import_model_copy, import_model_package
from .render_rules import source_score, material_role
from .texdb import discover


class Task(QThread):
    result = Signal(object)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self, work):
        super().__init__()
        self.work = work

    def run(self):
        try:
            self.result.emit(self.work(self))
        except Exception as exc:
            self.failed.emit(f'{type(exc).__name__}: {exc}')


class Studio(Window):
    def __init__(self):
        super().__init__()
        self.banks.currentItemChanged.disconnect()
        self.preview = Preview()
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setStyleSheet('color: #222; padding: 12px;')
        self.setWindowTitle('GTA SA Studio')
        self.resize(1450, 900)
        self.catalog, self.models, self.issues = [], [], []
        self.tasks = set()
        self.generation = 0
        self.model_generation = 0
        self.selected_catalog = None
        self.texture_index = defaultdict(list)
        self.current_model = None
        self.model_images = {}
        self.model_bindings = {}
        self.model_report = []
        self.textures.currentItemChanged.disconnect()
        self.textures.currentItemChanged.connect(self.select_catalog)
        self.search.textChanged.disconnect()
        self.search.textChanged.connect(self.filter_library)
        shell = QWidget()
        layout = QVBoxLayout(shell)
        toolbar = QHBoxLayout()
        for title, callback in [('Abrir pasta do GTA…', self.open_folder),
                                ('Adicionar fonte de texturas…', self.add_texture_source),
                                ('Abrir banco…', self.open_file),
                                ('Abrir DFF / IMG…', self.open_model_file)]:
            button = QPushButton(title)
            button.clicked.connect(callback)
            toolbar.addWidget(button)
        toolbar.addStretch()
        self.scan_status = QLabel('Abra a pasta de dados para montar a biblioteca.')
        toolbar.addWidget(self.scan_status)
        layout.addLayout(toolbar)
        layout.addWidget(self.origin)
        split = QSplitter()
        self.categories = QListWidget()
        for category in CATEGORIES:
            item = QListWidgetItem(category)
            item.setData(Qt.UserRole, category)
            self.categories.addItem(item)
        self.categories.setCurrentRow(0)
        self.categories.currentItemChanged.connect(self.filter_library)
        split.addWidget(self.categories)
        library = QWidget()
        library_layout = QVBoxLayout(library)
        self.search.setPlaceholderText('Buscar texturas ou modelos…')
        library_layout.addWidget(self.search)
        self.library_tabs = QTabWidget()
        self.library_tabs.addTab(self.textures, 'Texturas')
        self.model_list = QListWidget()
        self.model_list.currentItemChanged.connect(self.select_model)
        self.library_tabs.addTab(self.model_list, 'Modelos 3D')
        library_layout.addWidget(self.library_tabs)
        split.addWidget(library)
        self.workspace = QTabWidget()
        texture_panel = QWidget()
        texture_layout = QVBoxLayout(texture_panel)
        self.variants = QComboBox()
        self.variants.currentIndexChanged.connect(self.select_variant)
        texture_layout.addWidget(self.variants)
        texture_layout.addWidget(self.details)
        scroll = QScrollArea()
        scroll.setWidget(self.preview)
        scroll.setWidgetResizable(True)
        texture_layout.addWidget(scroll, 1)
        texture_layout.addWidget(self.export)
        texture_layout.addWidget(self.import_button)
        self.workspace.addTab(texture_panel, 'Textura')
        model_panel = QWidget()
        model_layout = QVBoxLayout(model_panel)
        model_layout.addWidget(QLabel('Arraste para girar • roda do mouse para zoom • pose estática'))
        controls = QHBoxLayout()
        reset = QPushButton('Enquadrar')
        wire = QPushButton('Wireframe')
        wire.setCheckable(True)
        controls.addWidget(reset)
        controls.addWidget(wire)
        model_layout.addLayout(controls)
        self.model_source = QComboBox()
        self.model_source.addItem('Texturas automáticas — fontes listadas abaixo', None)
        self.model_source.currentIndexChanged.connect(lambda: self.select_model(self.model_list.currentItem()))
        model_layout.addWidget(self.model_source)
        self.viewer = None
        if QApplication.platformName() not in ('offscreen', 'minimal'):
            from .viewer3d import Viewer3D
            self.viewer = Viewer3D()
            model_layout.addWidget(self.viewer, 1)
            reset.clicked.connect(self.viewer.reset_camera)
            wire.toggled.connect(self.set_wireframe)
        else:
            model_layout.addWidget(QLabel('Visualização OpenGL indisponível no modo de teste sem janela.'), 1)
        self.materials = QTextEdit()
        self.materials.setReadOnly(True)
        self.materials.setMaximumHeight(180)
        model_layout.addWidget(self.materials)
        self.export_model_button = QPushButton('Exportar modelo completo + texturas…')
        self.export_model_button.clicked.connect(self.export_current_model)
        self.export_model_button.setEnabled(False)
        model_layout.addWidget(self.export_model_button)
        self.import_model_button = QPushButton('Importar DFF e salvar cópia do IMG…')
        self.import_model_button.clicked.connect(self.import_current_model)
        self.import_model_button.setEnabled(False)
        model_layout.addWidget(self.import_model_button)
        self.import_package_button = QPushButton('Importar modelo completo + texturas…')
        self.import_package_button.clicked.connect(self.import_current_package)
        self.import_package_button.setEnabled(False)
        model_layout.addWidget(self.import_package_button)
        self.workspace.addTab(model_panel, 'Modelo 3D')
        self.diagnostics = QTextEdit()
        self.diagnostics.setReadOnly(True)
        self.workspace.addTab(self.diagnostics, 'Leitura / avisos')
        split.addWidget(self.workspace)
        split.setSizes([170, 430, 850])
        layout.addWidget(split, 1)
        self.setCentralWidget(shell)

    def run_task(self, work, completed):
        task = Task(work)
        self.tasks.add(task)
        task.result.connect(completed)
        def failed(message):
            self.scan_status.setText('Falha na leitura; consulte Leitura / avisos.')
            self.diagnostics.append(message)
            self.materials.setPlainText('Não foi possível carregar. ' + message)
        task.failed.connect(failed)
        task.progress.connect(self.scan_status.setText)
        task.finished.connect(lambda: self.tasks.discard(task))
        task.start()

    def open_folder(self):
        folder = QFileDialog.getExistingDirectory(self, 'Escolher pasta de dados do GTA')
        if folder:
            root = Path(folder).resolve()
            self.load_paths(discover(root), root)

    def load_paths(self, paths, root):
        self.generation += 1
        generation = self.generation
        self.model_generation += 1
        self.source_root = root
        self.origin.setText(str(root))
        self.catalog, self.models = [], []
        self.texture_index.clear()
        self.textures.clear()
        self.model_list.clear()
        self.clear_preview()
        self.scan_status.setText('Lendo bancos e arquivos de modelos…')
        if self.viewer:
            self.viewer.set_scene([], {})

        def work(task):
            all_paths = list(paths) + sorted(root.rglob('*.astc_arc'))
            catalog, errors = scan(all_paths, lambda done, total: task.progress.emit(f'Lendo banco {done}/{total}…'),
                                   task.isInterruptionRequested)
            models, model_errors = discover_models(root, task.isInterruptionRequested)
            return catalog, models, errors + model_errors

        def complete(result):
            if generation != self.generation:
                return
            self.catalog, self.models, self.issues = result
            self.texture_index = defaultdict(list)
            for item in self.catalog:
                self.texture_index[item.name.casefold()].append(item)
            self.model_source.blockSignals(True)
            self.model_source.clear()
            self.model_source.addItem('Texturas automáticas — fontes listadas abaixo', None)
            for source in sorted({item.source for item in self.catalog}):
                self.model_source.addItem(self.source_label(source), source)
            self.model_source.blockSignals(False)
            self.populate_library()
            self.diagnostics.setPlainText('Categorias automáticas por banco, arquivo e nome; podem precisar de revisão.\n'
                'Variantes de uma textura são agrupadas por origem; as duas árvores texdb permanecem separadas.\n'
                'As fontes escolhidas para a prévia 3D não determinam a prioridade de carregamento do jogo.\n\n'
                + ('\n'.join(self.issues) or 'Nenhum erro de leitura.'))
            self.scan_status.setText(f'{len(self.catalog)} texturas • {len(self.models)} modelos • {len(self.issues)} avisos')
        for task in self.tasks:
            task.requestInterruption()
        self.run_task(work, complete)

    def populate_library(self):
        self.textures.setUpdatesEnabled(False)
        self.textures.clear()
        for entry in self.catalog:
            row = QListWidgetItem(f'{entry.name}  •  {self.source_label(entry.source)}')
            row.setData(Qt.UserRole, entry)
            row.setToolTip(f'{entry.category} • classificação automática\n{entry.source}\n{len(entry.variants)} variante(s)')
            self.textures.addItem(row)
        self.textures.setUpdatesEnabled(True)
        self.model_list.clear()
        for model in self.models:
            row = QListWidgetItem(f'{model.name}  •  {model.path.name}')
            row.setData(Qt.UserRole, model)
            row.setToolTip(str(model.path))
            self.model_list.addItem(row)
        textures = Counter(item.category for item in self.catalog)
        models = Counter(item.category for item in self.models)
        for index, category in enumerate(CATEGORIES):
            count = len(self.catalog) + len(self.models) if category == 'All' else textures[category] + models[category]
            self.categories.item(index).setText(f'{category} ({count})')
        self.filter_library()

    def filter_library(self, *args):
        current = self.categories.currentItem()
        category = current.data(Qt.UserRole) if current else 'All'
        query = self.search.text().casefold()
        for widget in (self.textures, self.model_list):
            for index in range(widget.count()):
                row = widget.item(index)
                entry = row.data(Qt.UserRole)
                row.setHidden((category != 'All' and entry.category != category) or query not in row.text().casefold())
            selected = widget.currentItem()
            if selected and selected.isHidden():
                widget.setCurrentRow(-1)

    def select_catalog(self, row, previous=None):
        self.clear_preview()
        self.selected_catalog = row.data(Qt.UserRole) if row else None
        self.variants.blockSignals(True)
        self.variants.clear()
        if row:
            for bank, entry in self.selected_catalog.variants:
                self.variants.addItem(f'{bank.toc.name} • {entry.format}', (bank, entry))
        self.variants.blockSignals(False)
        self.select_variant(0)

    def select_variant(self, index):
        data = self.variants.itemData(index)
        if data is None:
            return
        self.bank, entry = data
        row = QListWidgetItem()
        row.setData(Qt.UserRole, entry)
        Window.select_texture(self, row)
        self.origin.setText(str(self.bank.toc))
        self.workspace.setCurrentIndex(0)

    def open_model_file(self):
        filename, _ = QFileDialog.getOpenFileName(self, 'Abrir modelo ou arquivo de modelos', '', 'Modelos (*.dff *.img)')
        if not filename:
            return
        from .models import Model, archive_models, model_category
        path = Path(filename).resolve()
        try:
            models = archive_models(path) if path.suffix.lower() == '.img' else [Model(path.name, path, 0, path.stat().st_size, model_category(path, path.name))]
            self.models.extend(models)
            if self.source_root is None:
                self.source_root = path.parent
            self.populate_library()
            self.library_tabs.setCurrentIndex(1)
        except (OSError, ValueError) as exc:
            self.scan_status.setText(str(exc))

    def select_model(self, row, previous=None):
        self.model_generation += 1
        generation = self.model_generation
        if self.viewer:
            self.viewer.set_scene([], {})
        self.materials.clear()
        self.current_model = None
        self.model_images, self.model_report = {}, []
        self.model_bindings = {}
        self.export_model_button.setEnabled(False)
        self.import_model_button.setEnabled(False)
        self.import_package_button.setEnabled(False)
        if row is None:
            return
        model = row.data(Qt.UserRole)
        self.current_model = model
        self.export_model_button.setEnabled(True)
        self.import_model_button.setEnabled(True)
        self.import_package_button.setEnabled(True)
        source_filter = self.model_source.currentData()
        texture_index = self.texture_index
        self.workspace.setCurrentIndex(1)
        self.materials.setPlainText(f'Carregando {model.name}…')

        def work(task):
            meshes = load_model(model)
            images, report, bindings = {}, [], {}
            total_bytes = 0
            lookups = {}
            for name in sorted({m.texture_name.casefold() for m in meshes if m.texture_name}):
                candidates = sorted(texture_index.get(name, []),
                                    key=lambda item: source_score(model, item, source_filter))
                found = False
                for item in candidates:
                    for bank, entry in item.variants:
                        if bank.toc not in lookups:
                            lookups[bank.toc] = {e.name.casefold(): e for e in bank.textures}
                        seen = set()
                        while entry.offset < 0 and entry.name not in seen:
                            seen.add(entry.name)
                            target = lookups[bank.toc].get(entry.props.get('affiliate', '').casefold())
                            if target is None:
                                break
                            entry = target
                        if not entry.supported:
                            continue
                        try:
                            image = bank.image(entry)
                            image.thumbnail((1024, 1024))
                            total_bytes += image.width * image.height * 4
                            if total_bytes > 128 * 1024 * 1024:
                                raise ValueError('Texturas do modelo excedem 128 MiB.')
                            images[name] = image
                            bindings[name] = (bank, entry)
                            report.append(f'{name} → {bank.toc}' + (' [resolvido por prioridade GTA]' if len(candidates) > 1 else ''))
                            found = True
                            break
                        except (OSError, ValueError):
                            continue
                    if found:
                        break
                if not found:
                    report.append(f'{name} → ausente ou sem decodificador (exibida em cinza)')
                if task.isInterruptionRequested():
                    return None
            return meshes, images, report, bindings

        def complete(result):
            if result is None or generation != self.model_generation:
                return
            meshes, images, report, bindings = result
            self.model_images, self.model_report = images, report
            self.model_bindings = bindings
            if self.viewer:
                self.viewer.set_scene(meshes, images)
            total = len({mesh.texture_name.casefold() for mesh in meshes if mesh.texture_name})
            coverage = f'{len(images)}/{total} texturas resolvidas'
            roles = sorted({material_role(mesh.diffuse_color) for mesh in meshes if not mesh.texture_name})
            self.materials.setPlainText(f'{model.name} • {sum(m.triangle_count for m in meshes)} triângulos • {coverage}\n'
                f'{model.path}\n' + '\n'.join(report))
            if roles:
                self.materials.append('Materiais sem textura: ' + ', '.join(roles))
            self.scan_status.setText(f'Modelo carregado: {model.name}')
        self.run_task(work, complete)

    def source_label(self, source):
        return str(source.relative_to(self.source_root)) if source.is_relative_to(self.source_root) else str(source)

    def add_texture_source(self):
        folder = QFileDialog.getExistingDirectory(self, 'Adicionar pasta de texturas (inclui ASTC e texdb)')
        if not folder:
            return
        root = Path(folder).resolve()
        if self.source_root is None:
            self.source_root = root
        generation = self.generation
        def work(task):
            return scan(discover(root) + sorted(root.rglob('*.astc_arc')), cancelled=task.isInterruptionRequested)
        def complete(result):
            if generation != self.generation:
                return
            catalog, errors = result
            known = {(item.source, item.name) for item in self.catalog}
            self.catalog.extend(item for item in catalog if (item.source, item.name) not in known)
            self.catalog.sort(key=lambda item: (item.name.casefold(), str(item.source)))
            self.texture_index = defaultdict(list)
            for item in self.catalog:
                self.texture_index[item.name.casefold()].append(item)
            self.issues.extend(errors)
            self.model_source.blockSignals(True)
            self.model_source.clear()
            self.model_source.addItem('Texturas automáticas — fontes listadas abaixo', None)
            for source in sorted({item.source for item in self.catalog}):
                self.model_source.addItem(self.source_label(source), source)
            self.model_source.blockSignals(False)
            self.populate_library()
            self.diagnostics.setPlainText('\n'.join(self.issues) or 'Fontes carregadas sem erros.')
            self.scan_status.setText(f'Fonte adicionada: {root}')
        self.run_task(work, complete)

    def export_current_model(self):
        if self.current_model is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, 'Nome da nova pasta de exportação',
                                             str(Path.home() / (Path(self.current_model.name).stem + '-modelo')))
        if not path:
            return
        model, images, report, bindings = self.current_model, self.model_images, self.model_report, self.model_bindings
        source_root = self.source_root
        def work(task):
            export_model(model, images, report, path, source_root, bindings)
            return path
        self.run_task(work, lambda destination: self.scan_status.setText(f'Modelo e texturas disponíveis exportados: {destination}'))

    def import_current_model(self):
        if self.current_model is None:
            return
        model = self.current_model
        replacement, _ = QFileDialog.getOpenFileName(self, 'Escolher modelo DFF substituto', '', 'DFF (*.dff)')
        if not replacement:
            return
        suffix = '.img' if model.path.suffix.lower() == '.img' else '.dff'
        destination, _ = QFileDialog.getSaveFileName(self, 'Salvar cópia com o modelo substituído',
            str(Path.home() / (model.path.stem + '-editado' + suffix)), f'Cópia (*{suffix})')
        if not destination:
            return
        if not Path(destination).suffix:
            destination += suffix
        def work(task):
            return import_model_copy(model, replacement, destination, source_root)
        def complete(result):
            self.scan_status.setText(f'Modelo importado: {result.path}. Origem preservada; teste a cópia no jogo.')
        source_root = self.source_root
        self.run_task(work, complete)

    def import_current_package(self):
        if self.current_model is None:
            return
        package = QFileDialog.getExistingDirectory(self, 'Escolher pacote com model.dff e manifest.json')
        if not package:
            return
        destination = QFileDialog.getExistingDirectory(self, 'Escolher pasta onde criar a importacao completa')
        if not destination:
            return
        destination = str(Path(destination) / (Path(self.current_model.name).stem + '-pacote-importado'))
        try:
            import json
            manifest = json.loads((Path(package) / 'manifest.json').read_text(encoding='utf-8'))
            names = manifest.get('textures', {})
            bindings = {}
            source_filter = self.model_source.currentData()
            for name in names:
                candidates = sorted(self.texture_index.get(name.casefold(), []),
                                    key=lambda item: source_score(self.current_model, item, source_filter))
                for item in candidates:
                    match = next(((bank, entry) for bank, entry in item.variants
                                  if entry.offset >= 0 and entry.editable), None)
                    if match:
                        bindings[name.casefold()] = match
                        break
        except (OSError, ValueError, TypeError) as exc:
            self.materials.setPlainText(f'Pacote invalido: {exc}')
            return
        model, source_root = self.current_model, self.source_root
        def work(task):
            return import_model_package(model, package, destination, source_root, bindings)
        def complete(result):
            imported, banks = result
            self.scan_status.setText(
                f'Modelo completo importado: {imported.path} • {len(banks)} banco(s) de texturas em copia.')
        self.run_task(work, complete)

    def set_wireframe(self, enabled):
        if self.viewer:
            self.viewer.wireframe = enabled
            self.viewer.update()

    def closeEvent(self, event):
        for task in list(self.tasks):
            task.requestInterruption()
        if any(task.isRunning() for task in self.tasks):
            self.scan_status.setText('Encerrando leitura; feche novamente em instantes.')
            event.ignore()
            return
        super().closeEvent(event)
