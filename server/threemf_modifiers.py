"""Load OrthoCAD modifier 3MF into the belt slice without touching the STL path.

Orca applies per-volume settings only from ``Metadata/model_settings.config``
(see ``_BBS_3MF_Importer`` in ``src/libslic3r/Format/bbs_3mf.cpp``):

- ``<part id="N" subtype="modifier_part">`` becomes ``ModelVolumeType::PARAMETER_MODIFIER``.
  ``id`` is the component ``objectid`` of that mesh, not the parent object id.
- ``<metadata key="sparse_infill_density" value="20%"/>`` is stored with
  ``volume->config.set_deserialize``. The same call accepts ``sparse_infill_pattern``
  (``gyroid``) and any other real process key. Unknown keys are ignored.
- ``subtype`` is read from the attribute. A later ``part_type`` / volume-type
  metadata value overrides it, matching the importer.
- The root ``<metadata name="Application">`` must start with ``OrcaSlicer-`` or
  ``BambuStudio-`` so the CLI treats the file as a project (``is_bbl_3mf``).
  Otherwise ``--slice 1`` is reset and ``ensure_on_bed`` drops the placement.
  A missing marker is stamped ``OrcaSlicer-2.3.0``.
- A missing ``Metadata/project_settings.config`` is filled with a string
  ``printable_height`` so the CLI does not null-deref. ``--load-settings``
  still supplies the machine and the process, including global infill.

The form field ``infill_density`` stays the global process ``sparse_infill_density``
(``VS_SPARSE_INFILL_DENSITY`` / ``--load-settings``). Density keys on the main
``normal_part`` and on the parent object are removed so the main solid inherits
that global value. Modifier densities must be one of the hardness presets
(14, 20, 26, 32, 38). A file that declares modifiers and yields none is
``ModifierRegionsNotApplied`` (HTTP 422).

Belt placement is the same rigid move as ``prepare_orthocad_insole_stl``:
Z rotation, Y/Z min at 0, X centered. It is composed onto every build-item
transform so the main solid and its modifiers stay registered. STL bytes never
enter this module.
"""
from __future__ import annotations

import json
import math
import re
import struct
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from validation.apex_bed_placement import APEX_INSOLE_X_CENTER_MM, APEX_PRINTABLE_X_MAX_MM
from validation.apex_insole_densities import KENDON_INFILL_DENSITY_PRESETS
from validation.apex_insole_tunables import DEFAULT_PRINT_DIRECTION, orthocad_base_rotation_z_deg

ALLOWED_REGION_PERCENTS = frozenset(KENDON_INFILL_DENSITY_PRESETS.values())
_GYROID = "gyroid"
_DENSITY_KEY = "sparse_infill_density"
_PATTERN_KEY = "sparse_infill_pattern"
_TYPE_KEYS = ("part_type", "volume_type")
_APPLICATION_RE = re.compile(
    r"<metadata\s+name=\"Application\">([^<]*)</metadata>",
    re.IGNORECASE,
)


class ThreemfError(ValueError):
    """The upload is a 3MF this slicer will not run."""


class ModifierRegionsNotApplied(ThreemfError):
    """A 3MF declared modifier regions and none of them will be sliced."""


def is_3mf_file(path: Path, filename: str | None = None) -> bool:
    """True when the upload name ends in ``.3mf`` or the body is a ZIP."""
    name = filename if filename is not None else path.name
    if Path(name).suffix.lower() == ".3mf":
        return True
    try:
        with Path(path).open("rb") as handle:
            return handle.read(4) == b"PK\x03\x04"
    except OSError:
        return False


def prepare_belt_3mf(
    src: Path,
    dest_dir: Path | None = None,
    *,
    print_direction: str = DEFAULT_PRINT_DIRECTION,
    x_center_mm: float = APEX_INSOLE_X_CENTER_MM,
    bed_width_x_mm: float = APEX_PRINTABLE_X_MAX_MM,
    place: bool = True,
) -> tuple[Path, dict]:
    """Validate modifier parts, optionally belt-place, and write a 3MF Orca will honor.

    Returns ``(path, meta)``. ``meta["regions_applied"]`` is the number of
    modifier volumes in the file passed to Orca. ``meta["placed_main_stl"]`` is
    a binary STL of the placed main solid for the belt Y trim, which reads STL.
    """
    src = Path(src)
    if not zipfile.is_zipfile(src):
        raise ThreemfError(f"{src.name} is not a 3MF zip")

    with zipfile.ZipFile(src) as zin:
        names = {_norm(info.filename): info.filename for info in zin.infolist()}
        blobs = {_norm(info.filename): zin.read(info.filename) for info in zin.infolist()}

    root_key = _find_root_model(names)
    if root_key is None:
        raise ThreemfError("3MF has no 3D/3dmodel.model")

    settings_key = _find_name(names, "Metadata/model_settings.config")
    settings_xml = blobs[settings_key].decode("utf-8", errors="replace") if settings_key else ""
    root_xml = blobs[root_key].decode("utf-8", errors="replace")
    if _declares_modifiers(blobs) and not settings_key:
        raise ModifierRegionsNotApplied(
            "3MF contains modifier_part data but no Metadata/model_settings.config, "
            "so Orca would slice it as one global density. 0 regions applied."
        )

    geoms = _load_geoms(blobs)
    root_objs = geoms.get(root_key, {})
    parts, object_ids = _parse_settings(settings_xml) if settings_xml else ([], [])

    failures: list[str] = []
    applied: list[dict] = []
    for part in parts:
        if not part["is_modifier"]:
            continue
        mesh = _mesh_for_part(part, root_objs, geoms)
        if mesh is None:
            failures.append(
                f"modifier_part id={part['id']} has no mesh (component objectid must match the part id)"
            )
            continue
        pct = part["density"]
        if pct not in ALLOWED_REGION_PERCENTS:
            shown = part["density_raw"] or "(missing)"
            allowed = ", ".join(f"{n}%" for n in sorted(ALLOWED_REGION_PERCENTS))
            failures.append(
                f"modifier_part id={part['id']} sparse_infill_density {shown} is not an infill preset ({allowed})"
            )
            continue
        applied.append({**part, "mesh": mesh})

    if failures:
        detail = "; ".join(failures)
        raise ModifierRegionsNotApplied(
            f"3MF modifiers were not applied ({detail}). Refusing to slice at the global density only."
        )
    if not applied and _modifier_markers(blobs):
        raise ModifierRegionsNotApplied(
            "3MF contains modifier_part entries but 0 regions have a mesh and a preset density. "
            "Refusing to slice at the global density only."
        )

    if settings_xml:
        settings_xml = _rewrite_settings(settings_xml, object_ids)
        blobs[settings_key] = settings_xml.encode("utf-8")

    deg = orthocad_base_rotation_z_deg(print_direction) if place else 0
    instances = {item["object_id"]: item["matrix"] for item in _build_items(root_xml)}
    main_parts = [p for p in parts if not p["is_modifier"]]
    if not main_parts and root_objs:
        # A mesh-only 3MF (no model_settings parts) still has to place.
        for oid, geom in root_objs.items():
            if geom["triangles"] or geom["components"]:
                main_parts.append(
                    {
                        "object_id": oid,
                        "id": oid,
                        "is_modifier": False,
                        "density": None,
                        "density_raw": "",
                        "pattern": "",
                    }
                )
                break
    main_world = _world_triangles(main_parts, root_objs, geoms, instances)
    if place and not main_world:
        raise ThreemfError("3MF has no normal_part mesh to place on the belt")

    placement: dict = {
        "regions_applied": len(applied),
        "rotation_z_deg": deg if place else 0,
        "x_center_mm": x_center_mm,
        "print_direction": print_direction if place else None,
        "modifiers": [
            {
                "id": p["id"],
                "sparse_infill_density": f"{p['density']}%",
                "sparse_infill_pattern": p["pattern"] or _GYROID,
            }
            for p in applied
        ],
    }

    new_instances: dict[str, str] = {}
    if place:
        world_pts = [v for tri in main_world for v in tri]
        rotated = [_rotate_z(x, y, deg) + (z,) for x, y, z in world_pts]
        xs = [p[0] for p in rotated]
        ys = [p[1] for p in rotated]
        zs = [p[2] for p in rotated]
        min_x, max_x = min(xs), max(xs)
        min_y, min_z = min(ys), min(zs)
        width = max_x - min_x
        if width > bed_width_x_mm + 0.05:
            raise ThreemfError(
                f"insole width {width:.2f} mm exceeds belt width {bed_width_x_mm} mm after rotation {deg}°"
            )
        x_shift = x_center_mm - (width / 2.0) - min_x
        y_shift = -min_y
        z_shift = -min_z
        rigid = _mul(_translation(x_shift, y_shift, z_shift), _rotation_z(deg))
        items = _build_items(root_xml)
        if not items:
            raise ThreemfError("3MF has no build item to place")
        for item in items:
            new_instances[item["object_id"]] = _format_transform(_mul(rigid, item["matrix"]))
        root_xml = _rewrite_item_transforms(root_xml, new_instances)
        placement.update(
            {
                "model_x_shift_mm": round(x_shift, 3),
                "oriented_x_across_belt_mm": round(width, 3),
                "oriented_y_along_belt_mm": round(max(ys) - min_y, 3),
                "oriented_z_mm": round(max(zs) - min_z, 3),
                "bed_width_x_mm": bed_width_x_mm,
            }
        )
        placed_main = [
            tuple(_apply(rigid, *v) for v in tri)
            for tri in main_world
        ]
        placement["placed_main_bounds"] = _bounds(placed_main)
        placed_mods = []
        for part in applied:
            tris = _world_triangles([part], root_objs, geoms, instances)
            moved = [tuple(_apply(rigid, *v) for v in tri) for tri in tris]
            placed_mods.append({"id": part["id"], "density": part["density"], **_bounds(moved)})
        placement["placed_modifiers"] = placed_mods
        stl_tris = placed_main
    else:
        stl_tris = main_world
        placement["placed_main_bounds"] = _bounds(main_world) if main_world else {}
        placement["placed_modifiers"] = []

    root_xml = _ensure_application(root_xml)
    blobs[root_key] = root_xml.encode("utf-8")
    # CLI::run reads printable_height with no null check on a Bambu/Orca 3MF.
    # A missing project file segfaults (exit -11) before --load-settings runs.
    # The stub is only that guard; the machine/process JSON still replaces it.
    _ensure_project_settings(blobs, names)

    out_dir = Path(dest_dir) if dest_dir is not None else Path(tempfile.mkdtemp(prefix="belt_3mf_"))
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"{src.stem}_belt.3mf"
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as zout:
        for key, original in names.items():
            zout.writestr(original, blobs[key])
    stl_path = out_dir / f"{src.stem}_main.stl"
    _write_stl(stl_path, stl_tris)
    placement["preprocessed_stl"] = str(dest)
    placement["placed_main_stl"] = str(stl_path)
    return dest, placement


def _declares_modifiers(blobs: dict[str, bytes]) -> bool:
    return _modifier_markers(blobs)


def _modifier_markers(blobs: dict[str, bytes]) -> bool:
    for key, data in blobs.items():
        if not key.endswith((".model", ".config", ".xml")):
            continue
        if b"modifier_part" in data:
            return True
    return False


def _norm(name: str) -> str:
    return name.replace("\\", "/").lstrip("/")


def _find_name(names: dict[str, str], want: str) -> str | None:
    want_n = _norm(want).lower()
    for key in names:
        if key.lower() == want_n:
            return key
    return None


def _find_root_model(names: dict[str, str]) -> str | None:
    for candidate in ("3D/3dmodel.model", "3d/3dmodel.model"):
        found = _find_name(names, candidate)
        if found:
            return found
    for key in names:
        if key.lower().endswith("3dmodel.model"):
            return key
    return None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _attr(el: ET.Element, name: str) -> str | None:
    if name in el.attrib:
        return el.attrib[name]
    for key, value in el.attrib.items():
        if _local(key) == name:
            return value
    return None


def _unit_factor(root: ET.Element) -> float:
    unit = (_attr(root, "unit") or "millimeter").lower()
    return {
        "micron": 0.001,
        "millimeter": 1.0,
        "centimeter": 10.0,
        "inch": 25.4,
        "foot": 304.8,
        "meter": 1000.0,
    }.get(unit, 1.0)


def _parse_transform(text: str | None) -> list[list[float]]:
    """Orca's 3MF matrix: 12 numbers, column-major 3x4 (see bbs_get_transform_from_3mf_specs_string)."""
    ident = _identity()
    if text is None or not str(text).strip():
        return ident
    nums = [float(part) for part in str(text).split()]
    if len(nums) != 12:
        return ident
    m = _identity()
    i = 0
    for col in range(4):
        for row in range(3):
            m[row][col] = nums[i]
            i += 1
    return m


def _format_transform(m: list[list[float]]) -> str:
    parts: list[str] = []
    for col in range(4):
        for row in range(3):
            parts.append(_fmt(m[row][col]))
    return " ".join(parts)


def _fmt(n: float) -> str:
    if abs(n) < 1e-9:
        return "0"
    text = f"{n:.10f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def _identity() -> list[list[float]]:
    return [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]]


def _mul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    out = [[0.0] * 4 for _ in range(4)]
    for i in range(4):
        for j in range(4):
            out[i][j] = a[i][0] * b[0][j] + a[i][1] * b[1][j] + a[i][2] * b[2][j] + a[i][3] * b[3][j]
    return out


def _translation(tx: float, ty: float, tz: float) -> list[list[float]]:
    m = _identity()
    m[0][3], m[1][3], m[2][3] = tx, ty, tz
    return m


def _rotation_z(deg: float) -> list[list[float]]:
    rad = math.radians(deg)
    c, s = math.cos(rad), math.sin(rad)
    m = _identity()
    m[0][0], m[0][1] = c, -s
    m[1][0], m[1][1] = s, c
    return m


def _apply(m: list[list[float]], x: float, y: float, z: float) -> tuple[float, float, float]:
    return (
        m[0][0] * x + m[0][1] * y + m[0][2] * z + m[0][3],
        m[1][0] * x + m[1][1] * y + m[1][2] * z + m[1][3],
        m[2][0] * x + m[2][1] * y + m[2][2] * z + m[2][3],
    )


def _rotate_z(x: float, y: float, deg: float) -> tuple[float, float]:
    rad = math.radians(deg)
    c, s = math.cos(rad), math.sin(rad)
    return x * c - y * s, x * s + y * c


def _load_geoms(blobs: dict[str, bytes]) -> dict[str, dict[str, dict]]:
    geoms: dict[str, dict[str, dict]] = {}
    for key, data in blobs.items():
        if not key.lower().endswith(".model"):
            continue
        try:
            root = ET.fromstring(data)
        except ET.ParseError as exc:
            raise ThreemfError(f"3MF mesh XML is not readable ({key}): {exc}") from exc
        scale = _unit_factor(root)
        objects: dict[str, dict] = {}
        for el in root.iter():
            if _local(el.tag) != "object":
                continue
            oid = _attr(el, "id")
            if not oid:
                continue
            vertices: list[tuple[float, float, float]] = []
            triangles: list[tuple[int, int, int]] = []
            components: list[dict] = []
            for child in list(el):
                kind = _local(child.tag)
                if kind == "mesh":
                    for sub in child.iter():
                        sub_kind = _local(sub.tag)
                        if sub_kind == "vertex":
                            vertices.append(
                                (
                                    float(_attr(sub, "x") or 0) * scale,
                                    float(_attr(sub, "y") or 0) * scale,
                                    float(_attr(sub, "z") or 0) * scale,
                                )
                            )
                        elif sub_kind == "triangle":
                            triangles.append(
                                (
                                    int(_attr(sub, "v1") or 0),
                                    int(_attr(sub, "v2") or 0),
                                    int(_attr(sub, "v3") or 0),
                                )
                            )
                elif kind == "components":
                    for sub in list(child):
                        if _local(sub.tag) != "component":
                            continue
                        path = _attr(sub, "path") or ""
                        components.append(
                            {
                                "path": _norm(path) if path else key,
                                "object_id": _attr(sub, "objectid") or "",
                                "matrix": _parse_transform(_attr(sub, "transform")),
                            }
                        )
            objects[oid] = {
                "vertices": vertices,
                "triangles": triangles,
                "components": components,
            }
        geoms[key] = objects
    return geoms


def _parse_settings(xml: str) -> tuple[list[dict], list[str]]:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ThreemfError(f"Metadata/model_settings.config is not readable: {exc}") from exc
    parts: list[dict] = []
    object_ids: list[str] = []
    for obj in root.iter():
        if _local(obj.tag) != "object":
            continue
        oid = obj.get("id") or ""
        object_ids.append(oid)
        for part_el in list(obj):
            if _local(part_el.tag) != "part":
                continue
            meta = {
                (child.get("key") or ""): (child.get("value") or "")
                for child in list(part_el)
                if _local(child.tag) == "metadata"
            }
            subtype = (part_el.get("subtype") or "normal_part").strip()
            for key in _TYPE_KEYS:
                if meta.get(key):
                    subtype = meta[key].strip()
            density_raw = meta.get(_DENSITY_KEY, "")
            density = _parse_percent(density_raw) if density_raw else None
            pattern = (meta.get(_PATTERN_KEY) or "").strip().lower()
            parts.append(
                {
                    "object_id": oid,
                    "id": part_el.get("id") or "",
                    "is_modifier": subtype == "modifier_part",
                    "density": density,
                    "density_raw": density_raw,
                    "pattern": pattern,
                }
            )
    return parts, object_ids


def _parse_percent(value: str) -> int | None:
    raw = value.strip().rstrip("%").strip()
    if not raw:
        return None
    try:
        number = float(raw)
    except ValueError:
        return None
    nearest = int(round(number))
    if abs(number - nearest) > 0.05:
        return None
    return nearest


def _mesh_for_part(part: dict, root_objs: dict, geoms: dict[str, dict[str, dict]]) -> dict | None:
    parent = root_objs.get(part["object_id"])
    if parent is None:
        for objects in geoms.values():
            if part["object_id"] in objects:
                parent = objects[part["object_id"]]
                break
    if parent is None:
        return None
    for comp in parent["components"]:
        if comp["object_id"] == part["id"]:
            target = geoms.get(comp["path"], {}).get(comp["object_id"])
            if target and target["triangles"]:
                return {"component": comp, "geom": target, "parent_id": part["object_id"]}
    if not parent["components"] and parent["triangles"] and not part["is_modifier"]:
        return {
            "component": {"path": "", "object_id": part["id"], "matrix": _identity()},
            "geom": parent,
            "parent_id": part["object_id"],
        }
    return None


def _world_triangles(
    parts: list[dict],
    root_objs: dict,
    geoms: dict,
    instances: dict[str, list[list[float]]],
) -> list[tuple]:
    tris: list[tuple] = []
    for part in parts:
        mesh = part.get("mesh") or _mesh_for_part(part, root_objs, geoms)
        if mesh is None:
            continue
        geom = mesh["geom"]
        comp = mesh["component"]["matrix"]
        inst = instances.get(part["object_id"], _identity())
        world = _mul(inst, comp)
        for a, b, c in geom["triangles"]:
            try:
                tris.append(tuple(_apply(world, *geom["vertices"][i]) for i in (a, b, c)))
            except IndexError as exc:
                raise ThreemfError(f"3MF part id={part.get('id')} mesh indices are out of range") from exc
    return tris


def _build_items(xml: str) -> list[dict]:
    items = []
    for match in re.finditer(r"<item\b[^>]*>", xml):
        tag = match.group(0)
        oid = re.search(r'\bobjectid="(\d+)"', tag)
        if not oid:
            continue
        transform = re.search(r'\btransform="([^"]*)"', tag)
        items.append(
            {
                "object_id": oid.group(1),
                "matrix": _parse_transform(transform.group(1) if transform else None),
                "span": match.span(),
            }
        )
    return items


def _rewrite_item_transforms(xml: str, new_by_object: dict[str, str]) -> str:
    def repl(match: re.Match[str]) -> str:
        tag = match.group(0)
        oid = re.search(r'\bobjectid="(\d+)"', tag)
        if not oid or oid.group(1) not in new_by_object:
            return tag
        new_t = new_by_object[oid.group(1)]
        if re.search(r'\btransform="', tag):
            return re.sub(r'\btransform="[^"]*"', f'transform="{new_t}"', tag, count=1)
        if tag.endswith("/>"):
            return tag[:-2] + f' transform="{new_t}"/>'
        return tag[:-1] + f' transform="{new_t}">'

    return re.sub(r"<item\b[^>]*>", repl, xml)


def _ensure_project_settings(blobs: dict[str, bytes], names: dict[str, str]) -> None:
    """Keep Orca's CLI from null-dereferencing a 3MF that has no project config."""
    stub = {
        "printable_height": "250",
        "printable_area": ["0x0", "250x0", "250x250", "0x250"],
        "bed_exclude_area": ["0x0"],
    }
    key = _find_name(names, "Metadata/project_settings.config")
    if key is None:
        blobs["Metadata/project_settings.config"] = json.dumps(stub).encode("utf-8")
        names["Metadata/project_settings.config"] = "Metadata/project_settings.config"
        return
    try:
        data = json.loads(blobs[key].decode("utf-8"))
    except json.JSONDecodeError:
        blobs[key] = json.dumps(stub).encode("utf-8")
        return
    if not isinstance(data, dict):
        blobs[key] = json.dumps(stub).encode("utf-8")
        return
    height = data.get("printable_height")
    if isinstance(height, str) and height.strip():
        return
    data["printable_height"] = "250"
    blobs[key] = json.dumps(data).encode("utf-8")


def _ensure_application(xml: str) -> str:
    match = _APPLICATION_RE.search(xml)
    if match and (match.group(1).startswith("BambuStudio-") or match.group(1).startswith("OrcaSlicer-")):
        return xml
    stamped = '<metadata name="Application">OrcaSlicer-2.3.0</metadata>'
    if match:
        return xml[: match.start()] + stamped + xml[match.end() :]
    return re.sub(r"(<model\b[^>]*>)", r"\1\n " + stamped, xml, count=1)


def _rewrite_settings(xml: str, object_ids: list[str]) -> str:
    root = ET.fromstring(xml)
    main_id = object_ids[0] if object_ids else ""
    for obj in list(root):
        if _local(obj.tag) != "object":
            continue
        if not main_id:
            main_id = obj.get("id") or ""
        for child in list(obj):
            if _local(child.tag) == "metadata" and child.get("key") == _DENSITY_KEY:
                obj.remove(child)
            if _local(child.tag) != "part":
                continue
            subtype = child.get("subtype") or "normal_part"
            metas = [m for m in list(child) if _local(m.tag) == "metadata"]
            meta_map = {m.get("key"): m for m in metas}
            for key in _TYPE_KEYS:
                if key in meta_map and meta_map[key].get("value"):
                    subtype = meta_map[key].get("value") or subtype
            if subtype == "modifier_part":
                pattern = meta_map.get(_PATTERN_KEY)
                if pattern is None:
                    child.append(ET.Element("metadata", {"key": _PATTERN_KEY, "value": _GYROID}))
                elif not (pattern.get("value") or "").strip():
                    pattern.set("value", _GYROID)
            else:
                if _DENSITY_KEY in meta_map:
                    child.remove(meta_map[_DENSITY_KEY])
    if not any(_local(el.tag) == "plate" for el in list(root)):
        plate = ET.Element("plate")
        for key, value in (
            ("plater_id", "1"),
            ("plater_name", ""),
            ("locked", "false"),
        ):
            plate.append(ET.Element("metadata", {"key": key, "value": value}))
        inst = ET.Element("model_instance")
        inst.append(ET.Element("metadata", {"key": "object_id", "value": main_id}))
        inst.append(ET.Element("metadata", {"key": "instance_id", "value": "0"}))
        inst.append(ET.Element("metadata", {"key": "identify_id", "value": "1"}))
        plate.append(inst)
        root.append(plate)
    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body + "\n"


def _bounds(tris: list[tuple]) -> dict[str, float]:
    pts = [v for tri in tris for v in tri]
    if not pts:
        return {}
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    zs = [p[2] for p in pts]
    return {
        "xmin": min(xs),
        "xmax": max(xs),
        "ymin": min(ys),
        "ymax": max(ys),
        "zmin": min(zs),
        "zmax": max(zs),
    }


def _write_stl(path: Path, tris: list[tuple]) -> None:
    with path.open("wb") as handle:
        handle.write(b"vertexslice 3mf main solid".ljust(80, b"\0")[:80])
        handle.write(struct.pack("<I", len(tris)))
        for verts in tris:
            flat = [0.0, 0.0, 1.0]
            for x, y, z in verts:
                flat.extend((x, y, z))
            handle.write(struct.pack("<12f", *flat))
            handle.write(struct.pack("<H", 0))
