"""Small geometry helpers shared by components and the kinematic model.

Frame convention: right-handed, y up, x along the main flow (PLAN §4). ``rotation_deg`` is a
rotation about +y by the right-hand rule, which is also three.js ``Object3D.rotation.y``. Seen from
above, positive angles turn local +x toward world −z.
"""

from __future__ import annotations

import math

Vec3 = tuple[float, float, float]


def rotate_y(v: Vec3, degrees: float) -> Vec3:
    """Rotate a vector about +y."""
    if degrees == 0:
        return v
    r = math.radians(degrees)
    c, s = math.cos(r), math.sin(r)
    return (v[0] * c + v[2] * s, v[1], -v[0] * s + v[2] * c)


def to_world(origin: Vec3, degrees: float, local: Vec3) -> Vec3:
    """Transform a point from a component's local frame to the world frame."""
    w = rotate_y(local, degrees)
    return (origin[0] + w[0], origin[1] + w[1], origin[2] + w[2])


def to_local(origin: Vec3, degrees: float, world: Vec3) -> Vec3:
    """Transform a world point into a component's local frame."""
    d = (world[0] - origin[0], world[1] - origin[1], world[2] - origin[2])
    return rotate_y(d, -degrees)


def segment_hits_aabb(p0: Vec3, p1: Vec3, lo: Vec3, hi: Vec3) -> bool:
    """True if the segment p0→p1 touches the axis-aligned box [lo, hi] (slab method)."""
    t_min, t_max = 0.0, 1.0
    for axis in range(3):
        d = p1[axis] - p0[axis]
        if abs(d) < 1e-12:
            if p0[axis] < lo[axis] or p0[axis] > hi[axis]:
                return False
            continue
        t0 = (lo[axis] - p0[axis]) / d
        t1 = (hi[axis] - p0[axis]) / d
        if t0 > t1:
            t0, t1 = t1, t0
        t_min, t_max = max(t_min, t0), min(t_max, t1)
        if t_min > t_max:
            return False
    return True
