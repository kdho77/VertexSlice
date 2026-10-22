#!/usr/bin/env python3
"""Minimal 3MF with Prusa layer_config_ranges for Orca belt CLI slicing."""
from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape


def layer_config_ranges_xml(ranges: list[tuple[float, float, dict[str, str]]]) -> str:
    """ranges: list of (min_z, max_z, option dict)."""
    parts = ['<?xml version="1.0" encoding="UTF-8"?>\n<objects>']
    parts.append('<object id="1">')
    for min_z, max_z, opts in ranges:
        parts.append(f' <range min_z="{min_z:g}" max_z="{max_z:g}">')
        for key, val in opts.items():
            parts.append(f'  <option opt_key="{escape(key)}">{escape(val)}</option>')
        parts.append(" </range>")
    parts.append("</object>")
    parts.append("</objects>")
    return "\n".join(parts) + "\n"


def write_belt_3mf(
    stl_path: Path,
    out_3mf: Path,
    *,
    plate_x: float = 125.0,
    plate_y: float = 5.0,
    heel_ranges: list[tuple[float, float, dict[str, str]]] | None = None,
) -> Path:
    stl_name = stl_path.name
    stl_data = stl_path.read_bytes()

    model_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<model unit="millimeter" xml:lang="en-US"
 xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
 xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
 requiredextensions="p">
 <metadata name="Application">OrcaSlicer-2.3.2-dev</metadata>
 <resources>
  <object id="1" p:UUID="00000001-0000-0000-0000-000000000001" type="model">
   <components>
    <component p:path="/3D/Objects/{stl_name}_1.model" objectid="1"
               p:UUID="00010000-0000-0000-0000-000000000001"
               transform="1 0 0 0 1 0 0 0 1 0 0 0"/>
   </components>
  </object>
 </resources>
 <build p:UUID="00000002-0000-0000-0000-000000000001">
  <item objectid="1" p:UUID="00000003-0000-0000-0000-000000000001"
        transform="1 0 0 0 1 0 0 0 1 {plate_x:g} {plate_y:g} 0" printable="1"/>
 </build>
</model>"""

    object_xml = """<?xml version="1.0" encoding="UTF-8"?>
<model unit="millimeter" xml:lang="en-US"
 xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
 xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06">
 <resources>
  <object id="1" type="model">
   <mesh><vertices/><triangles/></mesh>
  </object>
 </resources>
</model>"""

    project_settings = json.dumps(
        {
            "printer_is_belt": "1",
            "printer_structure": "belt",
            "gcode_flavor": "klipper",
            "use_relative_e_distances": "1",
        }
    )

    slice_info = f"""<?xml version="1.0" encoding="UTF-8"?>
<config>
  <plate>
    <metadata key="index" value="1"/>
    <object identify_id="1" name="{escape(stl_name)}" skipped="false" />
  </plate>
</config>"""

    model_settings = """<?xml version="1.0" encoding="UTF-8"?>
<config>
  <object id="1">
    <metadata key="name" value="object"/>
    <part id="1" subtype="normal_part">
      <metadata key="name" value="part1"/>
      <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>
    </part>
  </object>
</config>"""

    content_types = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
</Types>"""

    rels = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Target="/3D/3dmodel.model" Id="rel-1" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>"""

    model_rels = f"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Target="/3D/Objects/{stl_name}_1.model" Id="rel-1" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>"""

    out_3mf.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_3mf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("3D/3dmodel.model", model_xml)
        zf.writestr("3D/_rels/3dmodel.model.rels", model_rels)
        zf.writestr(f"3D/Objects/{stl_name}_1.model", object_xml)
        zf.writestr(f"3D/Objects/{stl_name}", stl_data)
        zf.writestr("Metadata/project_settings.config", project_settings)
        zf.writestr("Metadata/slice_info.config", slice_info)
        zf.writestr("Metadata/model_settings.config", model_settings)
        if heel_ranges:
            zf.writestr(
                "Metadata/Prusa_Slicer_layer_config_ranges.xml",
                layer_config_ranges_xml(heel_ranges),
            )
    return out_3mf
