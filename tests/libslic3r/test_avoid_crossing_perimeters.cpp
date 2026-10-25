#include <catch2/catch_all.hpp>

#include "libslic3r/GCode/AvoidCrossingPerimeters.hpp"
#include "libslic3r/ClipperUtils.hpp"
#include "libslic3r/ExPolygon.hpp"

#include <algorithm>
#include <cmath>
#include <vector>

using namespace Slic3r;

namespace {

Point mm(double x, double y)
{
    return Point::new_scale(x, y);
}

double mm_of(coord_t v)
{
    return unscale_(v);
}

ExPolygon rect(double x0, double y0, double x1, double y1)
{
    ExPolygon ex;
    ex.contour = {mm(x0, y0), mm(x1, y0), mm(x1, y1), mm(x0, y1)};
    if (!ex.contour.is_counter_clockwise())
        ex.contour.reverse();
    return ex;
}

Polygon circle_poly(double cx, double cy, double radius, int n, bool ccw)
{
    Polygon poly;
    const double pi = std::acos(-1.0);
    poly.points.reserve(size_t(n));
    for (int i = 0; i < n; ++i) {
        const double a = 2.0 * pi * double(i) / double(n);
        poly.points.push_back(mm(cx + radius * std::cos(a), cy + radius * std::sin(a)));
    }
    if (ccw != poly.is_counter_clockwise())
        poly.reverse();
    return poly;
}

ExPolygon u_shape(double arm_top)
{
    ExPolygon u;
    u.contour = {mm(0, 0), mm(30, 0), mm(30, arm_top), mm(20, arm_top), mm(20, 8), mm(10, 8), mm(10, arm_top), mm(0, arm_top)};
    if (!u.contour.is_counter_clockwise())
        u.contour.reverse();
    return u;
}

bool in_u_gap(const Point &pt)
{
    const double x = mm_of(pt.x());
    const double y = mm_of(pt.y());
    return x > 10.05 && x < 19.95 && y > 8.05;
}

ExPolygons comb_for_strip(double length_mm, double width_mm)
{
    // Two walls: outer half-width 0.21 mm + 0.45 mm spacing → inner centreline inset 0.66 mm.
    // build_comb_region then shrinks by 0.5 * 0.45 mm inner-wall width.
    const double inset = 0.66;
    if (width_mm <= 2.0 * inset || length_mm <= 2.0 * inset)
        return {};
    const ExPolygon inner = rect(inset, inset, length_mm - inset, width_mm - inset);
    return AvoidCrossingPerimeters::build_comb_region({inner}, scale_(0.5 * 0.45), {}, 0., false, {}, 0.);
}

} // namespace

TEST_CASE("Combing fallback flag off is identical", "[AvoidCrossingPerimeters]")
{
    const ExPolygons comb{rect(0, 0, 20, 10)};
    const Polyline   original{mm(1, 1), mm(15, 8)};
    AvoidCrossingPerimeters avoid;
    const Polyline out = avoid.apply_comb_fallback(false, comb, original, 4, nullptr);
    REQUIRE(out.points == original.points);
    REQUIRE(avoid.comb_log().empty());
}

TEST_CASE("Combing fallback thin strips with two walls", "[AvoidCrossingPerimeters]")
{
    for (double width : {2.0, 3.0}) {
        DYNAMIC_SECTION("strip width " << width << " mm") {
            const double length = 30.0;
            const ExPolygons comb = comb_for_strip(length, width);
            REQUIRE(comb.size() == 1);

            const ExPolygons slice{rect(0, 0, length, width)};
            const ExPolygons safe = union_ex(offset_ex(slice, -float(scale_(0.5))));
            REQUIRE_FALSE(safe.empty());

            // A travel in the outer 0.5 mm band must be lifted, not drawn on the surface.
            const Polyline band{mm(2, 0.25), mm(length - 2, 0.25)};
            AvoidCrossingPerimeters avoid;
            const Polyline lifted = avoid.apply_comb_fallback(true, comb, band, 1, nullptr, &safe);
            REQUIRE(lifted.points == band.points);
            REQUIRE(avoid.island_lift_pending());
            REQUIRE_FALSE(avoid.comb_log().empty());
            REQUIRE(avoid.comb_log().front().lifted);

            // A travel that already sits inside the wall is kept on the surface.
            const Polyline inner{mm(2, width * 0.5), mm(length - 2, width * 0.5)};
            AvoidCrossingPerimeters avoid_inner;
            const Polyline kept = avoid_inner.apply_comb_fallback(true, comb, inner, 1, nullptr, &safe);
            REQUIRE_FALSE(avoid_inner.island_lift_pending());
            REQUIRE(kept.points == inner.points);
        }
    }
}

TEST_CASE("Combing fallback collapsed thin strip follows the centerline", "[AvoidCrossingPerimeters]")
{
    // 1.2 mm strip. A 1.5x 0.42 mm outline shrink (0.63 mm) collapses, and a straight
    // move at y=0.2 rides the outer 0.5 mm band. The deepest inset that still exists
    // is the strip centerline, past that band.
    const ExPolygons slices{rect(0, 0, 30, 1.2)};
    const ExPolygons comb = AvoidCrossingPerimeters::build_comb_region({}, 0., slices, scale_(1.5 * 0.42), false, {}, 0.);
    REQUIRE(comb.size() == 1);
    {
        double min_y = 1e9;
        double max_y = -1e9;
        for (const Point &pt : comb.front().contour.points) {
            min_y = std::min(min_y, mm_of(pt.y()));
            max_y = std::max(max_y, mm_of(pt.y()));
        }
        REQUIRE(min_y > 0.50);
        REQUIRE(max_y < 0.70);
    }

    const ExPolygons safe = union_ex(offset_ex(slices, -float(scale_(0.5))));
    REQUIRE_FALSE(safe.empty());

    // Endpoints in the outer band make the whole polyline illegal as a surface travel.
    const Polyline band{mm(1, 0.2), mm(29, 0.2)};
    AvoidCrossingPerimeters avoid;
    const Polyline lifted = avoid.apply_comb_fallback(true, comb, band, 2, nullptr, &safe);
    REQUIRE(lifted.points == band.points);
    REQUIRE(avoid.island_lift_pending());
    REQUIRE(avoid.comb_log().front().lifted);

    // The centerline itself is inside the band-free core and can be traveled.
    const Polyline core{mm(2, 0.6), mm(28, 0.6)};
    AvoidCrossingPerimeters avoid_core;
    const Polyline out = avoid_core.apply_comb_fallback(true, comb, core, 2, nullptr, &safe);
    REQUIRE_FALSE(avoid_core.island_lift_pending());
    for (const Point &pt : out.points) {
        const double y = mm_of(pt.y());
        REQUIRE(y > 0.50);
        REQUIRE(y < 0.70);
        REQUIRE(slices.front().contains(pt));
    }
}

TEST_CASE("Combing fallback U-shape routes inside the slice", "[AvoidCrossingPerimeters]")
{
    const ExPolygons comb{u_shape(20)};
    const Polyline   original{mm(4, 16), mm(26, 16)};
    AvoidCrossingPerimeters avoid;
    const Polyline out = avoid.apply_comb_fallback(true, comb, original, 5, nullptr);

    REQUIRE(out.points.front() == original.points.front());
    REQUIRE(out.points.back() == original.points.back());
    REQUIRE(out.length() > original.length());
    bool went_under_notch = false;
    for (const Point &pt : out.points) {
        REQUIRE_FALSE(in_u_gap(pt));
        REQUIRE(comb.front().contains(pt));
        if (mm_of(pt.y()) < 8.05)
            went_under_notch = true;
    }
    REQUIRE(went_under_notch);
    REQUIRE(avoid.comb_log().empty());
}

TEST_CASE("Combing fallback ring with a hole takes the shorter direction", "[AvoidCrossingPerimeters]")
{
    ExPolygon ring;
    ring.contour = circle_poly(0, 0, 20, 96, true);
    ring.holes.push_back(circle_poly(0, 0, 6, 96, false));
    const ExPolygons comb{ring};

    const double pi = std::acos(-1.0);
    auto polar = [&](double deg) {
        const double a = deg * pi / 180.0;
        return mm(7.0 * std::cos(a), 7.0 * std::sin(a));
    };
    const Polyline original{polar(-40), polar(40)};
    AvoidCrossingPerimeters avoid;
    const Polyline out = avoid.apply_comb_fallback(true, comb, original, 6, nullptr);

    REQUIRE(out.points.front() == original.points.front());
    REQUIRE(out.points.back() == original.points.back());
    // Short hole arc is about 8 mm; the long way around the hole is about 29 mm.
    REQUIRE(out.length() < scale_(18.0));
    REQUIRE(out.length() > original.length());
    for (const Point &pt : out.points) {
        const double radius = std::hypot(mm_of(pt.x()), mm_of(pt.y()));
        REQUIRE(radius > 5.7);
        REQUIRE(radius < 12.0);
    }
    REQUIRE(avoid.comb_log().empty());
}

TEST_CASE("Combing fallback respects a top-surface cut-out", "[AvoidCrossingPerimeters]")
{
    const ExPolygon body = rect(0, 0, 30, 20);
    const ExPolygon top  = rect(10, 5, 20, 15);
    const ExPolygons comb = AvoidCrossingPerimeters::build_comb_region({body}, scale_(0.01), {}, 0., false, {top}, scale_(0.3));
    REQUIRE(comb.size() == 1);
    REQUIRE_FALSE(comb.front().holes.empty());

    const Polyline original{mm(2, 10), mm(28, 10)};
    AvoidCrossingPerimeters avoid;
    const Polyline out = avoid.apply_comb_fallback(true, comb, original, 7, nullptr);
    REQUIRE(out.size() > 2);
    REQUIRE(out.points.front() == original.points.front());
    REQUIRE(out.points.back() == original.points.back());
    for (const Point &pt : out.points) {
        const double x = mm_of(pt.x());
        const double y = mm_of(pt.y());
        const bool in_cut = x > 11.0 && x < 19.0 && y > 6.0 && y < 14.0;
        REQUIRE_FALSE(in_cut);
    }
}

TEST_CASE("Combing fallback exceeds the 20 mm cap and still routes inside", "[AvoidCrossingPerimeters]")
{
    const ExPolygons comb{u_shape(50)};
    const Polyline   original{mm(4, 40), mm(26, 40)};
    AvoidCrossingPerimeters avoid;
    const Polyline out = avoid.apply_comb_fallback(true, comb, original, 8, nullptr);

    REQUIRE(out.points.front() == original.points.front());
    REQUIRE(out.points.back() == original.points.back());
    REQUIRE(out.size() > 2);
    const double detour_mm = unscale_(out.length() - original.length());
    REQUIRE(detour_mm > 20.0);
    bool saw_cap = false;
    for (const auto &entry : avoid.comb_log()) {
        if (entry.reason == "cap") {
            saw_cap = true;
            REQUIRE(entry.routed);
            REQUIRE(entry.layer_id == 8);
        }
    }
    REQUIRE(saw_cap);
    for (const Point &pt : out.points)
        REQUIRE_FALSE(in_u_gap(pt));
}

TEST_CASE("Combing fallback different islands stay straight and are logged", "[AvoidCrossingPerimeters]")
{
    const ExPolygons comb{rect(0, 0, 10, 10), rect(20, 0, 30, 10)};
    const Polyline   original{mm(5, 5), mm(25, 5)};
    AvoidCrossingPerimeters avoid;
    const Polyline out = avoid.apply_comb_fallback(true, comb, original, 9, nullptr);
    // XY stays straight. The caller retracts and lifts (z-hop / belt normal) instead of
    // dragging that straight move through the wall band.
    REQUIRE(out.points == original.points);
    REQUIRE(avoid.island_lift_pending());
    REQUIRE(avoid.comb_log().size() == 1);
    REQUIRE(avoid.comb_log().front().reason == "island");
    REQUIRE(avoid.comb_log().front().routed);
    REQUIRE(avoid.comb_log().front().lifted);
    REQUIRE(avoid.comb_log().front().layer_id == 9);
}

TEST_CASE("Combing fallback empty hole after an island lift does not crash", "[AvoidCrossingPerimeters]")
{
    // v3 died on the travel after "COMB_LIFT reason=island layer=214".
    // projection_onto() of an empty hole calls first_point() and segfaults, and a
    // line index past the ring used to walk forever.
    const ExPolygons islands{rect(0, 0, 10, 10), rect(30, 0, 40, 10)};
    const Polyline   hop{mm(36.313, 5), mm(-3.515, 5)};
    AvoidCrossingPerimeters avoid;
    const Polyline lifted = avoid.apply_comb_fallback(true, islands, hop, 214, nullptr, &islands);
    REQUIRE(avoid.island_lift_pending());
    REQUIRE(lifted.points == hop.points);
    REQUIRE(avoid.comb_log().front().reason == "island");
    REQUIRE(avoid.comb_log().front().layer_id == 214);
    REQUIRE(avoid.consume_island_lift());

    ExPolygon bad = rect(0, 0, 80, 20);
    bad.holes.emplace_back();
    const ExPolygons comb{bad};
    const ExPolygons safe{rect(0, 0, 80, 20)};
    // Start outside the island so locate has to project onto the empty hole.
    const Polyline across{mm(-4, 10), mm(40, 10)};
    const Polyline out = avoid.apply_comb_fallback(true, comb, across, 214, nullptr, &safe);
    REQUIRE(out.size() >= 2);
    if (avoid.island_lift_pending()) {
        REQUIRE(out.points == across.points);
        REQUIRE(avoid.comb_log().back().lifted);
    } else {
        for (const Point &pt : out.points)
            REQUIRE(safe.front().contains(pt));
    }

    // Duplicate vertices: the inward normal is zero and must not be cast from NaN.
    ExPolygon spike;
    spike.contour.points = {mm(0, 0), mm(10, 0), mm(10, 0), mm(10, 5), mm(0, 5)};
    const ExPolygons spike_comb{spike};
    const Polyline   spike_travel{mm(1, 1), mm(8, 4)};
    const Polyline   spike_out = avoid.apply_comb_fallback(true, spike_comb, spike_travel, 214, nullptr, &spike_comb);
    REQUIRE(spike_out.size() >= 2);
}

TEST_CASE("Combing fallback thin heel lifts band travels and island hops", "[AvoidCrossingPerimeters]")
{
    // Synthetic heel: a 1.2 mm sole edge plus a disconnected upper. The sole's
    // centerline survives; travels in the outer band and hops between islands lift.
    const ExPolygons part{rect(0, 0, 40, 1.2), rect(0, 8, 14, 18)};
    const ExPolygons safe = union_ex(offset_ex(part, -float(scale_(0.5))));
    const ExPolygons comb = AvoidCrossingPerimeters::build_comb_region({}, 0., part, scale_(1.5 * 0.42), false, {}, 0.);
    REQUIRE(comb.size() >= 2);
    REQUIRE_FALSE(safe.empty());

    {
        const Polyline band{mm(2, 0.2), mm(36, 0.2)};
        AvoidCrossingPerimeters avoid;
        const Polyline out = avoid.apply_comb_fallback(true, comb, band, 130, nullptr, &safe);
        REQUIRE(avoid.island_lift_pending());
        REQUIRE(out.points == band.points);
        REQUIRE(avoid.comb_log().front().lifted);
        REQUIRE(avoid.comb_log().front().layer_id == 130);
    }

    {
        // Same shape as the crashing v3 hop: one point on each island.
        const Polyline hop{mm(36.313, 0.6), mm(6.0, 12.0)};
        AvoidCrossingPerimeters avoid;
        const Polyline out = avoid.apply_comb_fallback(true, comb, hop, 214, nullptr, &safe);
        REQUIRE(avoid.island_lift_pending());
        REQUIRE(out.points == hop.points);
        REQUIRE(avoid.comb_log().front().reason == "island");
        REQUIRE(avoid.comb_log().front().lifted);
        REQUIRE(avoid.comb_log().front().layer_id == 214);
    }

    {
        const Polyline inner{mm(2, 12), mm(10, 12)};
        AvoidCrossingPerimeters avoid;
        const Polyline out = avoid.apply_comb_fallback(true, comb, inner, 215, nullptr, &safe);
        REQUIRE_FALSE(avoid.island_lift_pending());
        const Polylines outside = diff_pl(Polylines{out}, safe);
        double out_len = 0.;
        for (const Polyline &piece : outside)
            out_len += piece.length();
        REQUIRE(out_len <= scale_(0.05));
    }
}

TEST_CASE("Combing fallback a detour that leaves the part is lifted and reported", "[AvoidCrossingPerimeters]")
{
    const ExPolygon  part = u_shape(50);
    const ExPolygons safe = union_ex(offset_ex(ExPolygons{part}, -float(scale_(0.5))));
    // The comb fills the notch, so a boundary walk can cut the gap or leave the part.
    const ExPolygons comb{rect(0, 0, 30, 50)};
    const Polyline   original{mm(4, 40), mm(26, 40)};
    AvoidCrossingPerimeters avoid;
    const Polyline out = avoid.apply_comb_fallback(true, comb, original, 130, nullptr, &safe);

    if (avoid.island_lift_pending()) {
        REQUIRE(out.points == original.points);
        REQUIRE_FALSE(avoid.comb_log().empty());
        const auto &entry = avoid.comb_log().front();
        REQUIRE(entry.lifted);
        REQUIRE(entry.layer_id == 130);
        const bool reported = entry.reason == "cap" || entry.reason == "outside" || entry.reason == "collapsed";
        REQUIRE(reported);
        if (entry.reason == "cap")
            REQUIRE(entry.detour_mm > 20.0);
    } else {
        for (const Point &pt : out.points) {
            REQUIRE_FALSE(in_u_gap(pt));
            REQUIRE(part.contains(pt));
        }
        const Polylines outside = diff_pl(Polylines{out}, safe);
        double out_len = 0.;
        for (const Polyline &piece : outside)
            out_len += piece.length();
        REQUIRE(out_len <= scale_(0.05));
    }
}

TEST_CASE("Combing fallback deep shrink of a thin U stays one island", "[AvoidCrossingPerimeters]")
{
    // 6 mm shrink splits or erases the U. The shallower inset must stay a single island
    // so the gap crossing walks inside instead of becoming an island hop.
    const ExPolygon  u    = u_shape(20);
    const ExPolygons comb = AvoidCrossingPerimeters::build_comb_region({u}, scale_(6.), {}, 0., false, {}, 0.);
    REQUIRE(comb.size() == 1);
    const ExPolygons safe = union_ex(offset_ex(ExPolygons{u}, -float(scale_(0.5))));

    const Polyline original{mm(4, 16), mm(26, 16)};
    AvoidCrossingPerimeters avoid;
    const Polyline out = avoid.apply_comb_fallback(true, comb, original, 3, nullptr, &safe);
    REQUIRE_FALSE(avoid.island_lift_pending());
    // A deep inset can exceed the 20 mm cap. That is still an inside route, not an island hop.
    for (const auto &entry : avoid.comb_log()) {
        REQUIRE(entry.reason == "cap");
        REQUIRE(entry.routed);
    }
    REQUIRE(out.size() > 2);
    bool went_under_notch = false;
    for (const Point &pt : out.points) {
        REQUIRE_FALSE(in_u_gap(pt));
        if (mm_of(pt.y()) < 8.05)
            went_under_notch = true;
    }
    REQUIRE(went_under_notch);
}

TEST_CASE("Combing fallback boundary walk includes endpoints outside the comb grid", "[AvoidCrossingPerimeters]")
{
    // Both points are within the 6 mm snap limit but outside the comb bbox.
    // The boundary walker must query the original endpoints without indexing
    // outside EdgeGrid. The old code asserts in Debug and reads out of bounds
    // in Release when simplify_travel tests these connectors.
    const ExPolygons comb{rect(0, 0, 80, 20)};
    const ExPolygons safe{rect(-5, -1, 85, 21)};
    const Polyline original{mm(-4, 10), mm(84, 10)};
    AvoidCrossingPerimeters avoid;
    const Polyline out = avoid.apply_comb_fallback(true, comb, original, 214, nullptr, &safe);
    REQUIRE_FALSE(avoid.island_lift_pending());
    REQUIRE(out.points.front() == original.points.front());
    REQUIRE(out.points.back() == original.points.back());
    REQUIRE(diff_pl(Polylines{out}, safe).empty());
}

TEST_CASE("Combing fallback reported v3 endpoints outside the comb lift safely", "[AvoidCrossingPerimeters]")
{
    // Synthetic boundary, using the reported layer/move. This is a regression
    // for the grid precondition, not a substitute for slicing the actual v3.
    const ExPolygons comb{rect(-40, 1, 35, 10)};
    const ExPolygons safe{rect(-39.5, 1.5, 34.5, 9.5)};
    const Polyline original{mm(36.313, 2.721), mm(-43.515, 0.500)};
    AvoidCrossingPerimeters avoid;
    const Polyline out = avoid.apply_comb_fallback(true, comb, original, 214, nullptr, &safe);
    REQUIRE(avoid.island_lift_pending());
    REQUIRE(out.points == original.points);
}

TEST_CASE("Combing fallback rejects even short excursions outside the safe region", "[AvoidCrossingPerimeters]")
{
    const ExPolygons comb{rect(0, 0, 20, 10)};
    SECTION("endpoint lies 0.02 mm into the wall band")
    {
        const ExPolygons safe{rect(0.5, 0.5, 19.5, 9.5)};
        const Polyline original{mm(0.48, 5), mm(18, 5)};
        AvoidCrossingPerimeters avoid;
        const Polyline out = avoid.apply_comb_fallback(true, comb, original, 130, nullptr, &safe);
        REQUIRE(avoid.island_lift_pending());
        REQUIRE(out.points == original.points);
    }
    SECTION("segment leaves safe region although every vertex is inside")
    {
        ExPolygon safe_ring = rect(0.5, 0.5, 19.5, 9.5);
        Polygon hole = rect(9.99, 4, 10.01, 6).contour;
        hole.reverse();
        safe_ring.holes.push_back(std::move(hole));
        const ExPolygons safe{safe_ring};
        const Polyline original{mm(2, 5), mm(18, 5)};
        AvoidCrossingPerimeters avoid;
        const Polyline out = avoid.apply_comb_fallback(true, comb, original, 130, nullptr, &safe);
        REQUIRE(avoid.island_lift_pending());
        REQUIRE(out.points == original.points);
    }
    SECTION("travel on the safe boundary remains on the surface")
    {
        const ExPolygons safe{rect(0.5, 0.5, 19.5, 9.5)};
        const Polyline original{mm(2, 0.5), mm(18, 0.5)};
        AvoidCrossingPerimeters avoid;
        const Polyline out = avoid.apply_comb_fallback(true, comb, original, 130, nullptr, &safe);
        REQUIRE_FALSE(avoid.island_lift_pending());
        REQUIRE(out.points == original.points);
    }
}
