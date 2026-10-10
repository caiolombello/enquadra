# Componentes e atribuições

O código do Enquadra usa LGPL-3.0-or-later. Os textos completos estão em `COPYING` (GNU GPL v3) e `COPYING.LESSER` (GNU LGPL v3).

## Referência de ABI V4L2/UVC

A modelagem dos ioctls em `brio_control/camera.py` foi conferida no [cameractrls](https://github.com/soyersoyer/cameractrls/tree/6f388257ac21a0e91b143ad11cb2457036fa2c27), LGPL-3.0-or-later, revisão `6f388257ac21a0e91b143ad11cb2457036fa2c27`. Enquadra não importa o backend nem copia a classe de aplicação desse projeto. A atribuição original permanece no módulo.

## Modelos YuNet

Origem: [OpenCV Zoo — YuNet](https://github.com/opencv/opencv_zoo/tree/47534e27c9851bb1128ccc0102f1145e27f23f98/models/face_detection_yunet). Licença MIT completa: `brio_control/models/LICENSE`.

| Arquivo | Revisão OpenCV Zoo | SHA-256 |
| --- | --- | --- |
| `face_detection_yunet_2023mar.onnx` | `47534e27c9851bb1128ccc0102f1145e27f23f98` | `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4` |
| `face_detection_yunet_2022mar.onnx` | `7e062e54cf5410c09b795ff71b4a255e58498c79` | `50ef07f702a31741ca46a4c0d947773b64143b9362780237bf0d427d6c79bab7` |

O modelo 2023 é preferido; o 2022 atende versões do OpenCV que exigem a implementação anterior.

## Modelo PP-HumanSeg

Origem: [OpenCV Zoo — PP-HumanSeg](https://github.com/opencv/opencv_zoo/tree/47534e27c9851bb1128ccc0102f1145e27f23f98/models/human_segmentation_pphumanseg), revisão `47534e27c9851bb1128ccc0102f1145e27f23f98`. Copyright 2021 PaddlePaddle Authors. Licença Apache-2.0 completa: `brio_control/models/pphumanseg/LICENSE`.

Arquivo: `brio_control/models/pphumanseg/human_segmentation_pphumanseg_2023mar.onnx`.

SHA-256: `552d8a984054e59b5d773d24b9b12022b22046ceb2bbc4c9aaeaceb36a9ddf24`.

Os hashes são verificados pelo aplicativo antes do carregamento dos modelos e pelo comando `enquadra doctor`. As licenças completas acompanham também o pacote Debian.
