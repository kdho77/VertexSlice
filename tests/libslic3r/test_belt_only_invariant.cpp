#include <catch2/catch_all.hpp>

#include "libslic3r/BeltPrinter/BeltOnlyInvariant.hpp"
#include "libslic3r/BeltTransform.hpp"
#include "libslic3r/ExPolygon.hpp"

#include <cmath>

using namespace Slic3r;
using namespace Slic3r::BeltPrinter;

static bool near(double a, double b, double eps)
{
    return std::abs(a - b) <= eps;
}

static Point mm(double x, double y)
{
    return Point(coord_t(std::llround(scale_(x))), coord_t(std::llround(scale_(y))));
}

static ExPolygon rect_mm(double w, double h)
{
    ExPolygon ex;
    ex.contour.points = {mm(0, 0), mm(w, 0), mm(w, h), mm(0, h)};
    return ex;
}

TEST_CASE("Belt extrusion length is machine XY, and efix is the ratio to the shear term", "[BeltOnly]")
{
    const Vec2d a(0.0, 0.0);
    const Vec2d b(3.0, 4.0);
    const double angle_deg = 45.0;
    const double mach   = machine_xy_length(a, b, angle_deg);
    const double legacy = legacy_shear_extrusion_length(a, b, M_PI / 4.0);

    Vec3d am = BeltTransform::inverse_transform_point(Vec3d(a.x(), a.y(), 0.0), angle_deg);
    Vec3d bm = BeltTransform::inverse_transform_point(Vec3d(b.x(), b.y(), 0.0), angle_deg);
    const double emitted = std::hypot(bm.x() - am.x(), bm.y() - am.y());
    REQUIRE(near(mach, emitted, 1e-9));

    // Zero machine offsets: Y_mach = √2 Y_virt, so length is sqrt(dx^2 + 2 dy^2) = sqrt(41).
    // The shear term is sqrt(dx^2 + 4 dy^2) = sqrt(73). efix multiplies E by
    // hypot(dx_m, dy_m) / sqrt(dx_m^2 + 2 dy_m^2), which is mach/legacy.
    if (near(am.y(), 0.0, 1e-9) && near(bm.y(), 4.0 * std::sqrt(2.0), 1e-6)) {
        REQUIRE(near(mach, std::sqrt(41.0), 1e-6));
        REQUIRE(near(legacy, std::sqrt(73.0), 1e-6));
        const double dxm = 3.0;
        const double dym = 4.0 * std::sqrt(2.0);
        const double efix = std::hypot(dxm, dym) / std::sqrt(dxm * dxm + 2.0 * dym * dym);
        REQUIRE(near(mach / legacy, efix, 1e-9));
    }

    // Pure X is unchanged by the shear. Pure Y is long by exactly √2.
    REQUIRE(near(machine_xy_length(a, Vec2d(10.0, 0.0), angle_deg),
                 legacy_shear_extrusion_length(a, Vec2d(10.0, 0.0), M_PI / 4.0), 1e-9));
    const double mach_y   = machine_xy_length(a, Vec2d(0.0, 4.0), angle_deg);
    const double legacy_y = legacy_shear_extrusion_length(a, Vec2d(0.0, 4.0), M_PI / 4.0);
    REQUIRE(near(legacy_y / mach_y, std::sqrt(2.0), 1e-6));

    // Non-belt printers keep the virtual Euclidean length.
    REQUIRE(near(extrusion_length_for_move(false, a, b, angle_deg), 5.0, 1e-9));
    REQUIRE(near(extrusion_length_for_move(true, a, b, angle_deg), mach, 1e-12));

    // Both endpoints below the belt: the gantry clamp makes the machine Y delta 0.
    const double clamped = machine_xy_length(Vec2d(1.0, -1.0), Vec2d(1.0, -4.0), angle_deg);
    REQUIRE(near(clamped, 0.0, 1e-9));
}

TEST_CASE("Machine-length E per millimetre does not depend on direction", "[BeltOnly]")
{
    const Vec2d origin(0.0, 0.0);
    auto ratio = [&](const Vec2d &delta, bool belt) {
        const double used = extrusion_length_for_move(belt, origin, delta, 45.0);
        const double emit = machine_xy_length(origin, delta, 45.0);
        return used / emit;
    };
    const double horizontal = ratio(Vec2d(10.0, 0.0), true);
    const double diagonal   = ratio(Vec2d(10.0, 10.0), true);
    const double vertical   = ratio(Vec2d(0.0, 10.0), true);
    REQUIRE(near(horizontal, 1.0, 1e-9));
    REQUIRE(near(diagonal, 1.0, 1e-9));
    REQUIRE(near(vertical, 1.0, 1e-9));

    const double legacy_h = legacy_shear_extrusion_length(origin, Vec2d(10.0, 0.0), M_PI / 4.0) /
                            machine_xy_length(origin, Vec2d(10.0, 0.0), 45.0);
    const double legacy_v = legacy_shear_extrusion_length(origin, Vec2d(0.0, 10.0), M_PI / 4.0) /
                            machine_xy_length(origin, Vec2d(0.0, 10.0), 45.0);
    REQUIRE(near(legacy_h, 1.0, 1e-9));
    REQUIRE(std::abs(legacy_v - legacy_h) > 0.2);
}

TEST_CASE("Belt events stay on the contact side or in the interior", "[BeltOnly]")
{
    const WallGeometry wall = make_wall_geometry({rect_mm(40.0, 20.0)}, 0.42);
    REQUIRE(wall.valid);

    const Point bottom = mm(20.0, 0.02);
    const Point top    = mm(20.0, 19.9);
    const Point center = mm(20.0, 10.0);
    const Point side   = mm(0.02, 10.0);
    REQUIRE(nearest_is_belt(bottom, wall));
    REQUIRE(is_belt_contact(bottom, wall));
    REQUIRE_FALSE(is_safe_event(top, wall));
    REQUIRE_FALSE(is_safe_event(side, wall));
    REQUIRE(is_interior(center, wall));
    REQUIRE(is_safe_event(center, wall));

    // safe_mm is measured from the slice and grows with the bead. A point one
    // old-constant (1.85 mm) inside a wide bead is still a non-belt landing.
    const WallGeometry wide = make_wall_geometry({rect_mm(40.0, 20.0)}, 1.03);
    REQUIRE(wide.safe_mm > 1.5 + 0.5 * 1.03);
    REQUIRE_FALSE(is_interior(mm(20.0, 1.9), wide));
    REQUIRE(is_interior(mm(20.0, 8.0), wide));
    // Bottom outer-wall segment is the 20 mm/s belt pass. A side segment is not.
    const double capped = belt_outer_feedrate(mm(5.0, 0.0), mm(15.0, 0.0), 35.0 * 60.0, wide);
    const double side_f = belt_outer_feedrate(mm(0.0, 5.0), mm(0.0, 12.0), 35.0 * 60.0, wide);
    REQUIRE(near(capped, 20.0 * 60.0, 1e-6));
    REQUIRE(near(side_f, 35.0 * 60.0, 1e-6));

    Polyline chord;
    chord.points = {mm(0.0, 15.0), mm(40.0, 15.0)};
    const Polylines planned = plan_belt_polylines(Polylines{chord}, wall);
    REQUIRE_FALSE(planned.empty());
    REQUIRE(is_safe_event(planned.front().first_point(), wall));
    REQUIRE(is_safe_event(planned.front().last_point(), wall));
    REQUIRE(nearest_is_belt(planned.front().first_point(), wall));

    const Point left = mm(20.0, 5.0);
    const Point right = mm(30.0, 5.0);
    const Polyline direct = route_travel(left, right, wall);
    REQUIRE(direct.size() == 2);
    for (const Point &p : direct.points)
        REQUIRE(is_safe_event(p, wall));

    // A notch forces the straight segment outside the part. Waypoints still
    // have to be gate-safe, and the route must not be that straight segment.
    ExPolygon notch;
    notch.contour.points = {mm(0, 0), mm(40, 0), mm(40, 20), mm(30, 20),
                            mm(30, 8), mm(10, 8), mm(10, 20), mm(0, 20)};
    const WallGeometry concave = make_wall_geometry({notch}, 0.42);
    REQUIRE(concave.valid);
    const Point arm_l = mm(5.0, 16.0);
    const Point arm_r = mm(35.0, 16.0);
    REQUIRE(is_safe_event(arm_l, concave));
    REQUIRE(is_safe_event(arm_r, concave));
    const Polyline around = route_travel(arm_l, arm_r, concave);
    REQUIRE(around.size() >= 3);
    for (const Point &p : around.points)
        REQUIRE(is_safe_event(p, concave));
}

TEST_CASE("End-cap layer change stays on the belt contact", "[BeltOnly]")
{
    // The sole still meets the belt. The next slice is only the heel tip, whose
    // lowest point is ~1.5 mm above that contact (the ApexCore L454→L455 miss
    // at Y=4.26). The layer-change anchor is the sole's belt contact.
    const WallGeometry sole = make_wall_geometry({rect_mm(80.0, 12.0)}, 1.0);
    ExPolygon cap;
    cap.contour.points = {mm(70.0, 1.5), mm(84.0, 1.5), mm(84.0, 3.4), mm(70.0, 3.4)};
    const WallGeometry tip = make_wall_geometry({cap}, 1.0);
    REQUIRE(sole.valid);
    REQUIRE(tip.valid);
    REQUIRE(is_end_cap_layer(sole, tip));
    const Point change = layer_change_anchor(sole, tip);
    REQUIRE(is_belt_contact(change, sole));
    REQUIRE_FALSE(is_belt_contact(tip.belt_anchor, sole));
    const double rise = (tip.belt_anchor.y() - sole.belt_anchor.y()) * SCALING_FACTOR;
    REQUIRE(rise > 1.0);
    REQUIRE(change.y() + coord_t(std::llround(scale_(0.5))) < tip.belt_anchor.y());

    // The sole continuing, shifted by a fraction of a millimetre, is not an end
    // cap. The tag follows the new belt contact.
    ExPolygon shifted = rect_mm(80.0, 12.0);
    for (Point &p : shifted.contour.points)
        p.y() += coord_t(std::llround(scale_(0.05)));
    const WallGeometry next_sole = make_wall_geometry({shifted}, 1.0);
    REQUIRE_FALSE(is_end_cap_layer(sole, next_sole));
    const Point followed = layer_change_anchor(sole, next_sole);
    REQUIRE(is_belt_contact(followed, next_sole));
}
