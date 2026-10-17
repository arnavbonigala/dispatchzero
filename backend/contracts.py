from typing import Callable, TypedDict


class Call(TypedDict):
    call_id: str
    t: str
    lat: float
    lon: float
    hour: int
    nbhd: str
    station_area: str
    priority: str
    processing_s: float
    turnout_s: float
    drive_s: float
    busy_s: float
    historical_s: float
    historical_unit: str
    week: int
    split: str


class IdleUnit(TypedDict):
    unit_id: str
    post: int


class CallResult(TypedDict):
    call_id: str
    t: str
    lat: float
    lon: float
    unit: str
    from_post: int
    eta_s: float
    response_s: float
    historical_s: float
    hour: int
    nbhd: str


class Summary(TypedDict):
    version: str
    split: str
    slow_median_s: float
    slow_n: int
    median_s: float
    mean_s: float
    p90_s: float
    pct_under_8min: float
    n: int


class Version(TypedDict):
    version: str
    parent: str
    diagnosis: str
    train_median: float
    frozen_median: float
    accepted: bool


class CameraReading(TypedDict):
    camera_id: str
    name: str
    lat: float
    lon: float
    ts: str
    vehicles: int
    moving_frac: float
    slowdown: float


Eta = Callable[[IdleUnit, Call], float]
PostPlan = Callable[[int, list[str]], dict[str, int]]
ChooseUnit = Callable[[Call, list[IdleUnit], Eta], str]
SendEngine = Callable[[Call, list[IdleUnit], Eta, float], "str | None"]
