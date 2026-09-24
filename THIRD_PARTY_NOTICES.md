# Third-party notices

This source repository depends on third-party packages installed separately through requirements.txt. It does not include their source trees, wheels, Python runtime, or model weights.

| Component | Version used | License / upstream |
| --- | --- | --- |
| PaddlePaddle | 3.3.1 | [Apache-2.0](https://github.com/PaddlePaddle/Paddle/blob/develop/LICENSE) |
| PaddleOCR | 3.7.0 | [Apache-2.0](https://github.com/PaddlePaddle/PaddleOCR/blob/main/LICENSE) |
| PaddleX | 3.7.2 | [Apache-2.0](https://github.com/PaddlePaddle/PaddleX/blob/release/3.0/LICENSE) |
| Gradio | 6.27.0 | [Apache-2.0](https://github.com/gradio-app/gradio/blob/main/LICENSE) |
| FastAPI | 0.141.1 | [MIT](https://github.com/fastapi/fastapi/blob/master/LICENSE) |
| OpenCV | 4.10.0.84 | [Apache-2.0](https://github.com/opencv/opencv/blob/4.x/LICENSE) |
| pypdfium2 | 5.13.0 | [Apache-2.0 OR BSD-3-Clause; PDFium has separate notices](https://github.com/pypdfium2-team/pypdfium2/blob/main/README.md) |

Other packages and transitive dependencies retain their own license terms. Review installed package metadata and license files when distributing a complete binary package. PaddleOCR and PaddleX model weights are obtained from their official channels at runtime and are not included in this repository.

When redistributing dependencies or model weights, retain their applicable license texts, copyright notices and other required notices, including those of bundled native libraries. Upstream links above are references; they do not replace the notices required for binary redistribution.

Project-specific source code is licensed under the [Apache License 2.0](LICENSE). This does not change the rights or license terms of any third-party component.
