<div align="center">
  <img src="logo.png" alt="DeepMatrix Logo" width="120" />
  <h1 align="center">深维工坊 DeepMatrix</h1>
  <p align="center">基于 YOLOv8 的货架商品识别与标注系统</p>
</div>

<p align="center">
  <a href="#项目简介">项目简介</a> •
  <a href="#功能特性">功能特性</a> •
  <a href="#快速开始">快速开始</a> •
  <a href="#api-文档">API</a> •
  <a href="#项目结构">项目结构</a> •
  <a href="#源码构建">构建</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10+-blue" alt="Python 3.10+" />
  <img src="https://img.shields.io/badge/framework-FastAPI-green" alt="FastAPI" />
  <img src="https://img.shields.io/badge/model-YOLOv8-orange" alt="YOLOv8" />
  <img src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS-lightgrey" alt="Platform" />
</p>

---

## 项目简介 | About

DeepMatrix（深维工坊）是一个面向货架商品的计算机视觉识别与标注系统。它覆盖了完整的业务闭环：上传货架照片、使用 YOLOv8 模型检测商品、在浏览器中标注或修正检测结果、构建专属数据集、训练自定义模型，以及将训练好的模型发布到生产环境。

支持 Windows 和 macOS 原生桌面应用（PyInstaller 打包），并通过 GitHub Actions 实现自动化 CI/CD 构建。

---

## 功能特性 | Features

- **图像识别** -- 上传货架照片，基于 YOLOv8 实时检测商品，输出边界框、类别名称和置信度
- **在线标注** -- 内置基于 Annotorious 的可视化标注工具，支持绘制边界框、标注类别、修正模型预测结果
- **数据集管理** -- 支持以 ZIP 压缩包导入数据集，浏览已标注图片，管理训练/验证集划分，类别定义完全可自定义
- **模型训练** -- 在界面上直接启动 YOLOv8 训练，可配置训练轮数、批次大小、优化器、学习率和数据增强策略
- **模型生命周期** -- 发布的模型热加载生效，无需重启服务，通过 API 可列出、发布和删除模型版本
- **手机上传** -- 扫描二维码即可从手机浏览器直接上传货架照片
- **跨平台桌面应用** -- 基于 PyInstaller 构建原生 macOS / Windows 应用，macOS 版本经过签名和公证
- **GitHub Actions CI/CD** -- 自动构建流水线，产出 Windows NSIS 安装包和 macOS DMG

---

## 技术栈 | Tech Stack

| 层       | 技术                                                            |
|----------|----------------------------------------------------------------|
| 后端     | Python 3.10+, FastAPI, Uvicorn                                 |
| 模型引擎 | Ultralytics YOLOv8 (PyTorch)                                   |
| 前端     | Vue 3, Element Plus, Annotorious                               |
| 桌面壳   | pywebview（原生 WebView），自动降级为浏览器                      |
| 打包     | PyInstaller, NSIS (Windows), DMG (macOS)                       |
| CI/CD    | GitHub Actions                                                  |

---

## 快速开始 | Getting Started

### 前置要求 | Prerequisites

- Python 3.10 或更高版本
- pip

### 手动启动 | Manual Setup

```bash
# 1. 克隆仓库
git clone <仓库地址>
cd 货架商品识别v2

# 2. 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate       # macOS / Linux
# .venv\Scripts\activate        # Windows

# 3. 安装依赖
pip install -r backend/requirements.txt

# 4. 启动服务
python backend/start.py
```

启动后自动在浏览器打开 `http://127.0.0.1:8000`。如果系统装有 pywebview，则以原生桌面窗口运行。

### Windows 一键启动

双击 `run.bat` -- 自动创建虚拟环境、安装依赖并启动服务。

---

## API 文档 | API Reference

所有 REST API 都以应用根路径提供服务。

| 端点                          | 方法   | 说明                            |
|-------------------------------|--------|---------------------------------|
| `/infer/health`               | GET    | 健康检查                        |
| `/infer/upload`               | POST   | 上传图片并运行推理               |
| `/datasets/list`              | GET    | 列出所有数据集                   |
| `/datasets/create`            | POST   | 创建数据集（ZIP 或本地路径）     |
| `/datasets/{id}/images`       | GET    | 列出数据集中的图片               |
| `/annotation/save`            | POST   | 保存图片标注结果                 |
| `/train/start`                | POST   | 启动模型训练                     |
| `/train/status/{job_id}`      | GET    | 查询训练进度                     |
| `/train/stop/{job_id}`        | POST   | 停止训练任务                     |
| `/models/list`                | GET    | 列出已训练的模型版本             |
| `/models/publish`             | POST   | 发布某个模型版本                 |
| `/app/version`                | GET    | 应用版本号                       |
| `/system/ips`                 | GET    | 显示本机局域网地址               |
| `/classes`                    | GET    | 获取/设置全局类别列表            |

### 推理示例 | Inference Example

```bash
curl -X POST http://localhost:8000/infer/upload \
  -F "image=@shelf_photo.jpg" \
  -F "conf=0.25"
```

返回结果：

```json
{
  "boxes": [
    {
      "box": [120, 45, 340, 280],
      "class_id": 0,
      "class": "cola",
      "confidence": 0.92
    }
  ],
  "model_version_id": "abc123-20250205-143022",
  "model_source": "published"
}
```

### 训练示例 | Training Example

```bash
curl -X POST http://localhost:8000/train/start \
  -H "Content-Type: application/json" \
  -d '{
    "dataset_id": "abc12345",
    "epochs": 50,
    "batch": 8,
    "imgsz": 640,
    "device": "cpu",
    "optimizer": "AdamW",
    "lr0": 0.001,
    "augment": true
  }'
```

---

## 项目结构 | Project Structure

```
.
├── backend/                          # Python 后端
│   ├── app/
│   │   ├── main.py                   # FastAPI 应用及全部路由
│   │   ├── model_manager.py          # YOLO 模型加载与推理管理
│   │   ├── schemas.py                # Pydantic 数据模型
│   │   ├── storage.py                # 文件存储工具
│   │   └── version.py                # 版本号
│   ├── frontend/                     # Web 前端 (Vue 3 SPA)
│   │   ├── index.html                # 主界面
│   │   ├── mobile_upload.html        # 手机上传页面
│   │   └── libs/                     # 前端依赖库
│   ├── desktop.py                    # 桌面应用入口 (pywebview)
│   ├── start.py                      # 浏览器模式启动器
│   ├── main.py                       # Uvicorn 启动脚本
│   ├── requirements.txt
│   └── models/                       # 预置模型文件
├── packaging/
│   ├── assets/                       # 应用图标 (ico, icns)
│   ├── macos/                        # macOS 代码签名授权文件
│   └── pyinstaller/                  # PyInstaller 打包配置
├── installer/windows/                # Windows NSIS 安装脚本
├── scripts/
│   ├── generate_icons.py             # 图标生成工具
│   └── make_dmg.sh                   # macOS DMG 制作脚本
├── logo.png                          # 应用 Logo
├── version.txt
├── run.bat                           # Windows 一键启动脚本
├── start_v2.bat                      # Windows 启动脚本（嵌入式环境）
└── .github/workflows/release.yml     # CI/CD 流水线配置
```

---

## 源码构建 | Building from Source

### 前置要求 | Prerequisites

- Python 3.10 或更高版本
- PyInstaller
- Windows: NSIS（用于生成安装包）
- macOS: Xcode 命令行工具（用于代码签名）

### 构建命令 | Build Commands

```bash
# 安装构建依赖
pip install pyinstaller

# 生成应用图标
python scripts/generate_icons.py

# macOS 构建
pyinstaller --noconfirm --clean --distpath dist --workpath build packaging/pyinstaller/DeepMatrix-mac.spec

# Windows 构建
pyinstaller --noconfirm --clean --distpath dist --workpath build packaging/pyinstaller/DeepMatrix.spec
```

打包后的应用会自动包含前端页面、基础模型等所有资源文件。

### CI/CD 流水线 | CI/CD Pipeline

GitHub Actions 工作流 (`release.yml`) 为三个目标平台自动构建：

| 目标平台      | Runner         | 输出文件                              |
|---------------|----------------|---------------------------------------|
| Windows x64   | windows-latest | NSIS 安装包 (.exe)                    |
| macOS x64     | macos-15       | 已签名 DMG                            |
| macOS arm64   | macos-14       | 已签名 DMG (Apple Silicon)            |

在 GitHub Actions 页面手动触发工作流，可指定草稿或预发布选项。

---

## 数据存储 | Storage Layout

数据集、模型和训练产物保存在：

| 平台     | 路径                                                    |
|----------|---------------------------------------------------------|
| macOS    | `~/Library/Application Support/GoodsRecognitionModel/`  |
| Windows  | `%LOCALAPPDATA%/GoodsRecognitionModel/`                 |
| Linux    | `~/.local/share/GoodsRecognitionModel/`                 |

开发模式下（未使用 PyInstaller 打包时），数据保存在 `backend/` 目录下。

---

## 许可协议 | License

本项目供个人及教育用途使用。内置的 YOLOv8 模型遵循 Ultralytics 的 [AGPL-3.0 协议](https://github.com/ultralytics/ultralytics/blob/main/LICENSE)。

---

## 致谢 | Acknowledgments

- [Ultralytics YOLOv8](https://github.com/ultralytics/ultralytics) -- 目标检测引擎
- [Annotorious](https://annotorious.dev/) -- 图像标注工具
- [Vue 3](https://vuejs.org/) & [Element Plus](https://element-plus.org/) -- 前端框架
