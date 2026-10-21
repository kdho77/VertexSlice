#include "BeltOnlyInvariant.hpp"

#include "libslic3r/BeltTransform.hpp"
#include "libslic3r/BoundingBox.hpp"
#include "libslic3r/ClipperUtils.hpp"
#include "libslic3r/Line.hpp"

#include <algorithm>
#include <cmath>
#include <limits>

namespace Slic3r {
namespace BeltPrinter {

namespace {

double sq(double v) { return v * v; }

bool point_in_slices(const Point &p, const WallGeometry &wall)
{
    for (const ExPolygon &ex : wall.slices)
        if (ex.contains(p))
            return true;
    return false;
}

Point point_at_arc(const Polygon &poly, double arc)
{
    if (poly.empty())
        return Point(0, 0);
    double total = poly.length();
    if (total <= 0)
        return poly.points.front();
    arc = std::fmod(arc, total);
    if (arc < 0)
        arc += total;
    double acc = 0;
    for (const Line &ln : poly.lines()) {
        double len = ln.length();
        if (acc + len >= arc - 1.0) {
            double t = len > 0 ? (arc - acc) / len : 0;
            t = std::clamp(t, 0.0, 1.0);
            Vec2d v = ln.a.cast<double>() + (ln.b - ln.a).cast<double>() * t;
            return Point(coord_t(std::llround(v.x())), coord_t(std::llround(v.y())));
        }
        acc += len;
    }
    return poly.points.front();
}

Points walk_arc(const Polygon &poly, double a0, double a1, bool ccw)
{
    Points out;
    double total = poly.length();
    if (total <= 0)
        return out;
    auto norm = [&](double a) {
        a = std::fmod(a, total);
        if (a < 0)
            a += total;
        return a;
    };
    a0 = norm(a0);
    a1 = norm(a1);
    double dist = ccw ? (a1 >= a0 ? a1 - a0 : total - a0 + a1)
                      : (a0 >= a1 ? a0 - a1 : total - a1 + a0);
    double step = scale_(0.4);
    int    n    = std::max(1, int(std::ceil(dist / std::max(step, 1.0))));
    out.reserve(size_t(n) + 1);
    for (int i = 0; i <= n; ++i) {
        double a = ccw ? a0 + dist * (double(i) / n) : a0 - dist * (double(i) / n);
        Point  p = point_at_arc(poly, a);
        if (out.empty() || out.back() != p)
            out.push_back(p);
    }
    return out;
}

bool segment_inside(const Point &a, const Point &b, const WallGeometry &wall)
{
    double len = (b - a).cast<double>().norm();
    int    n   = std::max(1, int(std::ceil(len / scale_(0.5))));
    for (int i = 0; i <= n; ++i) {
        Vec2d  v = a.cast<double>() + (b - a).cast<double>() * (double(i) / n);
        Point  p(coord_t(std::llround(v.x())), coord_t(std::llround(v.y())));
        if (!point_in_slices(p, wall))
            return false;
    }
    return true;
}

bool on_belt_corridor(const Point &p, const WallGeometry &wall)
{
    WallHit hit = nearest_wall(p, wall);
    if (!hit.found || hit.dist_mm > GATE_NEAR_MM)
        return false;
    return hit.point.y() <= wall.ymin + wall.belt_band;
}

bool segment_travel_ok(const Point &a, const Point &b, const WallGeometry &wall)
{
    double       len     = (b - a).cast<double>().norm();
    int          n       = std::max(1, int(std::ceil(len / scale_(0.5))));
    for (int i = 0; i <= n; ++i) {
        Vec2d v = a.cast<double>() + (b - a).cast<double>() * (double(i) / n);
        Point p(coord_t(std::llround(v.x())), coord_t(std::llround(v.y())));
        if (on_belt_corridor(p, wall))
            continue;
        if (!point_in_slices(p, wall))
            return false;
        WallHit hit = nearest_wall(p, wall);
        // Clearance is the gate's interior margin measured from the slice, not
        // one bead. A travel vertex closer than that is a non-belt landing.
        if (hit.found && hit.dist_mm + 1e-6 < wall.safe_mm && hit.point.y() > wall.ymin + wall.belt_band)
            return false;
    }
    return true;
}

void dedup(Points &pts)
{
    Points out;
    out.reserve(pts.size());
    for (const Point &p : pts)
        if (out.empty() || out.back() != p)
            out.push_back(p);
    pts.swap(out);
}

void seam_closed(Polyline &pl, const WallGeometry &wall)
{
    if (pl.size() < 2)
        return;
    if (pl.first_point() != pl.last_point())
        pl.points.push_back(pl.first_point());
    size_t  best   = 0;
    bool    found  = false;
    coord_t best_y = std::numeric_limits<coord_t>::max();
    for (size_t i = 0; i + 1 < pl.size(); ++i) {
        if (!is_belt_contact(pl.points[i], wall))
            continue;
        if (!found || pl.points[i].y() < best_y) {
            best   = i;
            best_y = pl.points[i].y();
            found  = true;
        }
    }
    if (!found) {
        // No vertex is on the belt. Keep the closed shape and splice the belt
        // anchor in so the seam (the new start) is on the contact side.
        pl.points.insert(pl.points.begin(), wall.belt_anchor);
        pl.points.back() = wall.belt_anchor;
        dedup(pl.points);
        if (pl.size() >= 2 && pl.first_point() != pl.last_point())
            pl.points.push_back(pl.first_point());
        return;
    }
    if (best == 0)
        return;
    Points rotated;
    rotated.reserve(pl.size());
    for (size_t k = best; k + 1 < pl.size(); ++k)
        rotated.push_back(pl.points[k]);
    for (size_t k = 0; k <= best; ++k)
        rotated.push_back(pl.points[k]);
    if (rotated.front() != rotated.back())
        rotated.push_back(rotated.front());
    pl.points = std::move(rotated);
}

void append_deep_arc(Points &pts, const Point &from, const Point &to, const WallGeometry &wall)
{
    if (!wall.has_inner || wall.deep_contours.empty())
        return;
    size_t best_c = 0;
    double best_d = std::numeric_limits<double>::max();
    double a0 = 0, a1 = 0;
    bool   found = false;
    for (size_t ci = 0; ci < wall.deep_contours.size(); ++ci) {
        const Polygon &poly = wall.deep_contours[ci];
        double arc = 0, anch = 0, dest = 0;
        double anch_d = std::numeric_limits<double>::max();
        double dest_d = std::numeric_limits<double>::max();
        for (const Line &ln : poly.lines()) {
            Point  ca, cd;
            double da = ln.distance_to_squared(from, &ca);
            double dd = ln.distance_to_squared(to, &cd);
            double la = std::min((ca - ln.a).cast<double>().norm(), ln.length());
            double ld = std::min((cd - ln.a).cast<double>().norm(), ln.length());
            if (da < anch_d) { anch_d = da; anch = arc + la; }
            if (dd < dest_d) { dest_d = dd; dest = arc + ld; }
            arc += ln.length();
        }
        if (dest_d < best_d) {
            best_d = dest_d;
            best_c = ci;
            a0 = anch;
            a1 = dest;
            found = true;
        }
    }
    if (!found)
        return;
    const Polygon &poly  = wall.deep_contours[best_c];
    const double   total = poly.length();
    const double   ccw   = a1 >= a0 ? a1 - a0 : total - a0 + a1;
    for (const Point &p : walk_arc(poly, a0, a1, ccw <= total * 0.5))
        if (pts.empty() || pts.back() != p)
            pts.push_back(p);
}

} // namespace

double machine_xy_length(const Vec2d &from_virt_mm, const Vec2d &to_virt_mm, double angle_deg)
{
    Vec3d a = BeltTransform::inverse_transform_point(Vec3d(from_virt_mm.x(), from_virt_mm.y(), 0.0), angle_deg);
    Vec3d b = BeltTransform::inverse_transform_point(Vec3d(to_virt_mm.x(), to_virt_mm.y(), 0.0), angle_deg);
    return std::hypot(b.x() - a.x(), b.y() - a.y());
}

double legacy_shear_extrusion_length(const Vec2d &from_virt_mm, const Vec2d &to_virt_mm, double angle_rad)
{
    double dx    = to_virt_mm.x() - from_virt_mm.x();
    double dy    = to_virt_mm.y() - from_virt_mm.y();
    double cos_a = std::cos(angle_rad);
    if (std::abs(cos_a) < 1e-9)
        cos_a = 1e-9;
    double y_scale = 1.0 / (cos_a * cos_a);
    return std::sqrt(dx * dx + y_scale * y_scale * dy * dy);
}

double extrusion_length_for_move(bool belt, const Vec2d &from_virt_mm, const Vec2d &to_virt_mm, double angle_deg)
{
    if (!belt)
        return (to_virt_mm - from_virt_mm).norm();
    return machine_xy_length(from_virt_mm, to_virt_mm, angle_deg);
}

WallGeometry make_wall_geometry(const ExPolygons &slices, double bead_mm)
{
    WallGeometry wall;
    wall.slices = slices;
    wall.bead   = coord_t(std::llround(scale_(std::max(bead_mm, 0.05))));
    // Measure from the slice boundary. The emitted centre-line is inside it by
    // about half a bead, and Arachne can place that line a little deeper, so a
    // fixed inset of the contour under-estimates the distance the gate sees.
    const double bead_used = std::max(bead_mm, 0.05);
    wall.safe_mm = GATE_NEAR_MM + GATE_HALF_BEAD_MM + 0.5 * bead_used + SAFE_ARACHNE_MARGIN_MM;
    for (const ExPolygon &ex : slices) {
        if (ex.contour.size() >= 3)
            wall.contours.push_back(ex.contour);
        // Hole rims are outer wall too. A start on the heel-cup rim is not
        // interior just because it is far from the external contour.
        for (const Polygon &hole : ex.holes)
            if (hole.size() >= 3)
                wall.contours.push_back(hole);
    }
    if (wall.contours.empty())
        return wall;

    wall.ymin      = std::numeric_limits<coord_t>::max();
    wall.belt_band = coord_t(std::llround(scale_(SAFE_BELT_BAND_VIRT_MM)));
    const double step = scale_(0.2);
    for (const Polygon &poly : wall.contours) {
        for (const Point &p : poly.points)
            wall.ymin = std::min(wall.ymin, p.y());
        for (const Line &ln : poly.lines()) {
            double len = ln.length();
            int    n   = std::max(1, int(std::ceil(len / step)));
            for (int i = 0; i <= n; ++i) {
                Vec2d v = ln.a.cast<double>() + (ln.b - ln.a).cast<double>() * (double(i) / n);
                wall.samples.emplace_back(coord_t(std::llround(v.x())), coord_t(std::llround(v.y())));
            }
        }
    }
    if (wall.samples.empty())
        return wall;

    std::vector<coord_t> belt_x;
    for (const Point &s : wall.samples)
        if (s.y() <= wall.ymin + wall.belt_band)
            belt_x.push_back(s.x());
    if (belt_x.empty()) {
        Point lowest = wall.samples.front();
        for (const Point &s : wall.samples)
            if (s.y() < lowest.y())
                lowest = s;
        wall.belt_anchor = lowest;
    } else {
        std::sort(belt_x.begin(), belt_x.end());
        coord_t mid_x = belt_x[belt_x.size() / 2];
        // Anchor on the outer-wall centre-line (slice inset by half a bead).
        // A pure +Y shift of the slice minimum sits below that centre-line on a
        // curved tip and the inserted point pulls the gate's belt band down.
        wall.belt_anchor = Point(mid_x, wall.ymin + wall.bead / 2);
        ExPolygons centerline = offset_ex(slices, -float(wall.bead) * 0.5f);
        coord_t    low_y      = std::numeric_limits<coord_t>::max();
        std::vector<Point> lows;
        for (const ExPolygon &ex : centerline) {
            for (const Point &p : ex.contour.points) {
                if (p.y() < low_y) {
                    low_y = p.y();
                    lows.clear();
                    lows.push_back(p);
                } else if (p.y() <= low_y + wall.belt_band) {
                    lows.push_back(p);
                }
            }
        }
        if (!lows.empty()) {
            std::sort(lows.begin(), lows.end(), [](const Point &a, const Point &b) { return a.x() < b.x(); });
            wall.belt_anchor = lows[lows.size() / 2];
        }
    }

    // Inset past safe_mm so a travel vertex on this contour is interior for the
    // gate, not merely one bead inside the slice.
    const float inset = -float(scale_(wall.safe_mm + 0.15));
    ExPolygons  deep  = offset_ex(slices, inset);
    for (const ExPolygon &ex : deep) {
        if (ex.contour.size() >= 3)
            wall.deep_contours.push_back(ex.contour);
    }
    if (!wall.deep_contours.empty()) {
        wall.has_inner    = true;
        wall.inner_anchor = wall.deep_contours.front().points.front();
        for (const Polygon &poly : wall.deep_contours)
            for (const Point &p : poly.points)
                if (p.y() < wall.inner_anchor.y())
                    wall.inner_anchor = p;
    } else {
        wall.inner_anchor = wall.belt_anchor;
    }

    BoundingBox bb(wall.samples);
    wall.grid_cell     = std::max(coord_t(1), coord_t(std::llround(scale_(1.0))));
    wall.grid_origin_x = bb.min.x();
    wall.grid_origin_y = bb.min.y();
    wall.grid_w        = std::max(1, int((bb.max.x() - bb.min.x()) / wall.grid_cell) + 1);
    wall.grid_h        = std::max(1, int((bb.max.y() - bb.min.y()) / wall.grid_cell) + 1);
    // Guard a pathological bbox. A 1 mm grid over a normal plate is fine.
    if (int64_t(wall.grid_w) * int64_t(wall.grid_h) > 4000000) {
        wall.grid_cell = coord_t(std::llround(scale_(5.0)));
        wall.grid_w    = std::max(1, int((bb.max.x() - bb.min.x()) / wall.grid_cell) + 1);
        wall.grid_h    = std::max(1, int((bb.max.y() - bb.min.y()) / wall.grid_cell) + 1);
    }
    wall.grid.assign(size_t(wall.grid_w) * size_t(wall.grid_h), {});
    for (const Point &s : wall.samples) {
        int x = int((s.x() - wall.grid_origin_x) / wall.grid_cell);
        int y = int((s.y() - wall.grid_origin_y) / wall.grid_cell);
        x = std::clamp(x, 0, wall.grid_w - 1);
        y = std::clamp(y, 0, wall.grid_h - 1);
        wall.grid[size_t(y) * size_t(wall.grid_w) + size_t(x)].push_back(s);
    }
    wall.valid = true;
    return wall;
}

WallHit nearest_wall(const Point &p, const WallGeometry &wall)
{
    WallHit hit;
    if (!wall.valid || wall.grid.empty() || wall.grid_cell <= 0)
        return hit;
    int cx = int((p.x() - wall.grid_origin_x) / wall.grid_cell);
    int cy = int((p.y() - wall.grid_origin_y) / wall.grid_cell);
    auto search = [&](int radius) {
        double best = std::numeric_limits<double>::max();
        for (int dy = -radius; dy <= radius; ++dy) {
            int y = cy + dy;
            if (y < 0 || y >= wall.grid_h)
                continue;
            for (int dx = -radius; dx <= radius; ++dx) {
                int x = cx + dx;
                if (x < 0 || x >= wall.grid_w)
                    continue;
                for (const Point &s : wall.grid[size_t(y) * size_t(wall.grid_w) + size_t(x)]) {
                    double d2 = (s - p).cast<double>().squaredNorm();
                    if (d2 < best) {
                        best       = d2;
                        hit.found  = true;
                        hit.point  = s;
                    }
                }
            }
        }
        if (hit.found)
            hit.dist_mm = std::sqrt(best) * SCALING_FACTOR;
    };
    search(4);
    if (!hit.found)
        search(12);
    if (!hit.found)
        hit.dist_mm = 1e9;
    return hit;
}

bool nearest_is_belt(const Point &p, const WallGeometry &wall)
{
    WallHit hit = nearest_wall(p, wall);
    return hit.found && hit.point.y() <= wall.ymin + wall.belt_band;
}

bool is_interior(const Point &p, const WallGeometry &wall)
{
    if (!wall.valid)
        return false;
    WallHit hit = nearest_wall(p, wall);
    return hit.dist_mm > wall.safe_mm && point_in_slices(p, wall);
}

bool is_belt_contact(const Point &p, const WallGeometry &wall)
{
    if (!wall.valid)
        return false;
    WallHit hit = nearest_wall(p, wall);
    if (!hit.found || hit.dist_mm > GATE_NEAR_MM)
        return false;
    if (hit.point.y() > wall.ymin + wall.belt_band)
        return false;
    // 0.15 virtual mm is 0.21 mm of machine Y, inside the gate's 0.25 mm band
    // above the emitted outer-wall minimum (belt_anchor).
    const coord_t cap = wall.belt_anchor.y() + coord_t(std::llround(scale_(0.15)));
    return p.y() <= cap;
}

bool is_safe_event(const Point &p, const WallGeometry &wall)
{
    if (!wall.valid)
        return true;
    if (is_belt_contact(p, wall))
        return true;
    return is_interior(p, wall);
}

bool is_end_cap_layer(const WallGeometry &previous, const WallGeometry &next)
{
    if (!previous.valid || !next.valid)
        return false;
    const double rise = (next.belt_anchor.y() - previous.belt_anchor.y()) * SCALING_FACTOR;
    if (rise < END_CAP_RISE_VIRT_MM)
        return false;
    // A point that is still on the previous belt band is the sole, not the tip.
    return !is_belt_contact(next.belt_anchor, previous);
}

Point layer_change_anchor(const WallGeometry &previous, const WallGeometry &next)
{
    if (!next.valid)
        return previous.valid ? previous.belt_anchor : Point(0, 0);
    if (is_end_cap_layer(previous, next))
        return previous.belt_anchor;
    return next.belt_anchor;
}

Point belt_seam_point(const Points &loop_pts, const WallGeometry &wall)
{
    if (loop_pts.empty())
        return wall.valid ? wall.belt_anchor : Point(0, 0);
    // The lowest vertex is the belt side of this loop. A higher vertex can still
    // pass the slice belt test on a curved tip and leave the seam outside the
    // gate's 0.25 mm band.
    Point best = loop_pts.front();
    for (const Point &p : loop_pts)
        if (p.y() < best.y())
            best = p;
    return best;
}

Polyline connect_end_to_safe(const Point &end, const WallGeometry &wall)
{
    Polyline out;
    if (!wall.valid || is_safe_event(end, wall))
        return out;

    WallHit hit = nearest_wall(end, wall);
    // Outer-wall ends (about half a bead inside the slice) go to the belt.
    // Ends already inboard of that jog inward. A lap down the contour would
    // spend the per-metre time budget.
    const double on_wall_mm = std::max(0.30, 0.5 * double(wall.bead) * SCALING_FACTOR + 0.05);
    if (hit.found && hit.dist_mm >= on_wall_mm && hit.dist_mm < wall.safe_mm) {
        Vec2d  dir = (end - hit.point).cast<double>();
        double nrm = dir.norm();
        if (nrm > 1.0) {
            dir /= nrm;
            double need_mm = (wall.safe_mm + 0.15) - hit.dist_mm;
            Point  jog(coord_t(std::llround(end.x() + dir.x() * scale_(need_mm))),
                       coord_t(std::llround(end.y() + dir.y() * scale_(need_mm))));
            if (segment_inside(end, jog, wall) && is_safe_event(jog, wall)) {
                out.points = {end, jog};
                return out;
            }
        }
    }

    if (segment_inside(end, wall.belt_anchor, wall)) {
        out.points = {end, wall.belt_anchor};
        return out;
    }

    // Stay inside the part. Walking the raw slice contour extrudes outside the
    // bead and drops the gate's belt band below the real outer wall.
    if (wall.has_inner) {
        Points pts;
        pts.push_back(end);
        append_deep_arc(pts, end, wall.belt_anchor, wall);
        if (pts.empty() || pts.back() != wall.belt_anchor)
            pts.push_back(wall.belt_anchor);
        Points kept;
        kept.reserve(pts.size());
        for (const Point &p : pts) {
            if (p != end && p.y() < wall.belt_anchor.y())
                continue;
            if (kept.empty() || kept.back() != p)
                kept.push_back(p);
        }
        if (kept.size() >= 2) {
            out.points = std::move(kept);
            return out;
        }
    }

    out.points = {end, wall.belt_anchor};
    return out;
}

void prepare_polyline_for_belt(Polyline &pl, const WallGeometry &wall)
{
    if (!wall.valid || pl.size() < 2)
        return;
    bool closed = pl.first_point() == pl.last_point() && pl.size() >= 4;
    if (!closed && pl.size() >= 4 &&
        (pl.first_point() - pl.last_point()).cast<double>().norm() <= scale_(0.2)) {
        pl.points.back() = pl.points.front();
        closed = true;
    }
    if (closed) {
        seam_closed(pl, wall);
        return;
    }
    if (!is_safe_event(pl.first_point(), wall)) {
        Polyline ext = connect_end_to_safe(pl.first_point(), wall);
        if (ext.size() >= 2) {
            ext.reverse();
            Points np(ext.points.begin(), ext.points.end() - 1);
            np.insert(np.end(), pl.points.begin(), pl.points.end());
            pl.points = std::move(np);
            dedup(pl.points);
        }
    }
    if (pl.size() >= 2 && !is_safe_event(pl.last_point(), wall)) {
        Polyline ext = connect_end_to_safe(pl.last_point(), wall);
        if (ext.size() >= 2) {
            for (size_t i = 1; i < ext.size(); ++i)
                if (pl.points.back() != ext.points[i])
                    pl.points.push_back(ext.points[i]);
        }
    }
}

Polylines plan_belt_polylines(Polylines paths, const WallGeometry &wall)
{
    const double join = scale_(0.35);
    Polylines    open;
    open.reserve(paths.size());
    for (Polyline &pl : paths) {
        if (pl.size() < 2)
            continue;
        // Drop a degenerate stub. A closed loop (first == last, more than one segment) stays.
        if (pl.size() == 2 && (pl.first_point() - pl.last_point()).cast<double>().norm() < 1.0)
            continue;
        open.push_back(std::move(pl));
    }

    bool merged = true;
    while (merged) {
        merged = false;
        for (size_t i = 0; i < open.size() && !merged; ++i) {
            for (size_t j = i + 1; j < open.size() && !merged; ++j) {
                auto dist = [](const Point &a, const Point &b) {
                    return (a - b).cast<double>().norm();
                };
                Point ai = open[i].first_point();
                Point bi = open[i].last_point();
                Point aj = open[j].first_point();
                Point bj = open[j].last_point();
                int   mode = -1;
                if (dist(bi, aj) <= join) mode = 0;
                else if (dist(bi, bj) <= join) mode = 1;
                else if (dist(ai, aj) <= join) mode = 2;
                else if (dist(ai, bj) <= join) mode = 3;
                if (mode < 0)
                    continue;
                if (mode >= 2)
                    open[i].reverse();
                Polyline src = open[j];
                if (mode == 1 || mode == 3)
                    src.reverse();
                if (!open[i].empty() && !src.empty() && open[i].last_point() == src.first_point())
                    open[i].points.insert(open[i].points.end(), src.points.begin() + 1, src.points.end());
                else
                    open[i].points.insert(open[i].points.end(), src.points.begin(), src.points.end());
                open.erase(open.begin() + int(j));
                merged = true;
            }
        }
    }

    Polylines out;
    out.reserve(open.size());
    for (Polyline &pl : open) {
        if (pl.size() >= 3 && (pl.first_point() - pl.last_point()).cast<double>().norm() <= join) {
            if (pl.first_point() != pl.last_point())
                pl.points.push_back(pl.first_point());
        }
        prepare_polyline_for_belt(pl, wall);
        if (pl.size() >= 2)
            out.push_back(std::move(pl));
    }
    return out;
}

Polyline route_travel(const Point &from, const Point &to, const WallGeometry &wall)
{
    Polyline pl;
    if (from == to) {
        pl.points = {from};
        return pl;
    }
    if (!wall.valid || segment_travel_ok(from, to, wall)) {
        pl.points = {from, to};
        return pl;
    }

    Points pts;
    auto add = [&](const Point &p) {
        if (pts.empty() || pts.back() != p)
            pts.push_back(p);
    };
    add(from);
    // Reach the belt without crossing a non-belt wall. On a convex slice the
    // straight segment is inside; on a notched one, follow the inset contour.
    if (!segment_travel_ok(from, wall.belt_anchor, wall)) {
        if (wall.has_inner)
            append_deep_arc(pts, from, wall.inner_anchor, wall);
        if (wall.has_inner)
            add(wall.inner_anchor);
    }
    add(wall.belt_anchor);
    if (!segment_travel_ok(pts.empty() ? from : pts.back(), to, wall)) {
        if (wall.has_inner && !segment_travel_ok(wall.inner_anchor, to, wall))
            append_deep_arc(pts, wall.inner_anchor, to, wall);
        else if (wall.has_inner)
            add(wall.inner_anchor);
    }
    add(to);
    // Drop a waypoint that the gate would call off-belt, other than the
    // caller-supplied endpoints. The belt anchor and the inset are safe; a
    // sampled arc point that somehow is not gets replaced by the anchor.
    for (size_t i = 1; i + 1 < pts.size(); ++i) {
        if (!is_safe_event(pts[i], wall))
            pts[i] = wall.has_inner ? wall.inner_anchor : wall.belt_anchor;
    }
    dedup(pts);
    pl.points = std::move(pts);
    return pl;
}

double belt_outer_feedrate(const Point &a, const Point &b, double feed_mm_min, const WallGeometry &wall)
{
    if (!wall.valid)
        return feed_mm_min;
    // Contours are the slice boundary. The emitted bottom edge sits half a bead
    // above it, and beltslow's band is another 0.25 mm of machine Y.
    const double bead_mm = double(wall.bead) * SCALING_FACTOR;
    const double band_mm = GATE_BELT_BAND_GCODE_MM / BELT_I_YY + 0.5 * bead_mm + 0.05;
    const coord_t band = wall.ymin + coord_t(std::llround(scale_(band_mm)));
    if (a.y() <= band && b.y() <= band)
        return std::min(feed_mm_min, BELT_CONTACT_SPEED_MM_S * 60.0);
    return feed_mm_min;
}

} // namespace BeltPrinter
} // namespace Slic3r
