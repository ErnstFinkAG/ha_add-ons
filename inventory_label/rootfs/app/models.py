from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ID_RE = re.compile(r"^[a-z0-9]+$")
OPTIONS_FILE = Path("/data/options.json")
LOGO_SEARCH_PATHS = [Path("/config"), Path("/share")]


class ConfigError(ValueError):
    """Raised when the add-on configuration is invalid."""


@dataclass(slots=True)
class LabelProfile:
    id: str
    name: str
    printerhost: str
    printer_port: int = 9100
    printer_dpi: int = 203
    label_width_mm: float = 0.0
    label_length_mm: float = 0.0
    top_margin_mm: float = 0.0
    left_margin_mm: float = 0.0
    print_rotation: int = 0
    qr_code_quietzone_modules: int = 3
    qr_code_error_correction: str = "M"
    show_in_preview: bool = True


@dataclass(slots=True)
class LabelField:
    id: str
    name: str
    fontsize: int = 12
    default_value: str = ""
    valuelist: list[str] = field(default_factory=list)
    logo: bool = False
    logo_path: str = ""
    heading: str = ""
    fontfamily: str = "Arial"
    position: str = "text"
    max_lines: int = 1
    footer_margin_bottom: float = 0.0
    bold: bool = False
    italic: bool = False
    underline: bool = False
    print_by_default: bool = True
    numbers_only: bool = False
    append_current_date: bool = False
    default_for_rendering_qr_code: bool = False


@dataclass(slots=True)
class NormalizedConfig:
    labelprofiles: list[LabelProfile]
    labelfields: list[LabelField]
    qr_default_field_id: str | None
    available_logo_files: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "labelprofiles": [asdict(item) for item in self.labelprofiles],
            "labelfields": [asdict(item) for item in self.labelfields],
            "qr_default_field_id": self.qr_default_field_id,
            "available_logo_files": self.available_logo_files,
        }


def _require_id(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not ID_RE.fullmatch(text):
        raise ConfigError(f"{field_name} must contain only lowercase letters and digits: {text!r}")
    return text


def _require_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ConfigError(f"{field_name} must not be empty")
    return text


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off", ""}:
            return False
    return bool(value)


def _to_int(value: Any, field_name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{field_name} must be an integer") from exc


def _to_float(value: Any, field_name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{field_name} must be a number") from exc


def _normalize_value_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = [part.strip() for part in value.splitlines()]
        return [item for item in raw_items if item]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    raise ConfigError("valuelist must be a list of strings")


def _normalize_profile(raw: dict[str, Any]) -> LabelProfile:
    profile = LabelProfile(
        id=_require_id(raw.get("id"), "labelprofiles[].id"),
        name=_require_text(raw.get("name"), "labelprofiles[].name"),
        printerhost=_require_text(raw.get("printerhost"), "labelprofiles[].printerhost"),
        printer_port=_to_int(raw.get("printer_port", 9100), "labelprofiles[].printer_port"),
        printer_dpi=_to_int(raw.get("printer_dpi", 203), "labelprofiles[].printer_dpi"),
        label_width_mm=_to_float(raw.get("label_width_mm", 0), "labelprofiles[].label_width_mm"),
        label_length_mm=_to_float(raw.get("label_length_mm", 0), "labelprofiles[].label_length_mm"),
        top_margin_mm=_to_float(raw.get("top_margin_mm", 0), "labelprofiles[].top_margin_mm"),
        left_margin_mm=_to_float(raw.get("left_margin_mm", 0), "labelprofiles[].left_margin_mm"),
        print_rotation=_to_int(raw.get("print_rotation", 0), "labelprofiles[].print_rotation"),
        qr_code_quietzone_modules=_to_int(
            raw.get("qr_code_quietzone_modules", 3),
            "labelprofiles[].qr_code_quietzone_modules",
        ),
        qr_code_error_correction=str(raw.get("qr_code_error_correction", "M")).strip().upper() or "M",
        show_in_preview=_to_bool(raw.get("show_in_preview", True)),
    )

    if profile.print_rotation not in {0, 90, 180, 270}:
        raise ConfigError("labelprofiles[].print_rotation must be one of 0, 90, 180, 270")
    if profile.qr_code_quietzone_modules not in {0, 1, 2, 3, 4}:
        raise ConfigError("labelprofiles[].qr_code_quietzone_modules must be one of 0, 1, 2, 3, 4")
    if profile.qr_code_error_correction not in {"H", "Q", "M", "L"}:
        raise ConfigError("labelprofiles[].qr_code_error_correction must be one of H, Q, M, L")
    if profile.printer_port <= 0:
        raise ConfigError("labelprofiles[].printer_port must be greater than 0")
    if profile.printer_dpi <= 0:
        raise ConfigError("labelprofiles[].printer_dpi must be greater than 0")
    if profile.label_width_mm <= 0 or profile.label_length_mm <= 0:
        raise ConfigError("labelprofiles[].label_width_mm and labelprofiles[].label_length_mm must be greater than 0")
    return profile


def _normalize_field(raw: dict[str, Any]) -> LabelField:
    field_value = LabelField(
        id=_require_id(raw.get("id"), "labelfields[].id"),
        name=_require_text(raw.get("name"), "labelfields[].name"),
        fontsize=_to_int(raw.get("fontsize", 12), "labelfields[].fontsize"),
        default_value=str(raw.get("default_value", "") or ""),
        valuelist=_normalize_value_list(raw.get("valuelist", [])),
        logo=_to_bool(raw.get("logo", False)),
        logo_path=str(raw.get("logo_path", "") or "").strip(),
        heading=str(raw.get("heading", "") or "").strip(),
        fontfamily=str(raw.get("fontfamily", "Arial") or "Arial").strip(),
        position=str(raw.get("position", "text") or "text").strip().lower(),
        max_lines=_to_int(raw.get("max_lines", 1), "labelfields[].max_lines"),
        footer_margin_bottom=_to_float(
            raw.get("footer_margin_bottom", 0),
            "labelfields[].footer_margin_bottom",
        ),
        bold=_to_bool(raw.get("bold", False)),
        italic=_to_bool(raw.get("italic", False)),
        underline=_to_bool(raw.get("underline", False)),
        print_by_default=_to_bool(raw.get("print_by_default", True)),
        numbers_only=_to_bool(raw.get("numbers_only", False)),
        append_current_date=_to_bool(raw.get("append_current_date", False)),
        default_for_rendering_qr_code=_to_bool(raw.get("default_for_rendering_qr_code", False)),
    )

    if field_value.fontsize <= 0:
        raise ConfigError("labelfields[].fontsize must be greater than 0")
    if field_value.max_lines <= 0:
        raise ConfigError("labelfields[].max_lines must be greater than 0")
    if field_value.position not in {"text", "footer"}:
        raise ConfigError("labelfields[].position must be text or footer")
    if field_value.numbers_only:
        if field_value.default_value and not field_value.default_value.isdigit():
            raise ConfigError(
                f"labelfields[].default_value for field {field_value.id!r} must contain only digits when numbers_only is enabled"
            )
        invalid_values = [item for item in field_value.valuelist if not item.isdigit()]
        if invalid_values:
            raise ConfigError(
                f"labelfields[].valuelist for field {field_value.id!r} contains non-numeric values: {invalid_values}"
            )
    if not field_value.logo:
        field_value.logo_path = ""
    return field_value


def _list_available_logo_files() -> list[str]:
    found: list[str] = []
    for base_path in LOGO_SEARCH_PATHS:
        if not base_path.exists():
            continue
        for path in base_path.rglob("*.png"):
            try:
                found.append(str(path.relative_to(base_path)))
            except ValueError:
                found.append(str(path))
    return sorted(set(found))


def load_raw_options() -> dict[str, Any]:
    if not OPTIONS_FILE.exists():
        return {"labelprofiles": [], "labelfields": []}
    with OPTIONS_FILE.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ConfigError("/data/options.json must contain an object at the top level")
    data.setdefault("labelprofiles", [])
    data.setdefault("labelfields", [])
    return data


def normalize_config(raw: dict[str, Any] | None = None) -> NormalizedConfig:
    raw = raw or load_raw_options()
    raw_profiles = raw.get("labelprofiles") or []
    raw_fields = raw.get("labelfields") or []

    if not isinstance(raw_profiles, list):
        raise ConfigError("labelprofiles must be a list")
    if not isinstance(raw_fields, list):
        raise ConfigError("labelfields must be a list")

    profiles = [_normalize_profile(item or {}) for item in raw_profiles]
    fields = [_normalize_field(item or {}) for item in raw_fields]

    profile_ids = [item.id for item in profiles]
    if len(profile_ids) != len(set(profile_ids)):
        raise ConfigError("labelprofiles contain duplicate ids")

    field_ids = [item.id for item in fields]
    if len(field_ids) != len(set(field_ids)):
        raise ConfigError("labelfields contain duplicate ids")

    qr_defaults = [item.id for item in fields if item.default_for_rendering_qr_code]
    if len(qr_defaults) > 1:
        raise ConfigError(
            "Only one labelfield may have default_for_rendering_qr_code enabled"
        )

    return NormalizedConfig(
        labelprofiles=profiles,
        labelfields=fields,
        qr_default_field_id=qr_defaults[0] if qr_defaults else None,
        available_logo_files=_list_available_logo_files(),
    )
