"""part_remover: deletes parts whose center enters its volume and counts them by name."""

from __future__ import annotations

REM = {
    "id": "REM_01",
    "type": "part_remover",
    "description": "Discharge Part Remover",
    "position": [4.1, 0.0, 0.0],
    "size": [0.3, 1.0, 1.0],
}
BOX = (0.3, 0.2, 0.3)


def test_removes_part_whose_center_is_inside_and_counts_by_name(rig):
    r = rig(REM)
    inside = r.world.spawn("tall", BOX, (4.1, 0.1, 0.0))
    edge = r.world.spawn("short", BOX, (4.25, 0.1, 0.0))  # center exactly on the face
    outside = r.world.spawn("short", BOX, (3.9, 0.1, 0.0))  # overlaps the volume, center outside
    r.step()
    assert inside.id not in r.world.parts
    assert edge.id not in r.world.parts
    assert outside.id in r.world.parts
    assert r["REM_01"].state() == {"removed": 2, "by_name": {"tall": 1, "short": 1}}
    assert r.world.removed == {"tall": 1, "short": 1}


def test_rotated_volume(rig):
    r = rig({**REM, "position": [0.0, 0.0, 0.0], "size": [2.0, 1.0, 0.2], "rotation_deg": 90})
    along_world_z = r.world.spawn("short", BOX, (0.0, 0.0, 0.9))  # long axis now on world z
    along_world_x = r.world.spawn("short", BOX, (0.9, 0.0, 0.0))
    r.step()
    assert along_world_z.id not in r.world.parts
    assert along_world_x.id in r.world.parts


def test_no_ports_and_geometry(rig):
    r = rig(REM)
    remover = r["REM_01"]
    assert remover.bindings == {} and remover.inputs == {} and remover.outputs == {}
    assert remover.geometry()["size"] == [0.3, 1.0, 1.0]
