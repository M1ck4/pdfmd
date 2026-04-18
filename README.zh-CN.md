# PDF 转 Markdown 转换器（pdfmd）

[English](README.md) | [简体中文](README.zh-CN.md)

**一个注重隐私、支持桌面界面与命令行的 PDF 转 Markdown 工具。它可以将普通 PDF 和扫描版 PDF 转换为结构清晰、便于后续编辑的 Markdown 文档。**

**快速、本地、智能、完全离线。**

![Python Version](https://img.shields.io/badge/python-3.8%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)
![Version](https://img.shields.io/badge/version-1.6.0-purple)

---

## 目录

- [项目特点](#项目特点)
- [核心功能](#核心功能)
- [安装](#安装)
- [OCR 环境配置](#ocr-环境配置)
- [使用方式](#使用方式)
- [命令行示例](#命令行示例)
- [Python API](#python-api)
- [输出效果说明](#输出效果说明)
- [常见问题](#常见问题)
- [参与贡献](#参与贡献)
- [许可证](#许可证)

---

## 项目特点

很多 PDF 转换工具会把文件上传到远程服务器处理，而 `pdfmd` 的设计目标正好相反：

- **完全本地处理**：文档不会离开你的电脑
- **无遥测、无跟踪**：不上传、不采集使用数据
- **支持敏感文档场景**：适合研究、法律、医疗、企业内部文档
- **同时提供 GUI 和 CLI**：既适合普通用户，也适合自动化处理

如果你的首要要求是“可离线、可本地、可控”，这个项目就是围绕这个目标构建的。

---

## 核心功能

### 1. PDF 转 Markdown

- 自动还原段落
- 保留标题层级
- 识别常见列表
- 处理换行断词
- 自动链接 URL
- 支持粗体、斜体等基础格式

### 2. 表格识别

- 检测列对齐文本表格
- 识别边框风格表格
- 将表格输出为 Markdown pipe table
- 尽量降低正文误判为表格的概率

### 3. 数学公式支持

- 识别 Unicode 数学符号
- 将常见数学表达转换为 LaTeX 风格
- 尽量保留已有的 `$...$` 与 `$$...$$`
- 避免公式内容被普通 Markdown 转义破坏

### 4. 扫描件 OCR

- 支持 `tesseract`
- 支持 `ocrmypdf`
- 支持自动判断页面是否需要 OCR
- 支持多语言 OCR
- 支持扫描 PDF 与普通 PDF 混合场景

### 5. 图形界面与交互式 CLI

- 提供桌面 GUI
- 提供传统命令行参数模式
- 提供引导式交互 CLI
- 支持中文交互界面

---

## 安装

### 方式一：使用 pip

```bash
pip install -e .
```

如果需要 OCR：

```bash
pip install pytesseract pillow
```

如果还需要 `ocrmypdf`：

```bash
pip install ocrmypdf
```

### 方式二：使用 uv

在项目目录下：

```powershell
uv venv
.venv\Scripts\Activate.ps1
uv pip install -e .
uv pip install pytesseract pillow
```

如果你需要 `ocrmypdf`：

```powershell
uv pip install ocrmypdf
```

---

## OCR 环境配置

Python 包安装完成后，还需要准备系统级 OCR 工具。

### Tesseract

`tesseract` 模式必须安装 Tesseract 可执行程序。

Windows 推荐：

- [UB Mannheim Tesseract](https://github.com/UB-Mannheim/tesseract/wiki)

安装完成后，请确认它已加入 `PATH`，然后执行：

```powershell
tesseract --version
tesseract --list-langs
```

如果你要做中英混合识别，至少需要准备：

- `eng.traineddata`
- `chi_sim.traineddata`

### OCRmyPDF

如果你要使用 `ocrmypdf` 模式，除了 Python 包之外，还要确保 `ocrmypdf` 命令可用：

```powershell
ocrmypdf --version
```

建议先把 `tesseract` 模式跑通，再尝试 `ocrmypdf`。

---

## 使用方式

### 图形界面

启动 GUI：

```bash
python -m pdfmd.app_gui
```

GUI 适合：

- 拖选文件
- 调整 OCR 选项
- 查看进度和日志
- 批量转换

### 传统 CLI

最简单的转换：

```bash
python -m pdfmd input.pdf
```

指定输出文件：

```bash
python -m pdfmd input.pdf -o output.md
```

开启自动 OCR：

```bash
python -m pdfmd input.pdf --ocr auto
```

### 交互式 CLI

如果你希望一步一步选择参数：

```bash
python -m pdfmd --interactive
```

中文界面：

```bash
python -m pdfmd --interactive --ui-lang zh
```

如果在交互终端中直接运行：

```bash
python -m pdfmd
```

也会自动进入交互式模式。

---

## 命令行示例

### 1. 普通 PDF 转 Markdown

```bash
python -m pdfmd report.pdf
```

### 2. 输出到指定文件

```bash
python -m pdfmd report.pdf -o notes.md
```

### 3. 自动 OCR

```bash
python -m pdfmd scan.pdf --ocr auto
```

### 4. 强制使用 Tesseract

```bash
python -m pdfmd scan.pdf --ocr tesseract --lang chi_sim+eng
```

### 5. 只预览前几页

```bash
python -m pdfmd long.pdf --preview-only --stats
```

### 6. 批量转换

```bash
python -m pdfmd *.pdf -o out_md
```

### 7. 导出图片资源

```bash
python -m pdfmd input.pdf --export-images
```

---

## Python API

你也可以直接在 Python 中调用：

```python
from pdfmd import Options, pdf_to_markdown

opts = Options(
    ocr_mode="auto",
    ocr_lang="chi_sim+eng",
    preview_only=False,
    insert_page_breaks=False,
    export_images=False,
)

pdf_to_markdown("input.pdf", "output.md", opts)
```

---

## 输出效果说明

转换后的 Markdown 通常会包含这些能力：

- 自动拆出标题
- 尽量合并被换行打断的段落
- 尽量把表格转成 Markdown 表格
- 尽量保留数学表达
- 可选插入分页标记 `---`
- 可选导出图片到 `_assets` 目录

对于扫描版 PDF，最终质量会明显受这些因素影响：

- 原始扫描清晰度
- 页面倾斜情况
- 字体与布局复杂度
- OCR 语言选择是否准确

---

## 常见问题

### 1. `python -m pdfmd` 无法启动

请确认你安装的是当前项目代码，并且模块入口可用：

```bash
python -m pdfmd --version
```

### 2. Tesseract 找不到语言包

如果看到类似错误：

```text
Tesseract couldn't load any languages
```

通常说明：

- `tessdata` 目录里没有对应语言文件
- 或 `TESSDATA_PREFIX` 配置不正确

### 3. `ocrmypdf` 模式无法运行

请先验证：

```bash
ocrmypdf --version
```

并确认系统里已经装好依赖工具。

### 4. 扫描件识别结果有较多空格或错字

这是 OCR 场景中常见的问题。可以优先尝试：

- 提高扫描清晰度
- 选择更合适的 OCR 语言
- 先试 `tesseract`
- 再对比 `ocrmypdf`

---

## 参与贡献

欢迎提交 issue、改进建议或 Pull Request。

适合贡献的方向包括：

- OCR 准确率提升
- 表格识别增强
- 数学公式处理增强
- CLI / GUI 体验优化
- 文档与测试补充

---

## 许可证

本项目采用 [MIT License](LICENSE)。
