#include <catch2/catch_all.hpp>

#include "libslic3r/GCode/AvoidCrossingPerimeters.hpp"
#include "libslic3r/ExPolygon.hpp"

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

            const ExPolygon slice = rect(0, 0, length, width);
            // Travel sits in the wall band, inside the slice and outside the shrunk inner wall.
            const Polyline original{mm(2, 0.25), mm(length - 2, 0.25)};
            AvoidCrossingPerimeters avoid;
            const Polyline out = avoid.apply_comb_fallback(true, comb, original, 1, nullptr);

            REQUIRE(out.points.front() == original.points.front());
            REQUIRE(out.points.back() == original.points.back());
            REQUIRE(out.size() > 2);
            bool saw_inside = false;
            for (const Point &pt : out.points) {
                REQUIRE(slice.contains(pt));
                if (comb.front().contains(pt))
                    saw_inside = true;
            }
            REQUIRE(saw_inside);
            REQUIRE(avoid.comb_log().empty());
        }
    }
}

TEST_CASE("Combing fallback collapsed thin strip stays straight", "[AvoidCrossingPerimeters]")
{
    // 1.2 mm strip, 1.5x a 0.42 mm outer wall collapses the outline fallback.
    const ExPolygons slices{rect(0, 0, 30, 1.2)};
    const ExPolygons comb = AvoidCrossingPerimeters::build_comb_region({}, 0., slices, scale_(1.5 * 0.42), false, {}, 0.);
    REQUIRE(comb.empty());

    const Polyline original{mm(1, 0.6), mm(29, 0.6)};
    AvoidCrossingPerimeters avoid;
    const Polyline out = avoid.apply_comb_fallback(true, comb, original, 2, nullptr);
    REQUIRE(out.points == original.points);
    REQUIRE(avoid.comb_log().size() == 1);
    REQUIRE(avoid.comb_log().front().reason == "collapsed");
    REQUIRE_FALSE(avoid.comb_log().front().routed);
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
    REQUIRE(out.points == original.points);
    REQUIRE(avoid.comb_log().size() == 1);
    REQUIRE(avoid.comb_log().front().reason == "island");
    REQUIRE_FALSE(avoid.comb_log().front().routed);
    REQUIRE(avoid.comb_log().front().layer_id == 9);
}
