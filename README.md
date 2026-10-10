# Enquadra

**Sua câmera, seu enquadramento. Tudo no Linux.**

Enquadra combina controles de câmera, enquadramento automático local e efeitos de fundo em uma interface GTK 4. O vídeo é processado no computador e pode ser enviado para uma câmera virtual usada por aplicativos de reunião.

[Conheça o produto](https://caiolombello.github.io/enquadra/) · [Downloads](https://github.com/caiolombello/enquadra/releases) · [Relatar problema](https://github.com/caiolombello/enquadra/issues)

## Status

Primeira versão de pré-lançamento: **v0.1.0-alpha.1**. É software em desenvolvimento, com foco inicial na Logitech Brio 500. A validação de hardware foi realizada no Ubuntu 26.04.1 com a Brio 500 (`046d:0943`). Ubuntu 24.04 é um alvo de compatibilidade de distribuição, ainda sem validação de hardware nesta versão. Outras câmeras não têm garantia de suporte.

Enquadra é um projeto independente de Caio Lombello, sem afiliação com a Logitech.

## Recursos

- Controles V4L2/UVC disponíveis na câmera e seleção de campo de visão na Brio 500.
- Zoom e enquadramento digital, espelhamento e perfis salvos localmente.
- Enquadramento automático por detecção de rosto com YuNet, executado localmente.
- Desfoque ou substituição de fundo por imagem PNG/JPEG local, com segmentação PP-HumanSeg.
- Preview local e saída para câmera virtual via v4l2loopback.
- Serviço independente da janela, controle por linha de comando e diagnóstico sem captura.

Os recursos automáticos são implementados por software; não equivalem ao RightSight nem representam paridade com o Logi Tune. HDR e um comando nativo para ativar/desativar Show Mode não são oferecidos. A rotação automática ao inclinar fisicamente a câmera foi observada pelo usuário no firmware da Brio 500; Enquadra não a apresenta como controle nativo validado.

## Instalar o pacote Debian

Na página de [releases](https://github.com/caiolombello/enquadra/releases/tag/v0.1.0-alpha.1), baixe `enquadra_0.1.0.alpha1_all.deb` e `SHA256SUMS` da mesma versão. No diretório dos downloads:

```sh
sha256sum --check SHA256SUMS
sudo apt install ./enquadra_0.1.0.alpha1_all.deb
enquadra
```

Só instale se a verificação SHA-256 do arquivo baixado terminar com `OK`.

O pacote utiliza Python, GTK 4, PyGObject, GStreamer, NumPy e OpenCV da distribuição. Não instala dependências com `pip` global nem prepara automaticamente módulos do kernel.

### Preparar a câmera virtual

O preview funciona sem v4l2loopback. Para habilitar a saída virtual, execute explicitamente:

```sh
sudo apt install pkexec mokutil
enquadra setup-virtual-camera
```

A preparação pede autenticação pelo Polkit, instala `v4l2loopback-dkms`, suas ferramentas e `v4l-utils` pelo APT e cria a saída **Enquadra** em `/dev/video10`. Os headers do kernel em execução precisam estar instalados. Se Secure Boot exigir assinatura/cadastro MOK do módulo DKMS, siga as instruções da distribuição e reinicie quando necessário.

Configurações ou dispositivos v4l2loopback de outros programas são preservados; conflitos interrompem a preparação. A configuração exata da versão anterior é mantida, inclusive o nome **Brio Control** da câmera virtual. O script não descarrega módulos nem interrompe câmeras existentes.

Abra Enquadra, inicie o vídeo e selecione **Enquadra** no aplicativo de reunião; uma instalação anterior pode manter **Brio Control**. Aplicativos com restrições de sandbox podem precisar de acesso à câmera concedido pelo sistema.

## Linha de comando

```sh
enquadra --version
enquadra doctor
enquadra start --preview-only
enquadra start --output /dev/video10 --size 720 --autoframe
enquadra status
enquadra stop
enquadra shutdown
```

Sem argumentos, `enquadra` abre a interface. A captura começa por uma ação explícita na interface ou pelo comando `start`. O alias `brio-control`, o módulo Python `brio_control` e os diretórios privados de configuração/estado `brio-control` permanecem compatíveis com os perfis existentes.

## Executar a partir do código

Use as dependências GTK/GStreamer/Python do sistema em Ubuntu ou Debian:

```sh
sudo apt install python3 python3-gi python3-numpy python3-opencv gir1.2-gtk-4.0 gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 gstreamer1.0-plugins-good gstreamer1.0-plugins-base
./scripts/run-local
python3 -m unittest discover -s tests
python3 scripts/build_deb.py
```

Python 3.12 ou superior é necessário. O pacote resultante fica em `dist/enquadra_0.1.0~alpha1_all.deb`. A pasta `.runtime` é um ambiente local opcional de desenvolvimento e não faz parte da distribuição. O executável principal distribuído é o pacote `.deb`; o manifesto Python descreve o módulo e os comandos, mas não substitui a instalação das bibliotecas GTK do sistema.

## Privacidade e licenças

A captura, detecção de rosto e segmentação são locais. A imagem de fundo permanece no computador. Nenhum serviço remoto é necessário para esses recursos; o aplicativo de reunião escolhido controla o envio do vídeo para terceiros.

O código é licenciado sob **LGPL-3.0-or-later**, com os textos GNU em `COPYING` e `COPYING.LESSER`. Os modelos YuNet usam MIT e PP-HumanSeg usa Apache-2.0, com licenças completas incluídas. Os modelos estão fixados por revisão de origem e SHA-256: YuNet 2023 é o principal, com fallback 2022 para compatibilidade com OpenCV. Veja [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) para atribuições e hashes.
