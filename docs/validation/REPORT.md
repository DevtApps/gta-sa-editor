# Validação técnica — 2026-09-09

## Conclusão

É viável construir um navegador/exportador DXT para Linux. O mobile-textdb 0.4.0 não está aprovado como backend de escrita: foram reproduzidas inversão dos canais vermelho/azul, alteração indevida de bloco vizinho e perda de metadados.

Não foi feito teste no Android. Reabrir o banco na mesma biblioteca não comprova compatibilidade com o jogo.

## Proteção dos dados

A fixture original foi copiada integralmente para uma área isolada de validação, incluindo sua pasta `data/` interna. São 617 arquivos e 5.554.205.868 bytes. As listas de arquivos eram idênticas e não houve diferenças SHA-256. Ver `backup.json`. Todos os testes de escrita usaram outra cópia temporária.

## Ambiente e abrangência

Linux, Python 3.12; dependências fixadas em `tools/requirements-validation.txt`. Código da distribuição PyPI de mobile-textdb 0.4.0 inspecionado antes de executar os testes.

Foram examinadas 29 combinações banco/formato na árvore interna `data/texdb`. A leitura de todos os cabeçalhos terminou em 27 combinações, sem divergências entre os hashes de nomes calculados e os cabeçalhos lidos. Foram decodificadas amostras limitadas a cinco texturas por codificação por banco; isso não equivale a validar todas as imagens ou seus mipmaps. O banco `gta3.dxt` contém 10.160 texturas.

A árvore externa `texdb/` também existe; não foi determinado qual árvore o cliente efetivamente carrega. A extensão não garante a codificação: `player.pvr`, por exemplo, contém blocos DXT.

## Falhas reproduzidas

| Teste | Resultado |
| --- | --- |
| Imagem vermelha DXT de 8×8 → leitura | `(255,0,0,255)` vira `(0,0,255,255)` |
| Imagem azul DXT → leitura | Vermelho e azul também invertidos |
| DXT com alpha 85 → leitura | Alpha preservado na amostra; RGB invertido |
| Substituição de `ahoodfence2` no gta3 DXT | Banco salva e reabre, mas altera também `barbersflr1_LA` |
| Cabeçalho da textura vizinha | Primeiros quatro bytes mudam de `d3eaf083` para `00000000` |
| Metadados da textura substituída | `hassibling` e `png` são removidos |
| ETC1 `0x8d64` e PVRTC `0x8c01/0x8c02` | Decodificação não implementada pela biblioteca |
| `mobile.etc`/`mobile.pvr` | Erro de índice em amostras |
| `samp.unc` | Erro de índice ao enumerar os blocos |
| `txd.360` | Leitura fora do buffer ao enumerar os blocos |

Os dois últimos erros demonstram incompatibilidade do leitor com esses arquivos; não comprovam corrupção dos arquivos originais.

Na substituição testada, o fim do bloco calculado pela biblioteca é 10956, mas o próximo bloco começa em 10952. A escrita inclui quatro bytes do próximo cabeçalho. A implementação de decodificação também trata como RGBA os bytes com ordem BGRA retornados pelo decoder. Além das falhas reproduzidas, o código de criação força `no_mip=True`; preservação/regeneração de mipmaps requer trabalho específico.

## Decisão para o projeto

Implementar leitura com validação de limites e identificação por cabeçalho, corrigir a ordem dos canais e testar imagens conhecidas. Não expor o método `replace/save` da biblioteca diretamente na interface. A escrita precisa preservar os blocos não editados byte a byte e os metadados desconhecidos; versões futuras devem testar substituições de tamanhos menores, iguais e maiores, aliases e mipmaps. Iniciar pela leitura DXT e exportação PNG, com formatos não suportados explicitamente identificados.

Fontes inspecionadas: https://pypi.org/project/mobile-textdb/ e os módulos `codec.py`, `operations.py`, `database.py`, `toc.py`, `txt.py` e `models.py` da distribuição 0.4.0. Evidências completas em `results.json`.
