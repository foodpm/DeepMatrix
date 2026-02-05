import os
import shutil
import sys
import uuid
import zipfile
from typing import Tuple, List

APP_NAME = "GoodsRecognitionModel"

def _bundle_dir() -> str:
    if getattr(sys, "frozen", False):
        # 优先检查可执行文件所在目录（onedir模式）
        exe_dir = os.path.dirname(sys.executable)
        if os.path.exists(os.path.join(exe_dir, "frontend")):
            return exe_dir
        # 否则使用 _MEIPASS（onefile模式或资源在 _internal 中）
        return getattr(sys, "_MEIPASS", exe_dir)
    return os.path.dirname(os.path.dirname(__file__))

def _app_data_dir() -> str:
    if sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
        return os.path.join(base, APP_NAME)
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local")
        return os.path.join(base, APP_NAME)
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, APP_NAME)

def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))

BUNDLE_DIR = _bundle_dir()
BASE_DIR = _app_data_dir() if _is_frozen() else os.path.dirname(os.path.dirname(__file__))
DATASETS_DIR = os.path.join(BASE_DIR, "datasets")
MODELS_DIR = os.path.join(BASE_DIR, "models")
RUNS_DIR = os.path.join(BASE_DIR, "runs")
CLASSES_FILE = os.path.join(BASE_DIR, "data", "classes.txt")
DEFAULT_YOLOV8N_FILE = os.path.join(BASE_DIR, "yolov8n.pt")

def ensure_dirs():
    os.makedirs(DATASETS_DIR, exist_ok=True)
    os.makedirs(MODELS_DIR, exist_ok=True)
    os.makedirs(RUNS_DIR, exist_ok=True)
    os.makedirs(os.path.join(BASE_DIR, "data"), exist_ok=True)
    if _is_frozen():
        _copy_bundled_assets()

def _copy_bundled_assets():
    _copy_missing(os.path.join(BUNDLE_DIR, "models"), MODELS_DIR)
    _copy_missing(os.path.join(BUNDLE_DIR, "data"), os.path.join(BASE_DIR, "data"))
    src_model = os.path.join(BUNDLE_DIR, "yolov8n.pt")
    if os.path.isfile(src_model) and not os.path.isfile(DEFAULT_YOLOV8N_FILE):
        os.makedirs(os.path.dirname(DEFAULT_YOLOV8N_FILE), exist_ok=True)
        shutil.copy2(src_model, DEFAULT_YOLOV8N_FILE)

def _copy_missing(src_dir: str, dst_dir: str):
    if not os.path.isdir(src_dir):
        return
    for root, _dirs, files in os.walk(src_dir):
        rel = os.path.relpath(root, src_dir)
        target_root = dst_dir if rel == "." else os.path.join(dst_dir, rel)
        os.makedirs(target_root, exist_ok=True)
        for name in files:
            src = os.path.join(root, name)
            dst = os.path.join(target_root, name)
            if not os.path.exists(dst):
                shutil.copy2(src, dst)

def save_dataset_zip(file_path: str) -> str:
    ensure_dirs()
    dataset_id = str(uuid.uuid4())[:8]
    dest_dir = os.path.join(DATASETS_DIR, dataset_id)
    os.makedirs(dest_dir, exist_ok=True)
    with zipfile.ZipFile(file_path, "r") as z:
        z.extractall(dest_dir)
    return dataset_id

def delete_dataset(dataset_id: str) -> bool:
    target_dir = os.path.join(DATASETS_DIR, dataset_id)
    if os.path.exists(target_dir) and os.path.isdir(target_dir):
        shutil.rmtree(target_dir)
        return True
    return False

def validate_dataset(dataset_id: str) -> Tuple[bool, List[str]]:
    base = os.path.join(DATASETS_DIR, dataset_id)
    problems = []
    has_images = False
    has_labels = False
    for root, dirs, files in os.walk(base):
        if "images" in dirs:
            has_images = True
        if "labels" in dirs:
            has_labels = True
    if not has_images:
        problems.append("missing images/")
    if not has_labels:
        problems.append("missing labels/")
    return (has_images and has_labels, problems)

def read_classes() -> List[str]:
    ensure_dirs()
    if not os.path.exists(CLASSES_FILE):
        return []
    with open(CLASSES_FILE, "r", encoding="utf-8", errors="replace") as f:
        return [x.strip() for x in f.readlines() if x.strip()]

def write_classes(classes: List[str]):
    ensure_dirs()
    with open(CLASSES_FILE, "w", encoding="utf-8") as f:
        for c in classes:
            f.write(f"{c}\n")
