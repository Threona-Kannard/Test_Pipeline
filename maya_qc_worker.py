from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def _check_scene(cmds: Any, scene_path: str) -> dict[str, Any]:
    issues: list[str] = []
    try:
        cmds.file(
            scene_path,
            open=True,
            force=True,
            prompt=False,
            executeScriptNodes=False,
        )
        meshes = cmds.ls(type="mesh", long=True, noIntermediate=True) or []
        checked_transforms: set[str] = set()

        for shape in meshes:
            history = cmds.listHistory(shape, pruneDagObjects=True) or []
            history_nodes = [
                node for node in history if cmds.nodeType(node) not in ("mesh",)
            ]
            if history_nodes:
                issues.append(
                    f"Clean History: {shape} has construction history "
                    f"({', '.join(history_nodes[:5])})"
                )

            transform = (cmds.listRelatives(shape, parent=True, fullPath=True) or [None])[0]
            if transform and transform not in checked_transforms:
                checked_transforms.add(transform)
                translate = cmds.getAttr(f"{transform}.translate")[0]
                rotate = cmds.getAttr(f"{transform}.rotate")[0]
                scale = cmds.getAttr(f"{transform}.scale")[0]
                if (
                    any(abs(float(value)) > 1e-5 for value in translate)
                    or any(abs(float(value)) > 1e-5 for value in rotate)
                    or any(abs(float(value) - 1.0) > 1e-5 for value in scale)
                ):
                    issues.append(
                        f"Freeze Transform: {transform} has non-default "
                        "translate, rotate, or scale values"
                    )

            face_info = cmds.polyInfo(f"{shape}.f[*]", faceToVertex=True) or []
            ngon_count = sum(
                1
                for item in face_info
                if len(item.partition(":")[2].split()) >= 5
            )
            if ngon_count:
                issues.append(
                    f"N-gons: {shape} has {ngon_count} face(s) with 5 or more sides"
                )

            uv_sets = cmds.polyUVSet(shape, query=True, allUVSets=True) or []
            if len(uv_sets) != 1:
                issues.append(
                    f"UV sets: {shape} has {len(uv_sets)}; expected exactly 1"
                )
    except (AttributeError, IndexError, OSError, RuntimeError, TypeError, ValueError) as error:
        return {
            "path": scene_path,
            "error": f"Could not inspect Maya scene: {error}",
        }
    return {"path": scene_path, "issues": issues}


def main() -> int:
    if len(sys.argv) != 2:
        print("Expected one JSON manifest path.", file=sys.stderr)
        return 2

    manifest_path = Path(sys.argv[1])
    try:
        scene_paths = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"Could not read Maya QC manifest: {error}", file=sys.stderr)
        return 2

    try:
        import maya.standalone
        import maya.cmds as cmds

        maya.standalone.initialize(name="python")
        try:
            results = [_check_scene(cmds, path) for path in scene_paths]
        finally:
            maya.standalone.uninitialize()
    except (ImportError, OSError, RuntimeError) as error:
        print(f"Could not initialize or run Maya standalone: {error}", file=sys.stderr)
        return 1

    print("QC_RESULT_JSON:" + json.dumps(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
