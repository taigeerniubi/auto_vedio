"""定义可由界面和智能体共同编辑的调色参数及预设目录。"""
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Unit = Annotated[float, Field(ge=0, le=1)]
Signed = Annotated[float, Field(ge=-1, le=1)]
ColorPreset = Literal[
    "natural", "cream", "warm", "portrait", "soft_skin", "cool_portrait",
    "forest", "ocean", "autumn", "food", "coffee", "fresh",
    "teal_orange", "faded_film", "vintage", "cool", "neon", "monochrome",
]
COLOR_PRESETS = json.loads((Path(__file__).resolve().parents[3] / "packages/composition/colorPresets.json").read_text(encoding="utf-8"))


class ColorModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, allow_inf_nan=False)


class CurvePoint(ColorModel):
    x: Unit
    y: Unit


def identity():
    return [CurvePoint(x=0, y=0), CurvePoint(x=1, y=1)]


def flat():
    return [CurvePoint(x=0, y=.5), CurvePoint(x=1, y=.5)]


class ColorCurves(ColorModel):
    master: list[CurvePoint] = Field(default_factory=identity, min_length=2, max_length=16)
    red: list[CurvePoint] = Field(default_factory=identity, min_length=2, max_length=16)
    green: list[CurvePoint] = Field(default_factory=identity, min_length=2, max_length=16)
    blue: list[CurvePoint] = Field(default_factory=identity, min_length=2, max_length=16)
    hue_hue: list[CurvePoint] = Field(default_factory=flat, min_length=2, max_length=16)
    hue_sat: list[CurvePoint] = Field(default_factory=flat, min_length=2, max_length=16)
    hue_lum: list[CurvePoint] = Field(default_factory=flat, min_length=2, max_length=16)

    @model_validator(mode="after")
    def ordered(self):
        for name in type(self).model_fields:
            points = getattr(self, name)
            if points[0].x != 0 or points[-1].x != 1 or any(a.x >= b.x for a, b in zip(points, points[1:])):
                raise ValueError("Curve x coordinates must increase strictly from 0 to 1")
            if name.startswith("hue_") and points[0].y != points[-1].y:
                raise ValueError("Hue curves must join at the red boundary")
        return self


class ColorWheel(ColorModel):
    hue: Annotated[float, Field(ge=0, le=360)] = 0
    saturation: Unit = 0
    luminance: Signed = 0


class ColorWheels(ColorModel):
    shadows: ColorWheel = Field(default_factory=ColorWheel)
    midtones: ColorWheel = Field(default_factory=ColorWheel)
    highlights: ColorWheel = Field(default_factory=ColorWheel)
    offset: ColorWheel = Field(default_factory=ColorWheel)


class HslChannel(ColorModel):
    hue: Annotated[float, Field(ge=-180, le=180)] = 0
    saturation: Signed = 0
    luminance: Signed = 0


class HslChannels(ColorModel):
    red: HslChannel = Field(default_factory=HslChannel)
    orange: HslChannel = Field(default_factory=HslChannel)
    yellow: HslChannel = Field(default_factory=HslChannel)
    green: HslChannel = Field(default_factory=HslChannel)
    cyan: HslChannel = Field(default_factory=HslChannel)
    blue: HslChannel = Field(default_factory=HslChannel)
    purple: HslChannel = Field(default_factory=HslChannel)
    magenta: HslChannel = Field(default_factory=HslChannel)


class ColorGrade(ColorModel):
    enabled: bool = True
    exposure: Annotated[float, Field(ge=-5, le=5)] = 0
    contrast: Annotated[float, Field(ge=0, le=2)] = 1
    temperature: Signed = 0
    tint: Signed = 0
    saturation: Annotated[float, Field(ge=0, le=2)] = 1
    vibrance: Signed = 0
    highlights: Signed = 0
    shadows: Signed = 0
    whites: Signed = 0
    blacks: Signed = 0
    wheels: ColorWheels = Field(default_factory=ColorWheels)
    hsl: HslChannels = Field(default_factory=HslChannels)
    curves: ColorCurves = Field(default_factory=ColorCurves)
