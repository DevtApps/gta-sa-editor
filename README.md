# GTA SA Editor

Editor de texturas e visualizador de modelos do GTA San Andreas para Android,
escrito em Python e PySide6. O projeto abre bancos de texturas e arquivos de
modelos, permite inspecionar e exportar seus conteúdos e faz alterações apenas
em cópias escolhidas pelo usuário.

> Este é um projeto comunitário e não oficial. Grand Theft Auto e GTA San
> Andreas são marcas de seus respectivos proprietários. Nenhum arquivo do jogo
> é distribuído neste repositório; use somente arquivos que você tenha direito
> de acessar e modificar.

## Recursos

- Descoberta de bancos `texdb`, pacotes `ASTCARC v3`, modelos DFF e índices IMG VER2.
- Catálogo de texturas e modelos com busca e classificação por categoria.
- Prévia e exportação de DXT1, DXT5, ETC1, RGBA 4444, PVRTC e ASTC.
- Visualização 3D de modelos DFF portáteis e WDGL mobile.
- Exportação de modelos e texturas e exportação auxiliar para GLB.
- Substituição experimental em uma nova cópia, mantendo a origem intacta.

## Requisitos

- Python 3.12 ou mais recente.
- Linux com suporte a OpenGL.
- Em Debian, Ubuntu e derivados, `libxcb-cursor0` para sessões X11.

## Instalação

```bash
git clone https://github.com/DevtApps/gta-sa-editor.git
cd gta-sa-editor
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Se o Qt não carregar o plugin `xcb` em uma distribuição baseada em Debian:

```bash
sudo apt-get install libxcb-cursor0
```

## Uso

Abra a interface gráfica:

```bash
.venv/bin/python -m gtasa_editor
```

Também é possível abrir diretamente uma pasta ou banco:

```bash
.venv/bin/python -m gtasa_editor /caminho/para/os/arquivos-do-jogo
```

Exemplo de edição de textura:

1. Abra uma pasta do jogo ou um banco `.dat`, `.toc` ou `.txt`.
2. Escolha uma textura e use **Exportar PNG**.
3. Edite o PNG sem alterar suas dimensões.
4. Use **Importar PNG e salvar cópia**.
5. Escolha uma pasta de saída nova, fora da origem.

A gravação é experimental e ainda não tem compatibilidade garantida com o
cliente Android. Mantenha backups e trabalhe somente em cópias.

### Exportar um modelo para GLB

```bash
.venv/bin/python tools/export_glb.py \
  /caminho/para/modelos.img nome-do-modelo.dff modelo.glb
```

Use `.venv/bin/python tools/export_glb.py --help` para consultar os argumentos.

## Estrutura do projeto

```text
gtasa_editor/
  __main__.py       Entrada da aplicação
  studio.py         Interface principal e tarefas em segundo plano
  texdb.py          Leitura e decodificação de bancos de texturas
  writer.py         Escrita protegida em cópias
  models.py         Catálogo e leitura de DFF/IMG
  native_dff.py     Geometria WDGL mobile
  viewer3d.py       Visualizador OpenGL
  model_io.py       Importação e exportação de modelos
  astc.py           Leitura de índices ASTCARC
  catalog.py        Catálogo e classificação
  render_rules.py   Regras de materiais e veículos
tests/               Testes automatizados
tools/               Diagnóstico, smoke test e exportação GLB
docs/validation/     Evidências técnicas de validação
```

## Testes

```bash
.venv/bin/python -m unittest discover -s tests -v
```

O smoke test visual requer uma instalação local válida do jogo:

```bash
.venv/bin/python tools/smoke_studio.py /caminho/para/os/arquivos-do-jogo
```

O relatório e os resultados históricos estão em
[`docs/validation`](docs/validation/). Eles documentam limitações conhecidas e
não incluem os arquivos usados durante a validação.

## Referências técnicas

- [DragonFF](https://github.com/Parik27/DragonFF), para estruturas WDGL e convenções de materiais.
- [gta-reversed](https://github.com/gta-reversed/gta-reversed), para montagem de veículos.
- [librw](https://github.com/aap/librw), como referência de RenderWare.

Essas referências não implicam afiliação. Consulte as licenças dos respectivos
projetos antes de reutilizar código.

## Contribuindo

1. Não inclua arquivos do jogo, credenciais, caminhos pessoais ou dados locais.
2. Adicione ou atualize testes para mudanças de comportamento.
3. Execute a suíte completa e descreva formatos e fixtures testados.
4. Preserve a regra de nunca sobrescrever arquivos de origem.

## Licença

Distribuído sob a licença MIT. Consulte [`LICENSE`](LICENSE).
