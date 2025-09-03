from __future__ import annotations
from typing import Dict, Optional
from pydantic import BaseModel

class Metric(BaseModel):
    key: str
    label: Optional[str] = None
    value: Optional[float] = None
    cloudvalue: Optional[float] = None
    delta_from_cloud: Optional[float] = None
    vendor_level: Optional[str] = None
    band: Optional[str] = None
    color: Optional[str] = None

class MachineScan(BaseModel):
    checkid: Optional[int] = None
    name: Optional[str] = None
    phone: Optional[str] = None
    age: Optional[int] = None
    sampling_images: Dict[str, str] = {}
    metrics: Dict[str, Metric] = {}
    raw: Dict = {}
