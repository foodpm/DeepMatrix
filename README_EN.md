<div align="center">
  <img src="logo.png" alt="DeepMatrix Logo" width="120" />
  <h1 align="center">DeepMatrix</h1>
  <p align="center">YOLOv8-based Shelf Product Recognition & Annotation System</p>
</div>

<p align="center">
  <a href="#about">About</a> •
  <a href="#features">Features</a> •
  <a href="#getting-started">Getting Started</a> •
  <a href="#api-reference">API</a> •
  <a href="#project-structure">Structure</a> •
  <a href="#building-from-source">Build</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10+-blue" alt="Python 3.10+" />
  <img src="https://img.shields.io/badge/framework-FastAPI-green" alt="FastAPI" />
  <img src="https://img.shields.io/badge/model-YOLOv8-orange" alt="YOLOv8" />
  <img src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS-lightgrey" alt="Platform" />
</p>

<p align="center">
  <a href="README.md">中文版</a>
</p>

---

## About

DeepMatrix is a full-stack computer vision application for recognizing and annotating products on retail shelves. It provides an end-to-end workflow: upload shelf photos, run YOLOv8 inference to detect products, annotate or correct detections with a browser-based labeling tool, build your own datasets, train custom models, and publish them for production use.

The application ships as a native desktop app (PyInstaller-bundled) for both Windows and macOS, with automated CI/CD builds via GitHub Actions.

---

## Features

- **Image recognition** -- Upload shelf photos and get real-time YOLOv8-based product detection with bounding boxes, class labels, and confidence scores
- **Web-based annotation** -- Built-in annotation interface powered by Annotorious. Draw bounding boxes, assign class labels, and correct model predictions interactively
- **Dataset management** -- Import datasets as ZIP archives, browse labeled images, and manage train/val splits. Class definitions are fully customizable
- **Custom model training** -- Train YOLOv8 models on your own labeled datasets directly from the UI. Configure epochs, batch size, optimizer, learning rate, and augmentation on the fly
- **Model lifecycle** -- Published models are hot-swapped at inference time without restarting the server. List, publish, and delete model versions through the API
- **Mobile upload** -- Scan a QR code with your phone to upload shelf photos directly from a mobile browser
- **Cross-platform desktop app** -- Native macOS and Windows desktop applications built with PyInstaller. macOS builds are codesigned and notarized
- **GitHub Actions CI/CD** -- Automated build pipeline producing Windows NSIS installers and macOS DMGs

---

## Tech Stack

| Layer          | Technology                                                                 |
|----------------|----------------------------------------------------------------------------|
| Backend        | Python 3.10+, FastAPI, Uvicorn                                             |
| ML Model       | Ultralytics YOLOv8 (PyTorch)                                               |
| Frontend       | Vue 3, Element Plus, Annotorious                                           |
| Desktop Shell  | pywebview (native WebView), falls back to browser                          |
| Packaging      | PyInstaller, NSIS (Windows), DMG (macOS)                                   |
| CI/CD          | GitHub Actions                                                             |

---

## Download

Pre-built installers are available on the [**Releases**](https://github.com/foodpm/DeepMatrix/releases) page.

| Platform         | Format | Notes                                           |
|------------------|--------|-------------------------------------------------|
| Windows x64      | .exe   | NSIS installer, includes Python runtime         |
| macOS Intel      | .dmg   | Codesigned and notarized                        |
| macOS Apple Silicon | .dmg | Codesigned and notarized                       |

---

## Getting Started

### Prerequisites

- Python 3.10 or later
- pip

### Manual Setup

```bash
# 1. Clone the repository
git clone <repo-url>
cd shelf-product-recognition

# 2. Set up a virtual environment
python3 -m venv .venv
source .venv/bin/activate   # macOS / Linux
# .venv\Scripts\activate    # Windows

# 3. Install dependencies
pip install -r backend/requirements.txt

# 4. Start the server
python backend/start.py
```

The application opens in your browser at `http://127.0.0.1:8000`. If pywebview is installed, it launches as a native desktop window instead.

### Windows One-Click

Double-click `run.bat` -- it creates a virtual environment, installs dependencies, and starts the service automatically.

---

## API Reference

All REST endpoints are served at the application root.

| Endpoint                    | Method | Description                            |
|-----------------------------|--------|----------------------------------------|
| `/infer/health`             | GET    | Health check                           |
| `/infer/upload`             | POST   | Upload image and run inference         |
| `/datasets/list`            | GET    | List all datasets                      |
| `/datasets/create`          | POST   | Create dataset (ZIP or local path)     |
| `/datasets/{id}/images`     | GET    | List images in a dataset               |
| `/annotation/save`          | POST   | Save annotations for an image          |
| `/train/start`              | POST   | Start model training                   |
| `/train/status/{job_id}`    | GET    | Query training progress                |
| `/train/stop/{job_id}`      | POST   | Stop a training job                    |
| `/models/list`              | GET    | List trained model versions            |
| `/models/publish`           | POST   | Publish a model version                |
| `/app/version`              | GET    | Application version                    |
| `/system/ips`               | GET    | Show local network addresses           |
| `/classes`                  | GET    | Get/Set global class list              |

### Inference Example

```bash
curl -X POST http://localhost:8000/infer/upload \
  -F "image=@shelf_photo.jpg" \
  -F "conf=0.25"
```

Response:

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

### Training Example

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

## Project Structure

```
.
├── backend/                          # Python backend
│   ├── app/
│   │   ├── main.py                   # FastAPI app & all routes
│   │   ├── model_manager.py          # YOLO model loading/inference
│   │   ├── schemas.py                # Pydantic models
│   │   ├── storage.py                # Filesystem storage helpers
│   │   └── version.py                # Version constant
│   ├── frontend/                     # Web UI (Vue 3 SPA)
│   │   ├── index.html                # Main page
│   │   ├── mobile_upload.html        # Mobile upload page
│   │   └── libs/                     # Vendor JS/CSS
│   ├── desktop.py                    # Desktop app entry (pywebview)
│   ├── start.py                      # Browser-based launcher
│   ├── main.py                       # Uvicorn runner
│   ├── requirements.txt
│   └── models/                       # Pre-bundled model files
├── packaging/
│   ├── assets/                       # App icons (ico, icns)
│   ├── macos/                        # Code signing entitlements
│   └── pyinstaller/                  # PyInstaller .spec files
├── installer/windows/                # NSIS installer script
├── scripts/
│   ├── generate_icons.py             # Icon generation utility
│   └── make_dmg.sh                   # macOS DMG creation script
├── logo.png                          # Application logo
├── version.txt
├── run.bat                           # Windows one-click launcher
├── start_v2.bat                      # Windows launcher (embedded env)
├── README.md                         # Chinese documentation
├── README_EN.md                      # English documentation (this file)
└── .github/workflows/release.yml     # CI/CD pipeline
```

---

## Building from Source

### Prerequisites

- Python 3.10+
- PyInstaller
- Windows: NSIS (for the installer)
- macOS: Xcode command-line tools (for codesigning)

### Build Commands

```bash
# Install build dependencies
pip install pyinstaller

# Generate app icons
python scripts/generate_icons.py

# macOS build
pyinstaller --noconfirm --clean --distpath dist --workpath build packaging/pyinstaller/DeepMatrix-mac.spec

# Windows build
pyinstaller --noconfirm --clean --distpath dist --workpath build packaging/pyinstaller/DeepMatrix.spec
```

### CI/CD Pipeline

The GitHub Actions workflow (`release.yml`) builds for three targets:

| Target       | Runner        | Output                    |
|--------------|---------------|---------------------------|
| Windows x64  | windows-latest | NSIS installer (.exe)    |
| macOS x64    | macos-15       | Codesigned DMG           |
| macOS arm64  | macos-14       | Codesigned DMG (Apple Silicon) |

Trigger the workflow manually from the Actions tab with optional draft/prerelease flags.

---

## Storage Layout

Datasets, models, and training runs are stored at:

| Platform | Path                                                    |
|----------|---------------------------------------------------------|
| macOS    | `~/Library/Application Support/GoodsRecognitionModel/`  |
| Windows  | `%LOCALAPPDATA%/GoodsRecognitionModel/`                 |
| Linux    | `~/.local/share/GoodsRecognitionModel/`                 |

In development mode (not frozen by PyInstaller), data is stored in `backend/`.

---

## License

This project is distributed for personal and educational use. The bundled YOLOv8 model is subject to the [AGPL-3.0 license](https://github.com/ultralytics/ultralytics/blob/main/LICENSE) from Ultralytics.

---

## Acknowledgments

- [Ultralytics YOLOv8](https://github.com/ultralytics/ultralytics) -- Object detection engine
- [Annotorious](https://annotorious.dev/) -- Image annotation toolkit
- [Vue 3](https://vuejs.org/) & [Element Plus](https://element-plus.org/) -- Frontend framework
