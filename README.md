# MedCert OCR

基于 PaddleOCR 的本地医疗行业证照文字识别与结构化工具。当前社区源码版适用于 Windows x64、Python 3.11 和 CPU 推理。文档默认留在本机，无需上传第三方 OCR 服务；识别结果需要人工核对，不用于证照真伪鉴定。

## 主要功能

- PP-OCRv6 通用文字识别、文字坐标与段落整理
- 证照类型判断、字段提取、候选值与原图证据定位
- “已提取 / 待核对 / 未识别”字段状态和人工修订
- PDF、多页 TIFF 与常见图片格式的逐页处理
- 印章区域处理与可选印章文字识别
- FastAPI 本地接口、浏览器工作台和本地任务历史
- Windows x64 CPU 运行，无需独立显卡

## 证照范围

代码中包含营业执照、医疗器械注册证、医疗器械经营许可证、医疗器械生产许可证、第一类医疗器械生产备案、第一类医疗器械产品备案、第二类医疗器械经营备案、医疗机构执业许可证、药品经营许可证、消毒产品生产企业卫生许可证的字段规则。不同版式的识别表现不同；类型判断不确定时可手动选择。

## 技术栈

Python、PaddlePaddle、PaddleOCR、PaddleX、OpenCV、pypdfium2、FastAPI、Gradio、HTML、CSS 和 JavaScript。

## 安装

需要 Windows x64 和 Python 3.11 x64。下载源码并解压，或使用 Git：

```powershell
git clone https://github.com/pm329946595/medcert-ocr-workbench.git
cd medcert-ocr-workbench
```

在源码目录打开 PowerShell，依次运行：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\start.bat
```

若系统没有 `py`，请确认 `python --version` 为 3.11，再将首行的 `py -3.11` 换成 `python`。也可用 `.\.venv\Scripts\python.exe app.py` 启动。安装中断时重新执行依赖安装命令。

首次启动需要联网取得 PaddleOCR 官方 PP-OCRv6 模型（`PP-OCRv6_medium_det` 和 `PP-OCRv6_medium_rec`）；首次使用印章文字专项识别时还需取得官方印章模型 `PP-OCRv4_server_seal_det`。模型保存在项目的 models/ 目录中。网络受限的环境可自行从 PaddleOCR / PaddleX 官方渠道取得对应模型并放入 models/official_models/ 下的同名目录；仓库不分发模型权重。

start.bat 在缺少 .venv 时会创建环境并安装依赖。安装后的启动可直接双击。依赖安装可能需要较长时间，取决于网络连接。

## 使用

启动后打开终端提示的本地地址，通常为 http://127.0.0.1:7860/。导入文件，选择自动判断、通用 OCR 或具体证照类型，设置页码和识别质量，开始识别。完成后对照原图核对字段，并保存修订或导出结果。

本地 API 包括：

- GET /api/health：服务状态及支持的类型
- GET /api/capabilities：格式、页数和运行参数
- POST /api/jobs：提交异步逐页识别任务
- GET /api/jobs/{id}：查询任务状态
- POST /api/ocr/{document_type}：识别指定页

服务只监听 127.0.0.1。若确需允许其他网页在浏览器中访问本机 API，可将 config.template.json 复制为 config.json 并填写准确的 allowed_origins。默认允许本机来源（localhost / 127.0.0.1）、本地文件页面的 null 来源及无 Origin 的本机客户端，不使用通配跨域许可。服务面向本机使用，没有多用户身份认证，请勿通过反向代理对外开放。接口参数可阅读 certificate_api.py 与 workbench_api.py。

停止服务可关闭启动窗口，或运行 .\stop.bat。

## 目录

- parsers/：证照类型与字段规则
- workbench/：本地浏览器界面
- app.py、ocr_service.py：入口和 OCR 引擎
- certificate_api.py、workbench_api.py：本地 HTTP API
- document_input.py：PDF 与图片载入
- models/、outputs/、work/：运行后在本机生成，不进入仓库

## 已知限制与隐私

OCR 和结构化结果可能因低清晰度、印章遮挡、复杂折页、扫描方向及证照版式变化而出错，所有字段应结合原图核对。本工具不鉴定证照真伪，也不验证证照当前法律效力。

程序默认本地运行；仓库不包含真实医院、客户或证照资料。导入的文件、识别结果和修订记录保存在本机项目目录中，请自行管理文件访问权限与清理周期。

## 开源组件与许可

本项目依赖多个开源组件，详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。本项目自有源码采用 [Apache License 2.0](LICENSE)，第三方组件保留各自版权和许可证。当前社区版本为 v0.1.0，仅提供源码，不包含模型或 Python 运行环境。

Community edition maintained by PM.
