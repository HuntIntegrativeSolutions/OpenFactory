"""Write the example custom mesh, ``tests/scenes/models/pusher-housing.glb`` (PLAN 2.15).

Usage::

    uv run python tools/make_example_mesh.py            # rewrite the file
    uv run python tools/make_example_mesh.py --check    # exit 1 if the committed file differs

The mesh is ours (HIS Controls, CC-BY-4.0; ``pusher-housing.glb.license.txt`` beside it), built
here from boxes so no outside asset is involved: a square-profile pneumatic cylinder housing for
the sorter's ``CYL_01`` (stroke 0.6 m), in the pusher's local frame (the plate face at the origin,
the plate extending along +z, so the housing lies along -z from z -0.06 to -0.76, the span of the
viewer's primitive body). Body and tie rods are a light aluminium grey, the end caps and the foot a
dark grey; neither is an ISA-101 status color.

The file is binary glTF 2.0 ("GLB File Format" in the Khronos glTF 2.0 specification,
https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html): a 12-byte header, a JSON chunk padded
with spaces and a BIN chunk padded with zeros, each to 4 bytes. One mesh with one primitive per
material: POSITION (with the min/max the specification requires) and NORMAL as float32, indices as
uint16. The output is deterministic, and ``tests/unit/test_mesh.py`` rebuilds it byte for byte.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "tests" / "scenes" / "models" / "pusher-housing.glb"

ALUMINIUM = 0xBFC5CC
DARK = 0x374151

Box = tuple[tuple[float, float, float], tuple[float, float, float]]  # min corner, max corner


def housing() -> dict[int, list[Box]]:
    """Boxes by color, in meters, pusher local frame."""
    body: list[Box] = [((-0.045, -0.045, -0.72), (0.045, 0.045, -0.10))]
    rods: list[Box] = [
        ((x - 0.006, y - 0.006, -0.76), (x + 0.006, y + 0.006, -0.06))
        for x in (-0.05, 0.05)
        for y in (-0.05, 0.05)
    ]
    caps: list[Box] = [
        ((-0.06, -0.06, -0.10), (0.06, 0.06, -0.06)),  # rod-end cap
        ((-0.06, -0.06, -0.76), (0.06, 0.06, -0.72)),  # rear cap
        ((-0.09, -0.085, -0.76), (0.09, -0.06, -0.66)),  # mounting foot under the rear cap
    ]
    return {ALUMINIUM: body + rods, DARK: caps}


FACES = [  # outward normal, then the four corners (as min/max picks per axis), counter-clockwise
    ((1, 0, 0), [(1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1)]),
    ((-1, 0, 0), [(0, 0, 1), (0, 1, 1), (0, 1, 0), (0, 0, 0)]),
    ((0, 1, 0), [(0, 1, 1), (1, 1, 1), (1, 1, 0), (0, 1, 0)]),
    ((0, -1, 0), [(0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)]),
    ((0, 0, 1), [(0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]),
    ((0, 0, -1), [(1, 0, 0), (0, 0, 0), (0, 1, 0), (1, 1, 0)]),
]


def boxes_to_arrays(boxes: list[Box]) -> tuple[list[float], list[float], list[int]]:
    positions: list[float] = []
    normals: list[float] = []
    indices: list[int] = []
    for lo, hi in boxes:
        for normal, corners in FACES:
            base = len(positions) // 3
            for pick in corners:
                positions += [hi[i] if pick[i] else lo[i] for i in range(3)]
                normals += list(normal)
            indices += [base, base + 1, base + 2, base, base + 2, base + 3]
    return positions, normals, indices


def linear(channel: int) -> float:
    """sRGB 0-255 to the linear factor glTF's baseColorFactor takes."""
    c = channel / 255
    return round(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4, 6)


def glb() -> bytes:
    gltf: dict = {
        "asset": {"version": "2.0", "generator": "OpenFactory tools/make_example_mesh.py"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": "pusher-housing"}],
        "meshes": [{"name": "pusher-housing", "primitives": []}],
        "materials": [],
        "accessors": [],
        "bufferViews": [],
        "buffers": [],
    }
    blob = bytearray()

    def view(data: bytes, target: int) -> int:
        while len(blob) % 4:
            blob.append(0)
        gltf["bufferViews"].append(
            {"buffer": 0, "byteOffset": len(blob), "byteLength": len(data), "target": target}
        )
        blob.extend(data)
        return len(gltf["bufferViews"]) - 1

    def accessor(**fields) -> int:
        gltf["accessors"].append(fields)
        return len(gltf["accessors"]) - 1

    for color, boxes in housing().items():
        positions, normals, indices = boxes_to_arrays(boxes)
        n = len(positions) // 3
        xyz = [positions[i::3] for i in range(3)]
        pos = accessor(
            bufferView=view(struct.pack(f"<{len(positions)}f", *positions), 34962),
            componentType=5126, count=n, type="VEC3",
            min=[min(a) for a in xyz], max=[max(a) for a in xyz],
        )  # fmt: skip
        nrm = accessor(
            bufferView=view(struct.pack(f"<{len(normals)}f", *normals), 34962),
            componentType=5126, count=n, type="VEC3",
        )  # fmt: skip
        idx = accessor(
            bufferView=view(struct.pack(f"<{len(indices)}H", *indices), 34963),
            componentType=5123, count=len(indices), type="SCALAR",
        )  # fmt: skip
        rgb = [linear((color >> s) & 0xFF) for s in (16, 8, 0)]
        gltf["materials"].append(
            {"pbrMetallicRoughness": {"baseColorFactor": [*rgb, 1.0], "metallicFactor": 0.3,
                                      "roughnessFactor": 0.6}}
        )  # fmt: skip
        gltf["meshes"][0]["primitives"].append(
            {"attributes": {"POSITION": pos, "NORMAL": nrm}, "indices": idx,
             "material": len(gltf["materials"]) - 1}
        )  # fmt: skip
    while len(blob) % 4:
        blob.append(0)
    gltf["buffers"].append({"byteLength": len(blob)})
    text = json.dumps(gltf, separators=(",", ":"), sort_keys=True).encode()
    text += b" " * (-len(text) % 4)
    chunks = struct.pack("<I4s", len(text), b"JSON") + text
    chunks += struct.pack("<I4s", len(blob), b"BIN\0") + bytes(blob)
    return struct.pack("<4sII", b"glTF", 2, 12 + len(chunks)) + chunks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write the example pusher housing (PLAN 2.15).")
    parser.add_argument("--check", action="store_true", help="exit 1 if the file differs")
    args = parser.parse_args(argv)
    data = glb()
    if args.check:
        if not OUT.is_file() or OUT.read_bytes() != data:
            print(f"{OUT} is stale; run: uv run python tools/make_example_mesh.py", file=sys.stderr)
            return 1
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(data)
    print(f"wrote {OUT.relative_to(REPO)} ({len(data)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
