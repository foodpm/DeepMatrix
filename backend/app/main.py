import os
import sys

# Fix for PyTorch DLL loading on Windows (WinError 126/1114)
if os.name == 'nt':
    try:
        import site
        # Find site-packages where torch is installed
        site_packages = site.getsitepackages()
        if not site_packages:
            import sysconfig
            site_packages = [sysconfig.get_path('purelib')]
        
        for sp in site_packages:
            torch_lib = os.path.join(sp, 'torch', 'lib')
            if os.path.exists(torch_lib):
                # Add torch/lib to DLL search path
                os.add_dll_directory(torch_lib)
    except Exception:
        pass

import re
import shutil
import socket
import tempfile
import threading
import time
import uuid
import json
import random
from fastapi import FastAPI, UploadFile, File, Form, WebSocket, WebSocketDisconnect, Request, Body
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import Response
from typing import List, Optional, Dict, Any
from .schemas import InferResponse, Box, TrainConfig, TrainStartResponse, TrainStatusResponse, ClassDict, PublishRequest, DatasetCreateRequest, ImageItem
from .model_manager import ModelManager
from .storage import ensure_dirs, save_dataset_zip, validate_dataset, read_classes, write_classes, delete_dataset, MODELS_DIR, DATASETS_DIR, RUNS_DIR, BUNDLE_DIR, DEFAULT_YOLOV8N_FILE
from .version import __version__
from fastapi.staticfiles import StaticFiles

ensure_dirs()
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def _no_cache_html(request: Request, call_next):
    response: Response = await call_next(request)
    try:
        path = request.url.path
        ct = (response.headers.get("content-type") or "").lower()
        if path == "/" or path.endswith(".html") or ct.startswith("text/html"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
    except Exception:
        pass
    return response


@app.get("/app/version")
def app_version():
    return {"version": __version__}

model_manager = ModelManager(MODELS_DIR)
jobs = {}
jobs_lock = threading.Lock()
current_train_job_id: Optional[str] = None


class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, dataset_id: str, websocket: WebSocket):
        await websocket.accept()
        if dataset_id not in self.active_connections:
            self.active_connections[dataset_id] = []
        self.active_connections[dataset_id].append(websocket)

    def disconnect(self, dataset_id: str, websocket: WebSocket):
        if dataset_id in self.active_connections:
            if websocket in self.active_connections[dataset_id]:
                self.active_connections[dataset_id].remove(websocket)

    async def broadcast(self, dataset_id: str, message: dict):
        if dataset_id in self.active_connections:
            for connection in self.active_connections[dataset_id]:
                try:
                    await connection.send_json(message)
                except Exception:
                    pass

ws_manager = ConnectionManager()


def _safe_mkdir(path: str) -> None:
    os.makedirs(path, exist_ok=True)

def _sanitize_upload_filename(raw: Optional[str]) -> str:
    s = (raw or "").replace("\x00", "")
    s = s.replace("\\", "/")
    s = s.split("/")[-1]
    if re.match(r"^[A-Za-z]:", s):
        s = re.sub(r"^[A-Za-z]:", "", s).lstrip("/").lstrip("\\")
    s = s.strip()
    if not s or s in (".", ".."):
        return uuid.uuid4().hex + ".jpg"
    stem, ext = os.path.splitext(s)
    reserved = {
        "CON", "PRN", "AUX", "NUL",
        "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
        "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
    }
    if stem.upper() in reserved:
        s = f"{uuid.uuid4().hex}_{s}"
    if len(s) > 180:
        ext = ext[:20]
        s = f"{stem[:160]}{ext}"
    return s


def _normalize_model_version(raw: Optional[str]) -> Optional[str]:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    return s


def _model_version_exists(version_id: str) -> bool:
    return os.path.exists(os.path.join(MODELS_DIR, version_id)) or os.path.exists(os.path.join(RUNS_DIR, version_id))


def _generate_model_version_id(dataset_id: str, requested_version: Optional[str]) -> str:
    req = _normalize_model_version(requested_version)
    if req:
        prefix = f"{dataset_id}-"
        if req.startswith(prefix):
            req = req[len(prefix):].strip()
        if not req or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", req):
            raise ValueError("invalid model version (only allow A-Z, a-z, 0-9, _, ., -)")
        version_id = f"{dataset_id}-{req}"
        if _model_version_exists(version_id):
            raise FileExistsError("model version already exists")
        return version_id

    base = f"{dataset_id}-{time.strftime('%Y%m%d-%H%M%S')}"
    version_id = base
    if _model_version_exists(version_id):
        version_id = f"{base}-{uuid.uuid4().hex[:6]}"
    return version_id


def _find_dataset_yaml(dataset_dir: str) -> Optional[str]:
    candidates = []
    for root, _dirs, files in os.walk(dataset_dir):
        for name in files:
            lower = name.lower()
            if lower.endswith(".yaml") or lower.endswith(".yml"):
                candidates.append(os.path.join(root, name))
    if not candidates:
        return None
    candidates.sort(key=lambda p: (0 if os.path.basename(p).lower() in ("data.yaml", "dataset.yaml") else 1, len(p)))
    return candidates[0]


def _ensure_dataset_yaml(dataset_dir: str) -> str:
    existing = _find_dataset_yaml(dataset_dir)
    if existing:
        try:
            yaml_dir = os.path.dirname(existing)
            target_dir = dataset_dir
            for root, dirs, _ in os.walk(dataset_dir):
                if "images" in dirs and os.path.isdir(os.path.join(root, "images")):
                    target_dir = root
                    break

            win_abs = False
            existing_path_val = None
            with open(existing, "r", encoding="utf-8") as f:
                lines = f.readlines()
            for line in lines:
                m = re.match(r"^\s*path\s*:\s*(.+?)\s*$", line)
                if m:
                    existing_path_val = m.group(1).strip().strip('"').strip("'")
                    break

            if isinstance(existing_path_val, str) and existing_path_val:
                if re.match(r"^[A-Za-z]:[\\/]", existing_path_val) or re.match(r"^[A-Za-z]:/", existing_path_val):
                    win_abs = True
                resolved = None
                if os.path.isabs(existing_path_val):
                    resolved = existing_path_val
                else:
                    resolved = os.path.abspath(os.path.join(yaml_dir, existing_path_val))
                if not resolved or not os.path.isdir(resolved):
                    win_abs = True
            else:
                win_abs = True

            if win_abs and os.path.isdir(target_dir):
                new_path = target_dir.replace(os.sep, "/")
                replaced = False
                new_lines = []
                for line in lines:
                    if re.match(r"^\s*path\s*:", line):
                        new_lines.append(f"path: {new_path}\n")
                        replaced = True
                    else:
                        new_lines.append(line)
                if not replaced:
                    new_lines.insert(0, f"path: {new_path}\n")
                if new_lines != lines:
                    with open(existing, "w", encoding="utf-8") as f:
                        f.writelines(new_lines)
        except Exception:
            pass
        return existing

    # Try to find the directory containing 'images'
    target_dir = dataset_dir
    for root, dirs, _ in os.walk(dataset_dir):
        if "images" in dirs and os.path.isdir(os.path.join(root, "images")):
            target_dir = root
            break

    names: List[str] = []
    try:
        classes_json = os.path.join(dataset_dir, "classes.json")
        if os.path.isfile(classes_json):
            with open(classes_json, "r", encoding="utf-8") as f:
                items = json.load(f)
            if isinstance(items, list):
                id_to_name: Dict[int, str] = {}
                max_id = -1
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    try:
                        cid = int(it.get("id"))
                    except Exception:
                        continue
                    if cid < 0:
                        continue
                    max_id = max(max_id, cid)
                    nm = it.get("name")
                    if isinstance(nm, str) and nm:
                        id_to_name[cid] = nm
                if max_id >= 0:
                    names = [id_to_name.get(i, f"class_{i}") for i in range(max_id + 1)]
    except Exception:
        names = []
    if not names:
        names = read_classes()
        if not names:
            names = ["item"]

    data_yaml = os.path.join(target_dir, "data.yaml")
    train_rel = "images/train"
    val_rel = "images/val"
    with open(data_yaml, "w", encoding="utf-8") as f:
        f.write(f"path: {target_dir.replace(os.sep, '/')}\n")
        f.write(f"train: {train_rel}\n")
        f.write(f"val: {val_rel}\n")
        f.write(f"nc: {len(names)}\n")
        f.write("names:\n")
        for n in names:
            f.write(f"  - {n}\n")
    return data_yaml

def _extract_xywh_from_annotation(ann: Any) -> Optional[List[float]]:
    try:
        selector = (ann or {}).get("target", {}).get("selector", {})
        val = selector.get("value", "")
        if selector.get("type") == "FragmentSelector" and isinstance(val, str) and val.startswith("xywh=pixel:"):
            x, y, w, h = map(float, val.replace("xywh=pixel:", "").split(","))
            if w > 0 and h > 0:
                return [x, y, w, h]
            return None
        if selector.get("type") == "SvgSelector" and isinstance(val, str):
            match = re.search(r'points=["\']([\d\s,.]+)["\']', val)
            if match:
                points_str = match.group(1)
                coords = [float(p) for p in re.split(r'[\s,]+', points_str) if p]
                xs = coords[0::2]
                ys = coords[1::2]
                if xs and ys:
                    min_x, max_x = min(xs), max(xs)
                    min_y, max_y = min(ys), max(ys)
                    w = max_x - min_x
                    h = max_y - min_y
                    if w > 0 and h > 0:
                        return [min_x, min_y, w, h]
    except Exception:
        return None
    return None


def _read_simple_yaml_value(yaml_path: str, key: str) -> Optional[str]:
    try:
        with open(yaml_path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if not re.match(rf"^{re.escape(key)}\s*:", line):
                    continue
                _, v = line.split(":", 1)
                v = v.strip()
                if not v:
                    return None
                return v.strip().strip('"').strip("'")
    except Exception:
        return None
    return None


def _yolo_data_split_has_images(data_yaml_path: str, split_key: str) -> bool:
    exts = {
        ".jpg",
        ".jpeg",
        ".png",
        ".bmp",
        ".webp",
        ".tif",
        ".tiff",
        ".dng",
        ".mpo",
        ".heic",
        ".pfm",
    }

    base_dir = None
    path_val = _read_simple_yaml_value(data_yaml_path, "path")
    if path_val:
        base_dir = path_val if os.path.isabs(path_val) else os.path.abspath(os.path.join(os.path.dirname(data_yaml_path), path_val))
    if not base_dir:
        base_dir = os.path.dirname(data_yaml_path)

    split_val = _read_simple_yaml_value(data_yaml_path, split_key)
    if not split_val:
        return False

    split_dir = split_val if os.path.isabs(split_val) else os.path.abspath(os.path.join(base_dir, split_val))
    if not os.path.isdir(split_dir):
        return False

    try:
        for root, _dirs, files in os.walk(split_dir):
            for name in files:
                if os.path.splitext(name)[1].lower() in exts:
                    return True
    except Exception:
        return False
    return False


def _get_image_exif_orientation_and_size(image_path: str) -> tuple[Optional[int], Optional[int], Optional[int]]:
    try:
        from PIL import Image, ExifTags
        with Image.open(image_path) as im:
            w, h = im.size
            exif = None
            try:
                exif = im.getexif()
            except Exception:
                exif = None
            orientation_tag = 274
            try:
                for k, v in ExifTags.TAGS.items():
                    if v == "Orientation":
                        orientation_tag = k
                        break
            except Exception:
                pass
            ori = None
            if exif is not None:
                try:
                    ori = exif.get(orientation_tag)
                except Exception:
                    ori = None
        try:
            ori_int = int(ori) if ori is not None else None
        except Exception:
            ori_int = None
        return ori_int, int(w), int(h)
    except Exception:
        return None, None, None


def _normalize_xywh_by_exif(
    xywh: List[float],
    orientation: Optional[int],
    raw_w: Optional[int],
    raw_h: Optional[int],
) -> List[float]:
    try:
        if not xywh or len(xywh) != 4:
            return xywh
        if not orientation or orientation == 1:
            return xywh
        if not raw_w or not raw_h or raw_w <= 0 or raw_h <= 0:
            return xywh
        x, y, w, h = map(float, xywh)

        if orientation == 2:
            return [float(raw_w) - (x + w), y, w, h]
        if orientation == 3:
            return [float(raw_w) - (x + w), float(raw_h) - (y + h), w, h]
        if orientation == 4:
            return [x, float(raw_h) - (y + h), w, h]
        if orientation == 5:
            return [y, x, h, w]
        if orientation == 6:
            return [float(raw_h) - (y + h), x, h, w]
        if orientation == 7:
            return [float(raw_h) - (y + h), float(raw_w) - (x + w), h, w]
        if orientation == 8:
            return [y, float(raw_w) - (x + w), h, w]
    except Exception:
        return xywh
    return xywh

def _get_image_size_px(image_path: str) -> Optional[tuple[int, int]]:
    try:
        from PIL import Image, ImageOps
        with Image.open(image_path) as im:
            try:
                im = ImageOps.exif_transpose(im)
            except Exception:
                pass
            w, h = im.size
        if int(w) > 0 and int(h) > 0:
            return int(w), int(h)
    except Exception:
        return None
    return None

def _get_class_names() -> List[str]:
    names = read_classes()
    if not names:
        return ["item"]
    return names

def _dataset_dir_path(dataset_id: str) -> str:
    return os.path.join(DATASETS_DIR, dataset_id)

def _dataset_classes_json_path(dataset_dir: str) -> str:
    return os.path.join(dataset_dir, "classes.json")

def _atomic_write_json(path: str, data: Any) -> None:
    base_dir = os.path.dirname(path)
    _safe_mkdir(base_dir)
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=base_dir)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        tmp = None
    finally:
        try:
            if tmp and os.path.isfile(tmp):
                os.remove(tmp)
        except Exception:
            pass

def _load_dataset_classes_items(dataset_id: str) -> List[Dict[str, Any]]:
    dataset_dir = _dataset_dir_path(dataset_id)
    path = _dataset_classes_json_path(dataset_dir)
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                out: List[Dict[str, Any]] = []
                for it in data:
                    if isinstance(it, dict):
                        out.append(it)
                return out
        except Exception:
            pass

    names: List[str] = []
    try:
        data_yaml = _ensure_dataset_yaml(dataset_dir)
        with open(data_yaml, "r", encoding="utf-8") as f:
            import yaml
            y = yaml.safe_load(f) or {}
        n = y.get("names", [])
        if isinstance(n, dict):
            max_key = max(n.keys()) if n else -1
            names = [str(n.get(i, f"class_{i}")) for i in range(max_key + 1)]
        elif isinstance(n, list):
            names = [str(x) for x in n]
    except Exception:
        names = []

    if not names:
        names = read_classes() or ["item"]

    now = time.time()
    items = [
        {"id": i, "name": names[i] if i < len(names) else f"class_{i}", "deleted": False, "created_at": now, "updated_at": now}
        for i in range(len(names))
    ]
    try:
        _atomic_write_json(path, items)
    except Exception:
        pass
    return items

def _dataset_class_names(dataset_id: str) -> List[str]:
    items = _load_dataset_classes_items(dataset_id)
    max_id = -1
    id_to_name: Dict[int, str] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            cid = int(it.get("id"))
        except Exception:
            continue
        if cid < 0:
            continue
        max_id = max(max_id, cid)
        nm = it.get("name")
        if isinstance(nm, str) and nm:
            id_to_name[cid] = nm
    if max_id < 0:
        return ["item"]
    return [id_to_name.get(i, f"class_{i}") for i in range(max_id + 1)]

def _update_dataset_yaml_names(dataset_id: str, names: List[str]) -> None:
    dataset_dir = _dataset_dir_path(dataset_id)
    data_yaml = _ensure_dataset_yaml(dataset_dir)
    try:
        with open(data_yaml, "r", encoding="utf-8") as f:
            import yaml
            data = yaml.safe_load(f) or {}
        data["nc"] = int(len(names))
        data["names"] = list(names)
        with open(data_yaml, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, sort_keys=False)
    except Exception:
        pass

def _sync_dataset_labels_to_active_classes(dataset_id: str) -> None:
    dataset_dir = _dataset_dir_path(dataset_id)
    if not os.path.isdir(dataset_dir):
        return
    items = _load_dataset_classes_items(dataset_id)
    active_items: List[Dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        if it.get("deleted") is True:
            continue
        nm = it.get("name")
        if isinstance(nm, str) and nm.strip():
            active_items.append(it)
    desired_names = [str(it.get("name")).strip() for it in sorted(active_items, key=lambda x: int(x.get("id", 0)) if str(x.get("id", "")).lstrip("-").isdigit() else 10**9)]
    if not desired_names:
        return
    name_to_new_id = {n: i for i, n in enumerate(desired_names)}

    old_to_new: Dict[int, int] = {}
    for subset in ("train", "val"):
        jd = os.path.join(dataset_dir, "labels_json", subset)
        if not os.path.isdir(jd):
            continue
        for fn in os.listdir(jd):
            if not fn.endswith(".json"):
                continue
            p = os.path.join(jd, fn)
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue
            if not isinstance(data, list):
                continue
            for ann in data:
                if not isinstance(ann, dict):
                    continue
                body = ann.get("body")
                if not isinstance(body, list):
                    continue
                tag = None
                cid = None
                for b in body:
                    if not isinstance(b, dict):
                        continue
                    if b.get("purpose") == "tagging" and isinstance(b.get("value"), str) and b.get("value").strip():
                        tag = b.get("value").strip()
                    if b.get("purpose") == "class_id":
                        try:
                            cid = int(float(b.get("value")))
                        except Exception:
                            cid = None
                if tag is None or cid is None:
                    continue
                if tag not in name_to_new_id:
                    continue
                old_to_new[cid] = int(name_to_new_id[tag])

    if not old_to_new:
        return

    for subset in ("train", "val"):
        ld = os.path.join(dataset_dir, "labels", subset)
        if os.path.isdir(ld):
            for fn in os.listdir(ld):
                if not fn.endswith(".txt"):
                    continue
                p = os.path.join(ld, fn)
                try:
                    with open(p, "r", encoding="utf-8", errors="ignore") as f:
                        lines = f.read().splitlines()
                except Exception:
                    continue
                out_lines: List[str] = []
                changed = False
                for line in lines:
                    s = (line or "").strip()
                    if not s:
                        continue
                    parts = s.split()
                    if not parts:
                        continue
                    try:
                        old_id = int(float(parts[0]))
                    except Exception:
                        out_lines.append(s)
                        continue
                    if old_id in old_to_new:
                        new_id = old_to_new[old_id]
                        if new_id != old_id:
                            changed = True
                        parts[0] = str(int(new_id))
                        out_lines.append(" ".join(parts))
                    else:
                        out_lines.append(s)
                if changed:
                    try:
                        with open(p, "w", encoding="utf-8") as f:
                            f.write("\n".join(out_lines) + ("\n" if out_lines else ""))
                    except Exception:
                        pass

        jd = os.path.join(dataset_dir, "labels_json", subset)
        if os.path.isdir(jd):
            for fn in os.listdir(jd):
                if not fn.endswith(".json"):
                    continue
                p = os.path.join(jd, fn)
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception:
                    continue
                if not isinstance(data, list):
                    continue
                changed = False
                for ann in data:
                    if not isinstance(ann, dict):
                        continue
                    body = ann.get("body")
                    if not isinstance(body, list):
                        continue
                    tag = None
                    old_cid = None
                    cid_body = None
                    for b in body:
                        if not isinstance(b, dict):
                            continue
                        if b.get("purpose") == "tagging" and isinstance(b.get("value"), str) and b.get("value").strip():
                            tag = b.get("value").strip()
                        if b.get("purpose") == "class_id":
                            cid_body = b
                            try:
                                old_cid = int(float(b.get("value")))
                            except Exception:
                                old_cid = None
                    if tag is None:
                        continue
                    new_cid = name_to_new_id.get(tag)
                    if new_cid is None:
                        continue
                    if cid_body is None:
                        body.append({"type": "TextualBody", "purpose": "class_id", "value": str(int(new_cid))})
                        changed = True
                    else:
                        if old_cid is None or old_cid != int(new_cid):
                            cid_body["value"] = str(int(new_cid))
                            changed = True
                if changed:
                    try:
                        with open(p, "w", encoding="utf-8") as f:
                            json.dump(data, f, ensure_ascii=False)
                    except Exception:
                        pass

    now = time.time()
    name_meta: Dict[str, Dict[str, Any]] = {}
    for it in active_items:
        try:
            n = str(it.get("name")).strip()
        except Exception:
            continue
        if not n:
            continue
        created_at = it.get("created_at", now)
        updated_at = it.get("updated_at", now)
        prev = name_meta.get(n)
        if not prev:
            name_meta[n] = {"created_at": created_at, "updated_at": updated_at}
        else:
            try:
                prev["created_at"] = min(float(prev.get("created_at", now)), float(created_at))
            except Exception:
                pass
            try:
                prev["updated_at"] = max(float(prev.get("updated_at", now)), float(updated_at))
            except Exception:
                pass

    compact_items: List[Dict[str, Any]] = []
    for i, n in enumerate(desired_names):
        meta = name_meta.get(n) or {}
        compact_items.append(
            {
                "id": int(i),
                "name": n,
                "deleted": False,
                "created_at": meta.get("created_at", now),
                "updated_at": meta.get("updated_at", now),
            }
        )
    try:
        _atomic_write_json(_dataset_classes_json_path(dataset_dir), compact_items)
    except Exception:
        pass
    _update_dataset_yaml_names(dataset_id, desired_names)
    _clean_cache_files(dataset_dir)

def _read_map_series_from_results_csv(csv_path: str) -> Dict[str, List[Optional[float]]]:
    if not csv_path or not os.path.isfile(csv_path):
        return {"epochs": [], "map50": [], "map5095": []}
    try:
        with open(csv_path, "r", encoding="utf-8-sig", errors="replace") as f:
            text = f.read()
    except Exception:
        return {"epochs": [], "map50": [], "map5095": []}
    raw = (text or "").strip()
    if not raw:
        return {"epochs": [], "map50": [], "map5095": []}
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    if len(lines) < 2:
        return {"epochs": [], "map50": [], "map5095": []}
    header = [h.strip().lower() for h in lines[0].split(",")]
    idx_epoch = -1
    idx_map50 = -1
    idx_map = -1
    for i, h in enumerate(header):
        if h == "epoch":
            idx_epoch = i
        if h in ("metrics/map50(b)", "metrics/map50"):
            idx_map50 = i
        if h in ("metrics/map50-95(b)", "metrics/map50-95", "metrics/map"):
            idx_map = i
    if idx_epoch < 0:
        return {"epochs": [], "map50": [], "map5095": []}

    epochs: List[int] = []
    map50: List[Optional[float]] = []
    map5095: List[Optional[float]] = []
    for li in range(1, len(lines)):
        cols = [c.strip() for c in lines[li].split(",")]
        if idx_epoch >= len(cols):
            continue
        try:
            e = int(float(cols[idx_epoch]))
        except Exception:
            continue
        v50: Optional[float] = None
        v5095: Optional[float] = None
        if 0 <= idx_map50 < len(cols):
            try:
                v50 = float(cols[idx_map50])
            except Exception:
                v50 = None
        if 0 <= idx_map < len(cols):
            try:
                v5095 = float(cols[idx_map])
            except Exception:
                v5095 = None
        if v50 is None and v5095 is None:
            continue
        epochs.append(e)
        map50.append(v50)
        map5095.append(v5095)
    return {"epochs": epochs, "map50": map50, "map5095": map5095}

def _class_name_from_id(class_id: int, names: List[str]) -> str:
    try:
        if 0 <= int(class_id) < len(names):
            return names[int(class_id)]
    except Exception:
        pass
    return f"class_{class_id}"

def _extract_class_id_from_annotation(ann: Any, names: List[str]) -> Optional[int]:
    try:
        body = (ann or {}).get("body", [])
        if not isinstance(body, list):
            return None
        for b in body:
            if not isinstance(b, dict):
                continue
            if b.get("purpose") == "class_id":
                v = b.get("value")
                if v is None:
                    continue
                cid = int(float(v))
                if cid >= 0:
                    return cid
        for b in body:
            if not isinstance(b, dict):
                continue
            if b.get("purpose") == "tagging" and isinstance(b.get("value"), str) and b.get("value"):
                v = b.get("value").strip()
                if v in names:
                    return names.index(v)
    except Exception:
        return None
    return None

def _ensure_annotation_tag(ann: Any, class_id: int, class_name: str) -> Any:
    try:
        if not isinstance(ann, dict):
            return ann
        body = ann.get("body", [])
        if not isinstance(body, list):
            body = []
            ann["body"] = body
        has_tag = False
        has_cid = False
        for b in body:
            if not isinstance(b, dict):
                continue
            if b.get("purpose") == "tagging" and isinstance(b.get("value"), str) and b.get("value"):
                has_tag = True
            if b.get("purpose") == "class_id":
                has_cid = True
        if not has_tag and class_name:
            body.append({"type": "TextualBody", "purpose": "tagging", "value": class_name})
        if not has_cid:
            body.append({"type": "TextualBody", "purpose": "class_id", "value": str(int(class_id))})
    except Exception:
        return ann
    return ann

def _extract_xywh_and_cid_from_yolo_txt(label_path: str, image_path: str) -> List[tuple[int, List[float]]]:
    size = _get_image_size_px(image_path)
    if not size:
        return []
    img_w, img_h = size
    out: List[tuple[int, List[float]]] = []
    try:
        with open(label_path, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) < 5:
                    continue
                try:
                    cid = int(float(parts[0]))
                    cx = float(parts[1])
                    cy = float(parts[2])
                    w = float(parts[3])
                    h = float(parts[4])
                except Exception:
                    continue
                if w <= 0 or h <= 0:
                    continue
                x_px = (cx - w / 2.0) * float(img_w)
                y_px = (cy - h / 2.0) * float(img_h)
                w_px = w * float(img_w)
                h_px = h * float(img_h)
                if w_px <= 0 or h_px <= 0:
                    continue
                out.append((cid, [x_px, y_px, w_px, h_px]))
    except Exception:
        return []
    return out

def _extract_xywh_from_yolo_txt(label_path: str, image_path: str) -> List[List[float]]:
    pairs = _extract_xywh_and_cid_from_yolo_txt(label_path, image_path)
    return [xywh for _cid, xywh in pairs]

def _build_fragment_annotation(
    dataset_id: str,
    subset: str,
    name: str,
    xywh: List[float],
    ann_id: str,
    class_id: Optional[int] = None,
    class_name: Optional[str] = None,
) -> Dict[str, Any]:
    x, y, w, h = xywh
    url = f"/datasets/{dataset_id}/image/{subset}/{name}"
    body: List[Dict[str, Any]] = []
    if class_name:
        body.append({"type": "TextualBody", "purpose": "tagging", "value": class_name})
    if class_id is not None:
        body.append({"type": "TextualBody", "purpose": "class_id", "value": str(int(class_id))})
    return {
        "@context": "http://www.w3.org/ns/anno.jsonld",
        "id": ann_id,
        "_exif_normalized": True,
        "type": "Annotation",
        "body": body,
        "target": {
            "source": url,
            "selector": {
                "type": "FragmentSelector",
                "conformsTo": "http://www.w3.org/TR/media-frags/",
                "value": f"xywh=pixel:{x},{y},{w},{h}",
            },
        },
    }


def _find_weight_pt(version_id: str, weight_name: str) -> Optional[str]:
    candidates = []
    if not os.path.isdir(RUNS_DIR):
        return None
    target = str(weight_name).lower()
    for root, _dirs, files in os.walk(RUNS_DIR):
        if version_id not in root:
            continue
        for name in files:
            if name.lower() != target:
                continue
            p = os.path.join(root, name)
            try:
                candidates.append((os.path.getmtime(p), p))
            except Exception:
                candidates.append((0.0, p))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def _find_best_pt(version_id: str) -> Optional[str]:
    return _find_weight_pt(version_id, "best.pt")


def _canonical_run_dir(version_id: str) -> str:
    return os.path.join(RUNS_DIR, version_id)


def _safe_prepare_run_dir(version_id: str) -> str:
    import shutil

    run_dir = _canonical_run_dir(version_id)
    try:
        if os.path.islink(run_dir) or os.path.isfile(run_dir):
            os.remove(run_dir)
        elif os.path.isdir(run_dir):
            shutil.rmtree(run_dir, ignore_errors=True)
    except Exception:
        pass
    return run_dir


def _find_latest_run_dir(version_id: str, require_file: Optional[str] = None) -> Optional[str]:
    if not os.path.isdir(RUNS_DIR):
        return None
    pat = re.compile(rf"^{re.escape(version_id)}(\d+)?$")
    candidates: List[Tuple[float, str]] = []
    try:
        for name in os.listdir(RUNS_DIR):
            if not pat.fullmatch(name):
                continue
            p = os.path.join(RUNS_DIR, name)
            if not os.path.isdir(p):
                continue
            if require_file:
                fp = os.path.join(p, require_file)
                if not os.path.isfile(fp):
                    continue
                try:
                    candidates.append((os.path.getmtime(fp), p))
                except Exception:
                    candidates.append((0.0, p))
            else:
                try:
                    candidates.append((os.path.getmtime(p), p))
                except Exception:
                    candidates.append((0.0, p))
    except Exception:
        return None
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def _sync_latest_run_artifacts_to_canonical(version_id: str) -> None:
    import shutil

    canonical_dir = _canonical_run_dir(version_id)
    latest_dir = _find_latest_run_dir(version_id, require_file="results.csv")
    if not latest_dir:
        return
    try:
        if os.path.abspath(latest_dir) == os.path.abspath(canonical_dir):
            return
    except Exception:
        pass
    _safe_mkdir(canonical_dir)
    for fname in (
        "results.csv",
        "results.png",
        "confusion_matrix.png",
        "val_batch0_labels.jpg",
        "val_batch0_pred.jpg",
    ):
        src = os.path.join(latest_dir, fname)
        if os.path.isfile(src):
            try:
                shutil.copy2(src, os.path.join(canonical_dir, fname))
            except Exception:
                pass


def _clean_cache_files(dataset_dir: str) -> None:
    for root, dirs, files in os.walk(dataset_dir):
        for file in files:
            if file.endswith(".cache"):
                try:
                    os.remove(os.path.join(root, file))
                except Exception:
                    pass


def _auto_update_classes(dataset_dir: str, data_yaml_path: str) -> None:
    try:
        # 1. Read existing config
        with open(data_yaml_path, "r", encoding="utf-8") as f:
            import yaml
            data = yaml.safe_load(f)
            current_names = data.get("names", [])
            if isinstance(current_names, dict):
                # Handle dictionary format names: {0: 'person', 1: 'car'}
                max_key = max(current_names.keys()) if current_names else -1
                temp_names = []
                for i in range(max_key + 1):
                    temp_names.append(current_names.get(i, f"class_{i}"))
                current_names = temp_names
            
            # Ensure current_names is a list
            if not isinstance(current_names, list):
                current_names = []

        train_path = data.get("train")
        if not train_path:
            return

        # Handle relative path in yaml
        if not os.path.isabs(train_path):
            yaml_dir = os.path.dirname(data_yaml_path)
            train_dir = os.path.join(yaml_dir, train_path)
        else:
            train_dir = train_path

        # Infer labels dir
        if "images" in train_dir:
            labels_dir = train_dir.replace("images", "labels")
        else:
            labels_dir = os.path.join(dataset_dir, "labels")

        if not os.path.isdir(labels_dir):
            return

        # 2. Scan for max class ID
        max_id = -1
        # Optimize: if dataset is huge, this might take time. 
        # But for correctness, we must scan.
        for root, _, files in os.walk(labels_dir):
            for file in files:
                if not file.endswith(".txt"):
                    continue
                path = os.path.join(root, file)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        for line in f:
                            parts = line.strip().split()
                            if parts:
                                cid = int(float(parts[0]))
                                if cid > max_id:
                                    max_id = cid
                except Exception:
                    pass
        
        # 3. Update config if needed
        current_nc = len(current_names)
        if max_id >= current_nc:
            # Expand names
            needed = max_id + 1
            new_names = current_names[:]
            for i in range(current_nc, needed):
                new_names.append(f"class_{i}")
            
            # Write back to yaml
            with open(data_yaml_path, "w", encoding="utf-8") as f:
                # Preserve other fields
                data["nc"] = len(new_names)
                data["names"] = new_names
                yaml.dump(data, f, allow_unicode=True, sort_keys=False)
            
            print(f"Updated dataset config: extended classes from {current_nc} to {len(new_names)}")

    except Exception as e:
        print(f"Auto-update classes failed: {e}")
        # Don't block training, let YOLO fail if it must
        pass


def _run_training(job_id: str, cfg: TrainConfig, model_version_id: str) -> None:
    try:
        dataset_dir = os.path.join(DATASETS_DIR, cfg.dataset_id)
        # ... (keep existing setup code) ...
        with jobs_lock:
            j = jobs.get(job_id) or {}
            j["status"] = "running"
            j["started_at"] = j.get("started_at") or time.time()
            j["stop_requested"] = False
            j["metrics"] = j.get("metrics") or {}
            j["metrics"]["device"] = cfg.device
            j["metrics"]["epochs_total"] = int(cfg.epochs)
            j["metrics"]["epochs_done"] = 0
            j["metrics"]["progress"] = 0.0
            j["metrics"]["eta_seconds"] = None
            j["metrics"]["elapsed_seconds"] = 0.0
            j["metrics"]["est_epoch_seconds"] = None
            j["metrics"]["model_version_id"] = model_version_id
            jobs[job_id] = j

        try:
            from ultralytics import YOLO
        except Exception as e:
            # Handle import error gracefully
            with jobs_lock:
                j = jobs.get(job_id) or {}
                j["status"] = "failed"
                m = j.get("metrics") or {}
                m["error"] = f"Training is not supported in this environment: {e}"
                j["metrics"] = m
                jobs[job_id] = j
            return
            # raise ImportError(f"ultralytics import failed: {e}")

        if not os.path.isdir(dataset_dir):
            raise FileNotFoundError("dataset not found")

        _sync_dataset_labels_to_active_classes(cfg.dataset_id)
        _clean_cache_files(dataset_dir)
        data_yaml = _ensure_dataset_yaml(dataset_dir)
        _auto_update_classes(dataset_dir, data_yaml)
        base_model = DEFAULT_YOLOV8N_FILE if os.path.isfile(DEFAULT_YOLOV8N_FILE) else "yolov8n.pt"

        started_at = time.time()
        last_epoch_done = 0

        def _update_epoch(trainer) -> None:
            nonlocal last_epoch_done
            try:
                epoch = int(getattr(trainer, "epoch", 0)) + 1
            except Exception:
                epoch = last_epoch_done
            last_epoch_done = max(last_epoch_done, epoch)

            now = time.time()
            elapsed = max(0.0, now - started_at)
            with jobs_lock:
                j = jobs.get(job_id) or {}
                m = j.get("metrics") or {}
                epochs_total = int(m.get("epochs_total") or 0)
                m["epochs_done"] = min(epochs_total, last_epoch_done) if epochs_total > 0 else last_epoch_done
                if epochs_total > 0:
                    m["progress"] = float(m["epochs_done"]) / float(epochs_total)
                else:
                    m["progress"] = 0.0
                m["elapsed_seconds"] = elapsed
                if m["epochs_done"] > 0 and epochs_total > 0:
                    est = elapsed / float(m["epochs_done"])
                    m["est_epoch_seconds"] = est
                    m["eta_seconds"] = max(0.0, (epochs_total - m["epochs_done"]) * est)
                j["metrics"] = m
                if j.get("stop_requested"):
                    try:
                        setattr(trainer, "stop", True)
                    except Exception:
                        pass
                jobs[job_id] = j

        model = YOLO(base_model)
        try:
            model.add_callback("on_train_epoch_end", _update_epoch)
        except Exception:
            pass

        import torch
        device_str = str(cfg.device)
        if device_str == "0" and not torch.cuda.is_available():
            print("WARNING: GPU requested but CUDA is not available. Falling back to CPU.")
            device_str = "cpu"

        _safe_prepare_run_dir(model_version_id)
        has_val_images = _yolo_data_split_has_images(data_yaml, "val")
        if not has_val_images:
            with jobs_lock:
                j = jobs.get(job_id) or {}
                m = j.get("metrics") or {}
                m["validation_skipped"] = True
                j["metrics"] = m
                jobs[job_id] = j
            print(f"WARNING: dataset val split has no images, skipping validation. data={data_yaml}")
        model.train(
            data=data_yaml,
            epochs=int(cfg.epochs),
            batch=int(cfg.batch),
            imgsz=int(cfg.imgsz),
            device=device_str,
            project=RUNS_DIR,
            name=model_version_id,
            exist_ok=True,
            verbose=False,
            workers=0,
            plots=True,
            val=bool(has_val_images),
        )

        _sync_latest_run_artifacts_to_canonical(model_version_id)
        chosen_weight_name = "best.pt"
        chosen_weight_path = _find_weight_pt(model_version_id, "best.pt")
        if not chosen_weight_path or not os.path.isfile(chosen_weight_path):
            chosen_weight_name = "last.pt"
            chosen_weight_path = _find_weight_pt(model_version_id, "last.pt")
        if not chosen_weight_path or not os.path.isfile(chosen_weight_path):
            raise FileNotFoundError("best.pt/last.pt not found after training")

        target_dir = os.path.join(MODELS_DIR, model_version_id)
        _safe_mkdir(target_dir)
        target_pt = os.path.join(target_dir, "best.pt")
        import shutil

        shutil.copy2(chosen_weight_path, target_pt)
        meta_path = os.path.join(target_dir, "meta.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "model_version_id": model_version_id,
                    "dataset_id": cfg.dataset_id,
                    "created_at": int(time.time()),
                    "train_config": cfg.model_dump(),
                    "weights_source": chosen_weight_name,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )

        with jobs_lock:
            j = jobs.get(job_id) or {}
            if j.get("stop_requested"):
                j["status"] = "stopped"
            else:
                j["status"] = "completed"
            m = j.get("metrics") or {}
            m["progress"] = 1.0
            m["epochs_done"] = int(m.get("epochs_total") or m.get("epochs_done") or 0)
            m["eta_seconds"] = 0.0
            j["metrics"] = m
            jobs[job_id] = j

    except Exception as e:
        import traceback
        traceback.print_exc()
        with jobs_lock:
            j = jobs.get(job_id) or {}
            if j.get("stop_requested"):
                j["status"] = "stopped"
            else:
                j["status"] = "failed"
            m = j.get("metrics") or {}
            m["error"] = f"{type(e).__name__}: {e}"
            m["traceback"] = traceback.format_exc()
            j["metrics"] = m
            jobs[job_id] = j

@app.get("/infer/health")
def health():
    return {"status": "ok"}

@app.post("/infer/image", response_model=InferResponse)
async def infer_image(file: UploadFile = File(...), conf: float = Form(0.25), iou: float = Form(0.45), max_det: int = Form(100)):
    data = await file.read()
    res, model_version_id, model_source = model_manager.infer_image(data, conf=conf, iou=iou, max_det=max_det)
    return InferResponse(
        boxes=[Box(**b) for b in res.boxes],
        model_version_id=model_version_id,
        model_source=model_source,
    )

@app.post("/infer/video")
async def infer_video(file: UploadFile = File(...)):
    data = await file.read()
    job_id = "video-" + file.filename
    jobs[job_id] = {"status": "queued", "metrics": {}}
    return {"job_id": job_id, "status": jobs[job_id]["status"]}

@app.post("/datasets/upload")
async def upload_dataset(file: UploadFile = File(...)):
    original_name = os.path.basename(file.filename or "")
    _, ext = os.path.splitext(original_name)
    if not ext:
        ext = ".zip"
    tmp = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4().hex}{ext}")
    with open(tmp, "wb") as f:
        f.write(await file.read())
    dataset_id = save_dataset_zip(tmp)
    ok, problems = validate_dataset(dataset_id)
    return {"dataset_id": dataset_id, "valid": ok, "problems": problems}

@app.post("/datasets/create")
def create_dataset(payload: DatasetCreateRequest):
    dataset_id = payload.dataset_id
    if not dataset_id or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", dataset_id):
        return JSONResponse(status_code=400, content={"error": "invalid dataset id (only allow A-Z, a-z, 0-9, _, -)"})
    
    dataset_dir = os.path.join(DATASETS_DIR, dataset_id)
    if os.path.exists(dataset_dir):
        return JSONResponse(status_code=400, content={"error": "dataset already exists"})
        
    try:
        # Handle custom path (Junction)
        if payload.path:
            target_path = os.path.abspath(payload.path)
            if not os.path.exists(target_path):
                os.makedirs(target_path, exist_ok=True)
            
            # Create Junction
            import subprocess
            subprocess.run(f'mklink /J "{dataset_dir}" "{target_path}"', shell=True, check=True)
        else:
            os.makedirs(dataset_dir, exist_ok=True)

        # Create standard structure
        _safe_mkdir(os.path.join(dataset_dir, "images", "train"))
        _safe_mkdir(os.path.join(dataset_dir, "images", "val"))
        _safe_mkdir(os.path.join(dataset_dir, "labels", "train"))
        _safe_mkdir(os.path.join(dataset_dir, "labels", "val"))
        
        # Save info.json with val_split
        info_path = os.path.join(dataset_dir, "info.json")
        with open(info_path, "w", encoding="utf-8") as f:
            json.dump({"val_split": payload.val_split}, f)

        # Create initial data.yaml
        _ensure_dataset_yaml(dataset_dir)
        
        return {"dataset_id": dataset_id, "status": "created"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/datasets/{dataset_id}/import")
async def import_dataset_images(dataset_id: str, request: Request):
    # Manual form parsing to debug 400 errors
    try:
        form = await request.form()
        files = form.getlist("files")
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": f"Form parse error: {str(e)}"})

    if not files:
        return JSONResponse(status_code=400, content={"error": "No files received"})

    dataset_dir = os.path.join(DATASETS_DIR, dataset_id)
    if not os.path.exists(dataset_dir):
        return JSONResponse(status_code=404, content={"error": "dataset not found"})

    # Read val_split
    val_split = 0.2
    info_path = os.path.join(dataset_dir, "info.json")
    if os.path.exists(info_path):
        try:
            with open(info_path, "r", encoding="utf-8") as f:
                info = json.load(f)
                val_split = info.get("val_split", 0.2)
        except Exception:
            pass

    count = 0
    for file in files:
        split = "val" if random.random() < val_split else "train"
        images_dir = os.path.join(dataset_dir, "images", split)
        _safe_mkdir(images_dir)
        
        # Keep original filename if possible, but avoid conflicts?
        # User wants to see filenames. 
        # But duplication is an issue.
        # I'll prefix UUID if exists, or just overwrite?
        # Standard: overwrite or skip.
        # Let's just use filename.
        raw_filename = getattr(file, "filename", None)
        filename = _sanitize_upload_filename(raw_filename)
        path = os.path.join(images_dir, filename)
        if os.path.exists(path):
            filename = f"{uuid.uuid4().hex}_{filename}"
            path = os.path.join(images_dir, filename)
        try:
            content = await file.read()
            with open(path, "wb") as f:
                f.write(content)
        except Exception as e:
            return JSONResponse(
                status_code=500,
                content={
                    "error": "failed to import image",
                    "filename": raw_filename,
                    "reason": str(e),
                },
            )
        count += 1
        
        # Broadcast update
        try:
            msg = {
                "type": "update",
                "image": filename,
                "subset": split,
                "labeled": False
            }
            await ws_manager.broadcast(dataset_id, msg)
        except Exception:
            pass
        
    return {"imported": count}


@app.get("/datasets/{dataset_id}/images", response_model=List[ImageItem])
def list_dataset_images(dataset_id: str):
    dataset_dir = os.path.join(DATASETS_DIR, dataset_id)
    if not os.path.exists(dataset_dir):
        return []

    items_with_sort_key = []
    for split in ["train", "val"]:
        img_dir = os.path.join(dataset_dir, "images", split)
        lbl_dir = os.path.join(dataset_dir, "labels", split)
        if not os.path.isdir(img_dir):
            continue
            
        for name in os.listdir(img_dir):
            lower = name.lower()
            if lower.endswith((".jpg", ".jpeg", ".png", ".bmp", ".webp")):
                # Check label
                base_name = os.path.splitext(name)[0]
                label_path = os.path.join(lbl_dir, base_name + ".txt")
                img_path = os.path.join(img_dir, name)
                json_path = os.path.join(dataset_dir, "labels_json", split, base_name + ".json")

                mtime = 0.0
                try:
                    mtime = float(os.path.getmtime(img_path))
                except Exception:
                    mtime = 0.0

                ori, raw_w, raw_h = _get_image_exif_orientation_and_size(img_path)
                img_w = raw_w
                img_h = raw_h
                if ori in (5, 6, 7, 8) and raw_w and raw_h:
                    img_w, img_h = raw_h, raw_w

                boxes: Optional[List[List[float]]] = None
                if os.path.isfile(json_path):
                    try:
                        with open(json_path, "r", encoding="utf-8") as f:
                            ann_list = json.load(f)
                        if isinstance(ann_list, list):
                            parsed: List[List[float]] = []
                            for ann in ann_list:
                                xywh = _extract_xywh_from_annotation(ann)
                                if (
                                    xywh is not None
                                    and isinstance(ann, dict)
                                    and not ann.get("_exif_normalized")
                                    and ori
                                    and raw_w
                                    and raw_h
                                ):
                                    xywh = _normalize_xywh_by_exif(xywh, ori, raw_w, raw_h)
                                if xywh is not None:
                                    parsed.append(xywh)
                            if parsed:
                                boxes = parsed
                    except Exception:
                        boxes = None

                has_txt = os.path.isfile(label_path) and os.path.getsize(label_path) > 0
                if boxes is None and has_txt:
                    parsed_txt = _extract_xywh_from_yolo_txt(label_path, img_path)
                    if parsed_txt:
                        boxes = parsed_txt

                labeled = bool(boxes) or has_txt
                
                # Construct URL
                # We need a way to serve these images.
                # Currently frontend mounts root.
                # We can add a route to serve dataset images.
                # Or just use static mount if we mount DATASETS_DIR?
                # Security risk? 
                # Better: /datasets/{id}/image/{subset}/{name} endpoint.
                url = f"/datasets/{dataset_id}/image/{split}/{name}"
                
                items_with_sort_key.append((mtime, name, ImageItem(
                    name=name,
                    subset=split,
                    labeled=labeled,
                    url=url,
                    w=img_w,
                    h=img_h,
                    boxes=boxes
                )))
    
    items_with_sort_key.sort(key=lambda x: (x[0], x[1]))
    return [it for _mtime, _name, it in items_with_sort_key]


@app.get("/datasets/{dataset_id}/image/{subset}/{name}")
def get_dataset_image(dataset_id: str, subset: str, name: str):
    from fastapi.responses import FileResponse
    dataset_dir = os.path.join(DATASETS_DIR, dataset_id)
    path = os.path.join(dataset_dir, "images", subset, name)
    if os.path.isfile(path):
        ext = os.path.splitext(name)[1].lower()
        if ext in (".jpg", ".jpeg"):
            try:
                from PIL import Image, ImageOps
                norm_dir = os.path.join(dataset_dir, "_normalized_images", subset)
                _safe_mkdir(norm_dir)
                base = os.path.splitext(name)[0]
                norm_path = os.path.join(norm_dir, base + ".jpg")
                try:
                    if os.path.isfile(norm_path) and os.path.getmtime(norm_path) >= os.path.getmtime(path):
                        return FileResponse(norm_path, media_type="image/jpeg")
                except Exception:
                    pass
                with Image.open(path) as im:
                    try:
                        im = ImageOps.exif_transpose(im)
                    except Exception:
                        pass
                    if im.mode not in ("RGB",):
                        im = im.convert("RGB")
                    im.save(norm_path, format="JPEG", quality=92, optimize=True)
                return FileResponse(norm_path, media_type="image/jpeg")
            except Exception:
                return FileResponse(path)
        return FileResponse(path)
    return JSONResponse(status_code=404, content={"error": "not found"})


@app.delete("/datasets/{dataset_id}/image/{subset}/{name}")
async def delete_dataset_image(dataset_id: str, subset: str, name: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", dataset_id or ""):
        return JSONResponse(status_code=400, content={"error": "invalid dataset id"})
    if subset not in ("train", "val"):
        return JSONResponse(status_code=400, content={"error": "invalid subset"})
    safe_name = (name or "").replace("\x00", "").strip()
    if not safe_name or safe_name in (".", ".."):
        return JSONResponse(status_code=400, content={"error": "invalid image name"})
    if safe_name != os.path.basename(safe_name):
        return JSONResponse(status_code=400, content={"error": "invalid image name"})
    if "/" in safe_name or "\\" in safe_name:
        return JSONResponse(status_code=400, content={"error": "invalid image name"})
    if re.match(r"^[A-Za-z]:", safe_name):
        return JSONResponse(status_code=400, content={"error": "invalid image name"})

    dataset_dir = os.path.join(DATASETS_DIR, dataset_id)
    if not os.path.isdir(dataset_dir):
        return JSONResponse(status_code=404, content={"error": "dataset not found"})

    img_path = os.path.join(dataset_dir, "images", subset, safe_name)
    if not os.path.isfile(img_path):
        return JSONResponse(status_code=404, content={"error": "image not found"})

    base = os.path.splitext(safe_name)[0]
    label_path = os.path.join(dataset_dir, "labels", subset, base + ".txt")
    json_path = os.path.join(dataset_dir, "labels_json", subset, base + ".json")
    norm_path = os.path.join(dataset_dir, "_normalized_images", subset, base + ".jpg")

    try:
        os.remove(img_path)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"delete image failed: {str(e)}"})

    for p in (label_path, json_path, norm_path):
        try:
            if os.path.isfile(p):
                os.remove(p)
        except Exception:
            pass

    try:
        await ws_manager.broadcast(dataset_id, {"type": "remove", "image": safe_name, "subset": subset})
    except Exception:
        pass

    return {"dataset_id": dataset_id, "subset": subset, "image": safe_name, "deleted": True}


@app.get("/datasets/{dataset_id}/annotation/{subset}/{name}")
def get_dataset_annotation(dataset_id: str, subset: str, name: str):
    # Return JSON for Annotorious
    # We store raw JSON in labels_json/{subset}/{name}.json
    # OR we reconstruct from TXT?
    # Reconstructing from TXT is lossy (no polygon points if simplified, no IDs).
    # But `save_annotation` saves `labels_json`.
    # So we prefer `labels_json`.
    
    dataset_dir = os.path.join(DATASETS_DIR, dataset_id)
    base_name = os.path.splitext(name)[0]
    names = _dataset_class_names(dataset_id)
    image_path = os.path.join(dataset_dir, "images", subset, name)
    ori, raw_w, raw_h = _get_image_exif_orientation_and_size(image_path)
    
    # Try JSON first
    json_path = os.path.join(dataset_dir, "labels_json", subset, base_name + ".json")
    if os.path.isfile(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list) and len(data) > 0:
                cid_list: List[int] = []
                label_path = os.path.join(dataset_dir, "labels", subset, base_name + ".txt")
                if os.path.isfile(label_path) and os.path.getsize(label_path) > 0 and os.path.isfile(image_path):
                    pairs = _extract_xywh_and_cid_from_yolo_txt(label_path, image_path)
                    cid_list = [cid for cid, _xywh in pairs]
                for i, ann in enumerate(data):
                    if not isinstance(ann, dict):
                        continue
                    try:
                        if ori and raw_w and raw_h and not ann.get("_exif_normalized"):
                            target = ann.get("target") or {}
                            selector = target.get("selector") if isinstance(target, dict) else None
                            if isinstance(selector, dict) and selector.get("type") == "FragmentSelector":
                                val = selector.get("value") or ""
                                if isinstance(val, str) and val.startswith("xywh=pixel:"):
                                    parts = val.replace("xywh=pixel:", "").split(",")
                                    if len(parts) == 4:
                                        x, y, w, h = map(float, parts)
                                        nx, ny, nw, nh = _normalize_xywh_by_exif([x, y, w, h], ori, raw_w, raw_h)
                                        selector["value"] = f"xywh=pixel:{nx},{ny},{nw},{nh}"
                                        ann["_exif_normalized"] = True
                    except Exception:
                        pass
                    cid = _extract_class_id_from_annotation(ann, names)
                    if cid is None and i < len(cid_list):
                        cid = cid_list[i]
                    if cid is None:
                        continue
                    cname = _class_name_from_id(cid, names)
                    _ensure_annotation_tag(ann, cid, cname)
                return data
        except Exception:
            pass
            
    label_path = os.path.join(dataset_dir, "labels", subset, base_name + ".txt")
    image_path = os.path.join(dataset_dir, "images", subset, name)
    if os.path.isfile(label_path) and os.path.getsize(label_path) > 0 and os.path.isfile(image_path):
        pairs = _extract_xywh_and_cid_from_yolo_txt(label_path, image_path)
        if pairs:
            out: List[Dict[str, Any]] = []
            for i, (cid, xywh) in enumerate(pairs):
                cname = _class_name_from_id(cid, names)
                out.append(_build_fragment_annotation(dataset_id, subset, name, xywh, f"{base_name}#{i+1}", class_id=cid, class_name=cname))
            return out

    return []


@app.websocket("/ws/dataset/{dataset_id}")
async def websocket_endpoint(websocket: WebSocket, dataset_id: str):
    await ws_manager.connect(dataset_id, websocket)
    try:
        while True:
            # Just keep connection alive, maybe handle incoming messages if needed
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(dataset_id, websocket)


@app.get("/datasets/list")
def list_datasets():
    base = DATASETS_DIR
    items = []
    for d in os.listdir(base):
        p = os.path.join(base, d)
        if os.path.isdir(p):
            ok, problems = validate_dataset(d)
            try:
                created_at = int(os.path.getctime(p))
            except Exception:
                created_at = None
            items.append({"id": d, "path": p, "valid": ok, "problems": problems, "created_at": created_at})
    return {"items": items}

@app.get("/datasets/validate/{dataset_id}")
def validate(dataset_id: str):
    ok, problems = validate_dataset(dataset_id)
    return {"dataset_id": dataset_id, "valid": ok, "problems": problems}

@app.delete("/datasets/{dataset_id}")
def remove_dataset(dataset_id: str):
    success = delete_dataset(dataset_id)
    if not success:
        return JSONResponse(status_code=404, content={"error": "dataset not found"})
    return {"dataset_id": dataset_id, "deleted": True}

def _names_from_class_items(items: List[Dict[str, Any]]) -> List[str]:
    max_id = -1
    id_to_name: Dict[int, str] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            cid = int(it.get("id"))
        except Exception:
            continue
        if cid < 0:
            continue
        max_id = max(max_id, cid)
        nm = it.get("name")
        if isinstance(nm, str) and nm:
            id_to_name[cid] = nm
    if max_id < 0:
        return ["item"]
    return [id_to_name.get(i, f"class_{i}") for i in range(max_id + 1)]

@app.get("/datasets/{dataset_id}/classes")
def get_dataset_classes(dataset_id: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", dataset_id or ""):
        return JSONResponse(status_code=400, content={"error": "invalid dataset id"})
    dataset_dir = _dataset_dir_path(dataset_id)
    if not os.path.isdir(dataset_dir):
        return JSONResponse(status_code=404, content={"error": "dataset not found"})
    items = _load_dataset_classes_items(dataset_id)
    try:
        items = sorted(items, key=lambda x: int(x.get("id")) if isinstance(x, dict) and str(x.get("id", "")).lstrip("-").isdigit() else 10**9)
    except Exception:
        pass
    return {"items": items}

@app.post("/datasets/{dataset_id}/classes")
def add_dataset_class(dataset_id: str, payload: Dict[str, Any] = Body(...)):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", dataset_id or ""):
        return JSONResponse(status_code=400, content={"error": "invalid dataset id"})
    dataset_dir = _dataset_dir_path(dataset_id)
    if not os.path.isdir(dataset_dir):
        return JSONResponse(status_code=404, content={"error": "dataset not found"})
    name = ""
    try:
        name = str((payload or {}).get("name", "")).strip()
    except Exception:
        name = ""
    if not name or len(name) > 64:
        return JSONResponse(status_code=400, content={"error": "invalid class name"})

    items = _load_dataset_classes_items(dataset_id)
    max_id = -1
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            cid = int(it.get("id"))
        except Exception:
            continue
        max_id = max(max_id, cid)
    now = time.time()
    new_item = {"id": int(max_id + 1), "name": name, "deleted": False, "created_at": now, "updated_at": now}
    items.append(new_item)
    try:
        _atomic_write_json(_dataset_classes_json_path(dataset_dir), items)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"write classes failed: {str(e)}"})
    _update_dataset_yaml_names(dataset_id, _names_from_class_items(items))
    return {"item": new_item, "items": items}

@app.put("/datasets/{dataset_id}/classes/{class_id}")
def rename_dataset_class(dataset_id: str, class_id: int, payload: Dict[str, Any] = Body(...)):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", dataset_id or ""):
        return JSONResponse(status_code=400, content={"error": "invalid dataset id"})
    dataset_dir = _dataset_dir_path(dataset_id)
    if not os.path.isdir(dataset_dir):
        return JSONResponse(status_code=404, content={"error": "dataset not found"})
    if class_id < 0:
        return JSONResponse(status_code=400, content={"error": "invalid class id"})
    name = ""
    try:
        name = str((payload or {}).get("name", "")).strip()
    except Exception:
        name = ""
    if not name or len(name) > 64:
        return JSONResponse(status_code=400, content={"error": "invalid class name"})

    items = _load_dataset_classes_items(dataset_id)
    found = False
    now = time.time()
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            cid = int(it.get("id"))
        except Exception:
            continue
        if cid == int(class_id):
            it["name"] = name
            it["updated_at"] = now
            found = True
            break
    if not found:
        return JSONResponse(status_code=404, content={"error": "class not found"})
    try:
        _atomic_write_json(_dataset_classes_json_path(dataset_dir), items)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"write classes failed: {str(e)}"})
    _update_dataset_yaml_names(dataset_id, _names_from_class_items(items))
    return {"items": items}

@app.delete("/datasets/{dataset_id}/classes/{class_id}")
def delete_dataset_class(dataset_id: str, class_id: int):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", dataset_id or ""):
        return JSONResponse(status_code=400, content={"error": "invalid dataset id"})
    dataset_dir = _dataset_dir_path(dataset_id)
    if not os.path.isdir(dataset_dir):
        return JSONResponse(status_code=404, content={"error": "dataset not found"})
    if class_id < 0:
        return JSONResponse(status_code=400, content={"error": "invalid class id"})

    items = _load_dataset_classes_items(dataset_id)
    found = False
    now = time.time()
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            cid = int(it.get("id"))
        except Exception:
            continue
        if cid == int(class_id):
            it["deleted"] = True
            it["updated_at"] = now
            found = True
            break
    if not found:
        return JSONResponse(status_code=404, content={"error": "class not found"})
    try:
        _atomic_write_json(_dataset_classes_json_path(dataset_dir), items)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"write classes failed: {str(e)}"})
    _update_dataset_yaml_names(dataset_id, _names_from_class_items(items))
    _sync_dataset_labels_to_active_classes(dataset_id)
    return {"items": _load_dataset_classes_items(dataset_id)}

@app.get("/classes")
def get_classes():
    return {"classes": read_classes()}

@app.post("/classes")
def set_classes(payload: ClassDict):
    write_classes(payload.classes)
    return {"classes": read_classes()}

@app.post("/train/start", response_model=TrainStartResponse)
def train_start(cfg: TrainConfig):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", cfg.dataset_id or ""):
        return JSONResponse(status_code=400, content={"error": "invalid dataset id"})
    datasets_base = DATASETS_DIR
    if not os.path.isdir(os.path.join(datasets_base, cfg.dataset_id)):
        return JSONResponse(status_code=404, content={"error": "dataset not found"})

    try:
        model_version_id = _generate_model_version_id(cfg.dataset_id, cfg.model_version)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except FileExistsError:
        return JSONResponse(status_code=400, content={"error": "model version already exists"})

    global current_train_job_id
    job_id = uuid.uuid4().hex[:12]
    with jobs_lock:
        jobs[job_id] = {
            "status": "queued",
            "started_at": time.time(),
            "metrics": {
                "device": cfg.device,
                "epochs_total": int(cfg.epochs),
                "epochs_done": 0,
                "progress": 0.0,
                "eta_seconds": None,
                "elapsed_seconds": 0.0,
                "est_epoch_seconds": None,
                "dataset_id": cfg.dataset_id,
                "model_version_id": model_version_id,
            },
            "stop_requested": False,
        }
        current_train_job_id = job_id

    t = threading.Thread(target=_run_training, args=(job_id, cfg, model_version_id), daemon=True)
    t.start()
    return TrainStartResponse(job_id=job_id, status=jobs[job_id]["status"])

@app.get("/train/status/{job_id}", response_model=TrainStatusResponse)
def train_status(job_id: str):
    with jobs_lock:
        j = jobs.get(job_id)
    if not j:
        return JSONResponse(status_code=404, content={"error": "not found"})
    status = j.get("status")
    m = j.get("metrics") or {}
    if status in ("running", "queued"):
        started_at = float(j.get("started_at") or time.time())
        m["elapsed_seconds"] = max(0.0, time.time() - started_at)
        with jobs_lock:
            j = jobs.get(job_id) or j
            j["metrics"] = m
            jobs[job_id] = j
    return TrainStatusResponse(job_id=job_id, status=status, metrics=m)

@app.post("/train/stop/{job_id}")
def train_stop(job_id: str):
    with jobs_lock:
        j = jobs.get(job_id)
        if not j:
            return JSONResponse(status_code=404, content={"error": "not found"})
        
        if j.get("stop_requested"):
            j["status"] = "stopped"
            jobs[job_id] = j
            return {"job_id": job_id, "status": "stopped", "msg": "Forced stop"}

        j["stop_requested"] = True
        if j.get("status") == "queued":
            j["status"] = "stopped"
        jobs[job_id] = j
    
    return {"job_id": job_id, "status": j["status"]}


@app.get("/train/current", response_model=TrainStatusResponse)
def train_current():
    with jobs_lock:
        job_id = current_train_job_id
        j = jobs.get(job_id) if job_id else None
    if not job_id or not j:
        return TrainStatusResponse(job_id="", status="none", metrics=None)
    try:
        vid = ((j.get("metrics") or {}).get("model_version_id") or "").strip()
        if vid:
            _sync_latest_run_artifacts_to_canonical(vid)
    except Exception:
        pass
    return TrainStatusResponse(job_id=job_id, status=j.get("status"), metrics=j.get("metrics"))

@app.get("/train/map/{version_id}")
def train_map(version_id: str):
    vid = (version_id or "").strip()
    if not vid or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", vid):
        return JSONResponse(status_code=400, content={"error": "invalid version_id"})
    csv_path = os.path.join(RUNS_DIR, vid, "results.csv")
    if not os.path.isfile(csv_path):
        latest_dir = _find_latest_run_dir(vid, require_file="results.csv")
        if latest_dir:
            csv_path = os.path.join(latest_dir, "results.csv")
            _sync_latest_run_artifacts_to_canonical(vid)
    series = _read_map_series_from_results_csv(csv_path)
    return {"version_id": vid, "series": series}

@app.get("/models/list")
def models_list():
    base = MODELS_DIR
    items = []
    for d in os.listdir(base):
        p = os.path.join(base, d)
        if os.path.isdir(p):
            try:
                created_at = int(os.path.getctime(p))
            except Exception:
                created_at = None
            items.append({"version_id": d, "created_at": created_at})
    return {"items": items}

@app.get("/models/current")
def models_current():
    cur = os.path.join(MODELS_DIR, "current.txt")
    if os.path.exists(cur):
        with open(cur, "r") as f:
            return {"version_id": f.read().strip()}
    return {"version_id": None}

@app.post("/models/publish")
def models_publish(payload: PublishRequest):
    model_manager.publish(payload.version_id)
    return {"version_id": payload.version_id}

@app.delete("/models/{version_id}")
def models_delete(version_id: str):
    vid = (version_id or "").strip()
    if not vid:
        return JSONResponse(status_code=400, content={"error": "missing version_id"})

    if vid in (".", "..") or ("/" in vid) or ("\\" in vid):
        return JSONResponse(status_code=400, content={"error": "invalid version_id"})

    base_abs = os.path.abspath(MODELS_DIR)
    target_abs = os.path.abspath(os.path.join(MODELS_DIR, vid))
    if not target_abs.startswith(base_abs + os.sep):
        return JSONResponse(status_code=400, content={"error": "invalid version_id"})

    if not os.path.isdir(target_abs):
        return JSONResponse(status_code=404, content={"error": "not found"})

    current_id = None
    cur_file = os.path.join(MODELS_DIR, "current.txt")
    if os.path.exists(cur_file):
        try:
            with open(cur_file, "r", encoding="utf-8") as f:
                current_id = f.read().strip() or None
        except Exception:
            current_id = None

    if current_id and current_id == vid:
        return JSONResponse(status_code=400, content={"error": "cannot delete current model"})

    try:
        shutil.rmtree(target_abs)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"delete failed: {str(e)}"})

    return {"version_id": vid, "deleted": True}

@app.post("/models/delete")
def models_delete_post(payload: PublishRequest):
    return models_delete(payload.version_id)

@app.get("/system/ips")
def system_ips():
    ips = set()

    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None):
            if info[0] == socket.AF_INET:
                ip = info[4][0]
                if ip and not ip.startswith("127."):
                    ips.add(ip)
    except Exception:
        pass

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127."):
            ips.add(ip)
    except Exception:
        pass

    return {"ips": sorted(ips)}

@app.get("/system/select_folder")
def select_folder():
    """
    Opens a native folder selection dialog on the server side (local machine)
    and returns the selected path.
    """
    try:
        import subprocess
        import sys
        
        # PowerShell command to open FolderBrowserDialog
        ps_script = """
Add-Type -AssemblyName System.Windows.Forms
$f = New-Object System.Windows.Forms.FolderBrowserDialog
$f.Description = '请选择数据集存储路径'
$f.ShowNewFolderButton = $true
$res = $f.ShowDialog()
if ($res -eq 'OK') {
    Write-Output $f.SelectedPath
}
"""
        # Encode command to base64 to avoid escaping issues
        import base64
        encoded_command = base64.b64encode(ps_script.encode('utf-16le')).decode('utf-8')
        
        cmd = ["powershell", "-NoProfile", "-EncodedCommand", encoded_command]
        
        # subprocess.run with capture_output requires Python 3.7+
        # If running on older python, might need adjustment, but here we assume modern env.
        # Ensure encoding is handled
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
        
        if result.returncode != 0:
             # If failed, maybe try fallback or return error
             return JSONResponse(status_code=500, content={"error": f"PowerShell error: {result.stderr}"})
             
        folder_path = result.stdout.strip()
        return {"path": folder_path}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"Failed to open dialog: {str(e)}"})

@app.post("/annotation/save")
async def save_annotation(
    dataset_id: str = Form(...),
    annotations_json: str = Form(...),
    width: int = Form(...),
    height: int = Form(...),
    file: Optional[UploadFile] = File(None),
    image_name: Optional[str] = Form(None),
    subset: Optional[str] = Form(None),
    class_id: int = Form(0), 
    val_split: float = Form(0.1),
):
    dataset_dir = os.path.join(DATASETS_DIR, dataset_id)
    if not os.path.isdir(dataset_dir):
        return JSONResponse(status_code=404, content={"error": "dataset not found"})

    # Parse annotations
    try:
        ann_list = json.loads(annotations_json)
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid annotations json"})
    if not isinstance(ann_list, list):
        return JSONResponse(status_code=400, content={"error": "annotations json must be a list"})

    target_image_name = image_name
    target_split = subset

    # If new file upload
    if file:
        split = "val" if random.random() < val_split else "train"
        images_dir = os.path.join(dataset_dir, "images", split)
        _safe_mkdir(images_dir)
        
        ext = os.path.splitext(file.filename)[1]
        if not ext:
            ext = ".jpg"
        unique_name = uuid.uuid4().hex
        target_image_name = unique_name + ext
        target_split = split
        image_path = os.path.join(images_dir, target_image_name)
        
        with open(image_path, "wb") as f:
            f.write(await file.read())
    elif target_image_name and target_split:
        # Existing file
        pass
    else:
        return JSONResponse(status_code=400, content={"error": "missing file or image_name/subset"})

    base_name = os.path.splitext(target_image_name)[0]

    labels_dir = os.path.join(dataset_dir, "labels", target_split)
    json_dir = os.path.join(dataset_dir, "labels_json", target_split)
    label_path = os.path.join(labels_dir, base_name + ".txt")
    json_path = os.path.join(json_dir, base_name + ".json")

    if len(ann_list) == 0:
        try:
            if os.path.isfile(label_path):
                os.remove(label_path)
        except Exception:
            pass
        try:
            if os.path.isfile(json_path):
                os.remove(json_path)
        except Exception:
            pass

        try:
            import asyncio
            msg = {
                "type": "update",
                "image": target_image_name,
                "subset": target_split,
                "labeled": False,
            }
            loop = asyncio.get_event_loop()
            loop.create_task(ws_manager.broadcast(dataset_id, msg))
        except Exception:
            pass

        return {"status": "ok", "image": target_image_name, "split": target_split, "deleted": True}

    _safe_mkdir(labels_dir)
    _safe_mkdir(json_dir)

    names = _dataset_class_names(dataset_id)
    normalized: List[Any] = []
    for ann in ann_list:
        if isinstance(ann, dict):
            cid = _extract_class_id_from_annotation(ann, names)
            if cid is None:
                try:
                    cid = int(class_id)
                except Exception:
                    cid = 0
            cname = _class_name_from_id(cid, names)
            _ensure_annotation_tag(ann, cid, cname)
            try:
                ann["_exif_normalized"] = True
            except Exception:
                pass
        normalized.append(ann)
    ann_list = normalized

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(ann_list, f)

    with open(label_path, "w", encoding="utf-8") as f:
        for ann in ann_list:
            try:
                cid = _extract_class_id_from_annotation(ann, names)
                if cid is None:
                    cid = int(class_id)

                selector = ann.get("target", {}).get("selector", {})
                val = selector.get("value", "")

                x, y, w, h = 0, 0, 0, 0

                if selector.get("type") == "FragmentSelector" and val.startswith("xywh=pixel:"):
                    x, y, w, h = map(float, val.replace("xywh=pixel:", "").split(","))
                elif selector.get("type") == "SvgSelector":
                    import re
                    match = re.search(r'points=["\']([\d\s,.]+)["\']', val)
                    if match:
                        points_str = match.group(1)
                        coords = [float(p) for p in re.split(r'[\s,]+', points_str) if p]
                        xs = coords[0::2]
                        ys = coords[1::2]
                        if xs and ys:
                            min_x, max_x = min(xs), max(xs)
                            min_y, max_y = min(ys), max(ys)
                            x = min_x
                            y = min_y
                            w = max_x - min_x
                            h = max_y - min_y

                if w <= 0 or h <= 0:
                    continue

                center_x = (x + w / 2.0) / float(width)
                center_y = (y + h / 2.0) / float(height)
                norm_w = w / float(width)
                norm_h = h / float(height)

                center_x = max(0.0, min(1.0, center_x))
                center_y = max(0.0, min(1.0, center_y))
                norm_w = max(0.0, min(1.0, norm_w))
                norm_h = max(0.0, min(1.0, norm_h))

                f.write(f"{cid} {center_x:.6f} {center_y:.6f} {norm_w:.6f} {norm_h:.6f}\n")
            except Exception:
                pass
            
    # Trigger auto update classes
    try:
        data_yaml = _ensure_dataset_yaml(dataset_dir)
        _auto_update_classes(dataset_dir, data_yaml)
    except Exception:
        pass
    
    # Broadcast update via WebSocket
    try:
        import asyncio
        msg = {
            "type": "update",
            "image": target_image_name,
            "subset": target_split,
            "labeled": True
        }
        # Fire and forget
        loop = asyncio.get_event_loop()
        loop.create_task(ws_manager.broadcast(dataset_id, msg))
    except Exception:
        pass

    return {"status": "ok", "image": target_image_name, "split": target_split}

if os.path.isdir(RUNS_DIR):
    app.mount("/runs", StaticFiles(directory=RUNS_DIR), name="runs")

FRONTEND_DIR = os.path.join(BUNDLE_DIR, "frontend")
@app.get("/logo.png")
def get_logo_png():
    candidates = [
        os.path.join(FRONTEND_DIR, "logo.png"),
        os.path.join(os.path.dirname(BUNDLE_DIR), "logo.png"),
        os.path.join(BUNDLE_DIR, "logo.png"),
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return FileResponse(path, media_type="image/png")
    return JSONResponse(status_code=404, content={"error": "not found"})

if os.path.isdir(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
