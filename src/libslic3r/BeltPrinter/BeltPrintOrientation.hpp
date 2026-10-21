#ifndef slic3r_BeltPrinter_BeltPrintOrientation_hpp_
#define slic3r_BeltPrinter_BeltPrintOrientation_hpp_

#include "libslic3r/Point.hpp"
#include "libslic3r/BoundingBox.hpp"
#include "libslic3r/Model.hpp"

#include <cmath>
#include <cstdlib>
#include <limits>

namespace Slic3r {

// Belt feed direction is model +Y after placement: Y_min enters the belt first.
// Toe-first is the default for every material. Heel-first is opt-in
// (VERTEXSLICE_BELT_HEEL_FIRST=1). The insole mesh is longest along +X, and the
// toe is the min-X end, so toe-first is +90° about Z: (x, y) -> (-y, x).
// Heel-first is -90°: (x, y) -> (y, -x).
// The gantry span is X 0-250 mm. The part's X midpoint is the center, 125 mm.

inline constexpr double belt_bed_center_x_mm = 125.0;
inline constexpr double belt_bed_center_x_tolerance_mm = 0.5;

inline double belt_feed_yaw_rad(bool heel_first)
{
    return heel_first ? -M_PI / 2.0 : M_PI / 2.0;
}

inline Vec3d belt_rotate_z(const Vec3d &p, double yaw_rad)
{
    const double c = std::cos(yaw_rad);
    const double s = std::sin(yaw_rad);
    return Vec3d(c * p.x() - s * p.y(), s * p.x() + c * p.y(), p.z());
}

struct BeltPartPlacement {
    double yaw_rad = belt_feed_yaw_rad(false);
    bool   heel_first = false;
    // Added to a Z-rotated raw-mesh point. X centers the part, Y and Z sit on 0.
    Vec3d  translation = Vec3d::Zero();
};

inline BeltPartPlacement belt_place_raw_box(const BoundingBoxf3 &raw, bool heel_first)
{
    BeltPartPlacement out;
    out.heel_first = heel_first;
    out.yaw_rad    = belt_feed_yaw_rad(heel_first);

    const Vec3d corners[8] = {
        Vec3d(raw.min.x(), raw.min.y(), raw.min.z()),
        Vec3d(raw.max.x(), raw.min.y(), raw.min.z()),
        Vec3d(raw.min.x(), raw.max.y(), raw.min.z()),
        Vec3d(raw.max.x(), raw.max.y(), raw.min.z()),
        Vec3d(raw.min.x(), raw.min.y(), raw.max.z()),
        Vec3d(raw.max.x(), raw.min.y(), raw.max.z()),
        Vec3d(raw.min.x(), raw.max.y(), raw.max.z()),
        Vec3d(raw.max.x(), raw.max.y(), raw.max.z()),
    };
    Vec3d mn(std::numeric_limits<double>::infinity(),
             std::numeric_limits<double>::infinity(),
             std::numeric_limits<double>::infinity());
    Vec3d mx(-std::numeric_limits<double>::infinity(),
             -std::numeric_limits<double>::infinity(),
             -std::numeric_limits<double>::infinity());
    for (const Vec3d &corner : corners) {
        const Vec3d q = belt_rotate_z(corner, out.yaw_rad);
        mn = mn.cwiseMin(q);
        mx = mx.cwiseMax(q);
    }
    const double mid_x = 0.5 * (mn.x() + mx.x());
    out.translation = Vec3d(belt_bed_center_x_mm - mid_x, -mn.y(), -mn.z());
    return out;
}

inline Vec3d belt_placed_point(const Vec3d &raw, const BeltPartPlacement &place)
{
    return belt_rotate_z(raw, place.yaw_rad) + place.translation;
}

// Absolute, so a second call does not rotate the mesh again. Heel-first is
// opt-in: VERTEXSLICE_BELT_HEEL_FIRST=1. Otherwise the toe (min X) prints first.
inline void apply_belt_feed_orientation(ModelObject &model_object)
{
    const char *heel_env = std::getenv("VERTEXSLICE_BELT_HEEL_FIRST");
    const bool heel_first = heel_env != nullptr && heel_env[0] != '\0' && heel_env[0] != '0';

    BoundingBoxf3 raw;
    bool any = false;
    for (const ModelVolume *volume : model_object.volumes) {
        if (!volume->is_model_part())
            continue;
        BoundingBoxf3 part = volume->mesh().bounding_box();
        part.translate(volume->source.mesh_offset);
        raw.merge(part);
        any = true;
    }
    if (!any || !raw.defined)
        return;

    const BeltPartPlacement place = belt_place_raw_box(raw, heel_first);
    const double c = std::cos(place.yaw_rad);
    const double s = std::sin(place.yaw_rad);
    for (ModelVolume *volume : model_object.volumes) {
        if (!volume->is_model_part())
            continue;
        const Vec3d origin = volume->source.mesh_offset;
        const Vec3d rotated_origin(c * origin.x() - s * origin.y(),
                                   s * origin.x() + c * origin.y(),
                                   origin.z());
        // Set the full yaw. set_rotation(Axis, angle) re-extracts Euler angles,
        // and Eigen's [0, pi] range turns a stored -90 deg into +90 deg on the
        // second apply, so heel-first collapsed to toe-first.
        volume->set_rotation(Vec3d(0.0, 0.0, place.yaw_rad));
        volume->set_offset(rotated_origin + place.translation);
    }
    for (ModelInstance *instance : model_object.instances) {
        instance->set_rotation(Vec3d::Zero());
        instance->set_offset(Vec3d::Zero());
    }
    model_object.invalidate_bounding_box();
}

} // namespace Slic3r

#endif
