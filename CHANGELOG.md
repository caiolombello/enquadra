# Histórico de versões

## v0.1.0-alpha.1

Primeira versão de pré-lançamento do Enquadra.

- Interface GTK 4 para controles V4L2/UVC da Logitech Brio 500.
- Preview, enquadramento digital e automático local com YuNet, perfis e espelhamento.
- Desfoque e substituição de fundo PNG/JPEG com PP-HumanSeg local.
- Serviço de vídeo separado da interface, diagnóstico e comandos de controle.
- Pacote Debian com launcher `enquadra`, alias `brio-control` e preparação explícita de câmera virtual.
- Identidade independente e preservação dos perfis/configuração existentes.

Hardware validado: Brio 500 (`046d:0943`) em Ubuntu 26.04.1. Ubuntu 24.04 permanece sem validação de hardware. A rotação física do Show Mode foi observada pelo usuário; controle nativo de ativação/desativação, HDR e paridade com RightSight/Logi Tune não fazem parte desta versão.
