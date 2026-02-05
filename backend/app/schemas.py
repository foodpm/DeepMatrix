from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Dict, Any


class Box(BaseModel):
    box: List[float]  # [x1, y1, x2, y2]
    class_id: int
    class_name: str = Field(..., alias="class") # Use 'class' in JSON
    confidence: float

    class Config:
        populate_by_name = True


class InferResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    boxes: List[Box]
    model_version_id: Optional[str] = None
    model_source: Optional[str] = None


class TrainConfig(BaseModel):
    dataset_id: str
    model_version: Optional[str] = Field(default=None, max_length=64)
    epochs: int = Field(default=50, ge=1)
    batch: int = Field(default=8, ge=1)
    imgsz: int = Field(default=640, ge=64)
    device: str = Field(default="cpu")
    optimizer: str = Field(default="AdamW")
    lr0: float = Field(default=0.001, gt=0)
    augment: bool = True


class TrainStartResponse(BaseModel):
    job_id: str
    status: str


class TrainStatusResponse(BaseModel):
    job_id: str
    status: str
    metrics: Optional[Dict[str, Any]] = None


class ClassDict(BaseModel):
    classes: List[str]


class PublishRequest(BaseModel):
    version_id: str


class DatasetCreateRequest(BaseModel):
    dataset_id: str
    path: Optional[str] = None
    val_split: float = 0.2


class ImageItem(BaseModel):
    name: str
    subset: str  # 'train' or 'val'
    labeled: bool
    url: str
    w: Optional[int] = None
    h: Optional[int] = None
    boxes: Optional[List[List[float]]] = None
