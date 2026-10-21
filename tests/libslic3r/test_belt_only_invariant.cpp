#include <catch2/catch_all.hpp>

#include "libslic3r/BeltPrinter/BeltOnlyInvariant.hpp"
#include "libslic3r/BeltPrinter/BeltMachineGcode.hpp"
#include "libslic3r/BeltPrinter/BeltPrintOrientation.hpp"
#include "libslic3r/BeltTransform.hpp"
#include "libslic3r/ExPolygon.hpp"
#include "libslic3r/Surface.hpp"

#include <cmath>
#include <fstream>
#include <string>

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

TEST_CASE("Toe-first is the default belt orientation and the part is centered on X", "[BeltOnly]")
{
    // insole_round_2p5mm.stl, unrotated.
    BoundingBoxf3 raw;
    raw.min = Vec3d(-0.706, -46.344, 0.0);
    raw.max = Vec3d(272.415, 46.354, 25.24);

    const BeltPartPlacement toe = belt_place_raw_box(raw, false);
    REQUIRE(near(toe.yaw_rad, -M_PI / 2.0, 1e-12));
    REQUIRE_FALSE(toe.heel_first);

    // -90° Z puts max X (the forefoot) at min Y, so that end enters the belt first.
    const Vec3d low_x = belt_placed_point(Vec3d(raw.min.x(), 0.0, 0.0), toe);
    const Vec3d high_x = belt_placed_point(Vec3d(raw.max.x(), 0.0, 0.0), toe);
    REQUIRE(high_x.y() < low_x.y());
    REQUIRE(near(high_x.y(), 0.0, 1e-6));

    const Vec3d x_lo = belt_placed_point(Vec3d(0.0, raw.min.y(), 0.0), toe);
    const Vec3d x_hi = belt_placed_point(Vec3d(0.0, raw.max.y(), 0.0), toe);
    const double x_mid = 0.5 * (std::min(x_lo.x(), x_hi.x()) + std::max(x_lo.x(), x_hi.x()));
    REQUIRE(near(x_mid, belt_bed_center_x_mm, belt_bed_center_x_tolerance_mm));
    REQUIRE(x_lo.x() > 0.0);
    REQUIRE(x_hi.x() < 250.0);

    const BeltPartPlacement heel = belt_place_raw_box(raw, true);
    REQUIRE(near(heel.yaw_rad, M_PI / 2.0, 1e-12));
    const Vec3d heel_first_end = belt_placed_point(Vec3d(raw.min.x(), 0.0, 0.0), heel);
    const Vec3d forefoot_later = belt_placed_point(Vec3d(raw.max.x(), 0.0, 0.0), heel);
    REQUIRE(heel_first_end.y() < forefoot_later.y());
    REQUIRE(near(heel_first_end.y(), 0.0, 1e-6));
}

TEST_CASE("Toe-first feeds the wide forefoot before the narrow end", "[BeltOnly]")
{
    // Width stations of insole_round_2p5mm.stl along raw X. The ball is the
    // wide station near X=180. The entering 20% of a toe-first feed is wider
    // than the leaving 20%.
    struct Station { double x; double width; };
    const Station stations[] = {
        {2.7, 42.5}, {16.4, 64.1}, {30.0, 71.3}, {43.7, 74.1}, {57.3, 75.4},
        {98.3, 76.8}, {139.3, 84.7}, {166.6, 91.4}, {180.2, 92.3}, {193.9, 91.3},
        {207.5, 88.0}, {221.2, 82.6}, {234.8, 75.4}, {248.5, 66.0}, {262.2, 52.4},
    };
    auto max_width = [](const BeltPartPlacement &place, bool first_band) {
        double y_min = std::numeric_limits<double>::infinity();
        double y_max = -y_min;
        double feed[15];
        for (size_t i = 0; i < 15; ++i) {
            feed[i] = belt_placed_point(Vec3d(stations[i].x, 0.0, 0.0), place).y();
            y_min = std::min(y_min, feed[i]);
            y_max = std::max(y_max, feed[i]);
        }
        const double span = y_max - y_min;
        const double lo = first_band ? y_min : y_min + 0.8 * span;
        const double hi = first_band ? y_min + 0.2 * span : y_max;
        double best = 0.0;
        for (size_t i = 0; i < 15; ++i) {
            if (feed[i] + 1e-6 < lo || feed[i] - 1e-6 > hi)
                continue;
            const Vec3d a = belt_placed_point(Vec3d(stations[i].x, -0.5 * stations[i].width, 0.0), place);
            const Vec3d b = belt_placed_point(Vec3d(stations[i].x, 0.5 * stations[i].width, 0.0), place);
            best = std::max(best, std::abs(a.x() - b.x()));
        }
        return best;
    };

    const BoundingBoxf3 raw = [] {
        BoundingBoxf3 box;
        box.min = Vec3d(-0.706, -46.344, 0.0);
        box.max = Vec3d(272.415, 46.354, 25.24);
        return box;
    }();
    const BeltPartPlacement toe = belt_place_raw_box(raw, false);
    const double toe_first = max_width(toe, true);
    const double toe_last = max_width(toe, false);
    REQUIRE(toe_first > toe_last);
    REQUIRE(toe_first > 80.0);
    REQUIRE(toe_last < 80.0);

    const BeltPartPlacement heel = belt_place_raw_box(raw, true);
    REQUIRE(max_width(heel, true) < max_width(heel, false));
}

TEST_CASE("PRINT_START is before the first extrusion and PRINT_END is the last command", "[BeltOnly]")
{
    const std::string start = belt_machine_start_gcode(260);
    const std::string end = belt_machine_end_gcode();
    const std::string gcode = start + "G1 X125 Y10 E0.20 F1000\n" + end;

    const auto print_start = gcode.find("PRINT_START");
    const auto print_end = gcode.rfind("PRINT_END");
    const auto first_extrude = gcode.find("G1 X125 Y10 E0.20");
    REQUIRE(print_start != std::string::npos);
    REQUIRE(first_extrude != std::string::npos);
    REQUIRE(print_start < first_extrude);
    REQUIRE(print_end != std::string::npos);
    REQUIRE(first_extrude < print_end);
    const auto after_end = gcode.find_first_not_of(" \t\r\n", print_end + std::string("PRINT_END").size());
    REQUIRE(after_end == std::string::npos);

    REQUIRE(start.find("M104 S260") != std::string::npos);
    REQUIRE(start.find("M109 S260") != std::string::npos);
    REQUIRE(start.find("M105") != std::string::npos);
    REQUIRE(start.find("SET_PRESSURE_ADVANCE ADVANCE=0.05") != std::string::npos);
    REQUIRE(start.find("M140") == std::string::npos);
    REQUIRE(end.find("M140 S35") == std::string::npos);
    REQUIRE(end.find("M140 S0") != std::string::npos);
    REQUIRE(belt_machine_start_gcode(225).find("M104 S225") != std::string::npos);
    REQUIRE(belt_machine_start_gcode(225).find("M109 S225") != std::string::npos);
}

TEST_CASE("Cura first-layer Y and Z, toe-first, X center, and machine tokens", "[BeltOnly]")
{
    // Cura ApexLight starts the first extrusion at Y 0.016. Shifting the
    // contact (previously ~0.82) lands on that line, and a 0.04 mm rise
    // along the first segment stays inside the ±0.05 mm band.
    const double cura_y = BELT_CURA_FIRST_Y_MM;
    REQUIRE(std::abs(0.82 + belt_y_offset_to_cura(0.82) - cura_y) <= 0.05);
    const double contact = 0.777;
    const double first_dest = 0.817;
    REQUIRE(std::abs(first_dest + belt_y_offset_to_cura(contact) - cura_y) <= 0.05);
    REQUIRE(near(contact + belt_y_offset_to_cura(contact), cura_y, 1e-12));

    // First-layer Z and the layer step are Cura's 0.65 for a 0.46 mm layer.
    REQUIRE(near(belt_layer_machine_z(0, 0.46), 0.65, 1e-12));
    REQUIRE(near(belt_layer_machine_z(1, 0.46), 1.30, 1e-12));
    REQUIRE(near(belt_layer_machine_z(2, 0.46) - belt_layer_machine_z(1, 0.46), 0.65, 1e-12));
    REQUIRE(near(belt_layer_machine_z(15, 0.46), 0.65 * 16.0, 1e-12));

    BoundingBoxf3 raw;
    raw.min = Vec3d(-0.706, -46.344, 0.0);
    raw.max = Vec3d(272.415, 46.354, 25.24);
    const BeltPartPlacement toe = belt_place_raw_box(raw, false);
    REQUIRE_FALSE(toe.heel_first);
    REQUIRE(near(toe.yaw_rad, -M_PI / 2.0, 1e-12));
    const Vec3d forefoot = belt_placed_point(Vec3d(raw.max.x(), 0.0, 0.0), toe);
    const Vec3d other_end = belt_placed_point(Vec3d(raw.min.x(), 0.0, 0.0), toe);
    REQUIRE(forefoot.y() < other_end.y());
    REQUIRE(near(forefoot.y(), 0.0, 1e-6));
    const Vec3d x_lo = belt_placed_point(Vec3d(0.0, raw.min.y(), 0.0), toe);
    const Vec3d x_hi = belt_placed_point(Vec3d(0.0, raw.max.y(), 0.0), toe);
    const double x_mid = 0.5 * (std::min(x_lo.x(), x_hi.x()) + std::max(x_lo.x(), x_hi.x()));
    REQUIRE(std::abs(x_mid - belt_bed_center_x_mm) <= 1.0);
    REQUIRE(x_lo.x() > 0.0);
    REQUIRE(x_hi.x() < 250.0);

    const std::string light = belt_machine_start_gcode(260);
    const std::string core = belt_machine_start_gcode(225);
    const std::string end = belt_machine_end_gcode();
    REQUIRE(light.find("PRINT_START") != std::string::npos);
    REQUIRE(light.find("M109 S260") != std::string::npos);
    REQUIRE(light.find("SET_PRESSURE_ADVANCE") != std::string::npos);
    REQUIRE(core.find("M109 S225") != std::string::npos);
    REQUIRE(core.find("SET_PRESSURE_ADVANCE ADVANCE=0.05") != std::string::npos);
    REQUIRE(end.find("PRINT_END") != std::string::npos);
}

TEST_CASE("Belt narrow solid surfaces stay sparse infill in G-code typing", "[BeltOnly]")
{
    REQUIRE(BeltPrinter::is_belt_narrow_solid_surface(
        stInternal, BeltPrinter::BELT_NARROW_SOLID_SURFACE_ANGLE, true));
    REQUIRE_FALSE(BeltPrinter::is_belt_narrow_solid_surface(
        stInternalSolid, BeltPrinter::BELT_NARROW_SOLID_SURFACE_ANGLE, true));
    REQUIRE_FALSE(BeltPrinter::is_belt_narrow_solid_surface(
        stInternal, BeltPrinter::BELT_NARROW_SOLID_SURFACE_ANGLE, false));
    REQUIRE_FALSE(BeltPrinter::is_belt_narrow_solid_surface(stInternal, -1.0, true));
}

static bool fixture_contains(const char *rel_path, const char *needle)
{
    const std::string path = std::string(TEST_DATA_DIR) + "/../fixtures/belt_profiles/" + rel_path;
    std::ifstream in(path);
    if (!in)
        return false;
    std::string body((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
    return body.find(needle) != std::string::npos;
}

TEST_CASE("ApexLight and ApexCore belt process presets match TPU expert infill keys", "[BeltOnly]")
{
    for (const char *file : {"ApexLight_belt_process.json", "ApexCore_belt_process.json"}) {
        REQUIRE(fixture_contains(file, "\"resolution\": \"0.05\""));
        REQUIRE(fixture_contains(file, "\"enable_arc_fitting\": \"0\""));
        REQUIRE(fixture_contains(file, "\"infill_anchor\": \"400%\""));
        REQUIRE(fixture_contains(file, "\"infill_anchor_max\": \"20\""));
        REQUIRE(fixture_contains(file, "\"infill_combination\": \"0\""));
        REQUIRE(fixture_contains(file, "\"minimum_sparse_infill_area\": \"0\""));
        REQUIRE(fixture_contains(file, "\"detect_narrow_internal_solid_infill\": \"0\""));
        REQUIRE(fixture_contains(file, "\"reduce_infill_retraction\": \"1\""));
        REQUIRE(fixture_contains(file, "\"internal_solid_infill_pattern\": \"rectilinear\""));
        REQUIRE(fixture_contains(file, "\"filter_out_gap_fill\": \"9999\""));
        REQUIRE(fixture_contains(file, "\"travel_speed\": \"212\""));
        REQUIRE(fixture_contains(file, "\"wall_loops\": \"2\""));
        REQUIRE(fixture_contains(file, "\"wall_generator\": \"arachne\""));
    }
    REQUIRE(fixture_contains("ApexLight_belt_process.json", "\"layer_height\": \"0.46\""));
    REQUIRE(fixture_contains("ApexCore_belt_process.json", "\"layer_height\": \"0.42\""));
}
