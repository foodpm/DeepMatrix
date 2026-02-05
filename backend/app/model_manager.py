import os
import threading
from typing import List, Optional, Tuple
from PIL import Image, ImageOps
import io
from .storage import DEFAULT_YOLOV8N_FILE

class DetectionResult:
    def __init__(self, boxes: List[dict], ov_suggestions: Optional[List[str]] = None):
        self.boxes = boxes
        self.ov_suggestions = ov_suggestions or []

class ModelManager:
    def __init__(self, models_dir: str):
        self.models_dir = models_dir
        self.current_model_path = None
        self.current_version_id = None
        self.engine = None
        self._lock = threading.RLock()
        self._current_file = os.path.join(self.models_dir, "current.txt")
        self._current_file_mtime = None
        self._current_model_mtime = None
        self._load_current()

    def _load_current(self):
        self.ensure_current_loaded(force=True)

    def _read_current_version_id_from_disk(self) -> Optional[str]:
        try:
            if not os.path.exists(self._current_file):
                return None
            with open(self._current_file, "r", encoding="utf-8", errors="replace") as f:
                s = f.read().strip()
            return s or None
        except Exception:
            return None

    def _get_current_file_mtime(self) -> Optional[float]:
        try:
            return os.path.getmtime(self._current_file)
        except Exception:
            return None
    
    def _get_file_mtime(self, path: Optional[str]) -> Optional[float]:
        try:
            if not path:
                return None
            return os.path.getmtime(path)
        except Exception:
            return None

    def _load_version_locked(self, version_id: str) -> bool:
        candidate = os.path.join(self.models_dir, version_id, "best.pt")
        if not os.path.exists(candidate):
            return False
        from ultralytics import YOLO
        print(f"Loading custom model from {candidate}")
        self.engine = YOLO(candidate)
        self.current_model_path = candidate
        self.current_version_id = version_id
        self._current_model_mtime = self._get_file_mtime(candidate)
        return True

    def _load_fallback_locked(self) -> None:
        from ultralytics import YOLO
        candidate = DEFAULT_YOLOV8N_FILE if os.path.isfile(DEFAULT_YOLOV8N_FILE) else "yolov8n.pt"
        print(f"Loading standard model as fallback: {candidate}")
        self.engine = YOLO(candidate)
        self.current_model_path = candidate
        self.current_version_id = None
        self._current_model_mtime = self._get_file_mtime(candidate)

    def ensure_current_loaded(self, force: bool = False) -> None:
        with self._lock:
            disk_version_id = self._read_current_version_id_from_disk()
            disk_mtime = self._get_current_file_mtime()
            disk_model_path = (
                os.path.join(self.models_dir, disk_version_id, "best.pt")
                if disk_version_id
                else self.current_model_path
            )
            disk_model_mtime = self._get_file_mtime(disk_model_path)
            if (
                not force
                and disk_version_id == self.current_version_id
                and (disk_mtime is None or self._current_file_mtime == disk_mtime)
                and (disk_model_mtime is None or self._current_model_mtime == disk_model_mtime)
            ):
                return

            self._current_file_mtime = disk_mtime

            if disk_version_id:
                try:
                    if self._load_version_locked(disk_version_id):
                        return
                except Exception as e:
                    print(f"Failed to load custom model: {e}")

            try:
                self._load_fallback_locked()
            except Exception as e:
                print(f"Failed to load fallback model: {e}")
                self.engine = None
                self.current_model_path = None
                self.current_version_id = None
                self._current_model_mtime = None

    def publish(self, version_id: str):
        with self._lock:
            path = os.path.join(self.models_dir, version_id, "best.pt")
            if not os.path.exists(path):
                raise FileNotFoundError("model not found")

            os.makedirs(self.models_dir, exist_ok=True)
            with open(self._current_file, "w", encoding="utf-8") as f:
                f.write(version_id)
            self._current_file_mtime = self._get_current_file_mtime()

            try:
                self._load_version_locked(version_id)
                print(f"Published and loaded model: {version_id}")
            except Exception as e:
                print(f"Error loading published model: {e}")
                self.engine = None
                self.current_model_path = None
                self.current_version_id = None
                self._current_model_mtime = None

    def infer_image(self, image_bytes: bytes, conf: float = 0.25, iou: float = 0.45, max_det: int = 100) -> Tuple[DetectionResult, Optional[str], str]:
        try:
            img = Image.open(io.BytesIO(image_bytes))
            img = ImageOps.exif_transpose(img).convert("RGB")
        except Exception:
            return DetectionResult([]), None, "default"

        with self._lock:
            self.ensure_current_loaded(force=False)
            engine = self.engine
            version_id = self.current_version_id
            source = "published" if version_id else "default"

            if engine is None:
                return DetectionResult([]), version_id, source

            try:
                results = engine.predict(img, conf=conf, iou=iou, max_det=max_det, imgsz=640, verbose=False)
                boxes = []
                names = getattr(engine, "names", {}) or {}
                for r in results:
                    for b in r.boxes:
                        xyxy = b.xyxy[0].tolist()
                        cls_id = int(b.cls[0].item())
                        class_name = names.get(cls_id, str(cls_id))

                        boxes.append({
                            "box": [float(x) for x in xyxy],
                            "class_id": cls_id,
                            "class": class_name,
                            "confidence": float(b.conf[0].item())
                        })
                return DetectionResult(boxes), version_id, source
            except Exception as e:
                print(f"Inference error: {e}")
                return DetectionResult([]), version_id, source
