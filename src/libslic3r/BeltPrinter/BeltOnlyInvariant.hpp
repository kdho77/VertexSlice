#ifndef slic3r_BeltPrinter_BeltOnlyInvariant_hpp_
#define slic3r_BeltPrinter_BeltOnlyInvariant_hpp_

#include "libslic3r/ExPolygon.hpp"
#include "libslic3r/Point.hpp"
#include "libslic3r/Polygon.hpp"
#include "libslic3r/Polyline.hpp"

#include <vector>

namespace Slic3r {
namespace BeltPrinter {

// Locked IdeaFormer inverse: Y_mach = √2 * Y_virt. Must match BeltTransform.cpp.
constexpr double BELT_I_YY = 1.41421356237;

// belt_gate2 acceptance (virtual millimetres). The gate's belt band is 0.25 mm of
// *machine* Y, which is 0.25/√2 ≈ 0.177 mm of virtual Y. Interior is
// centre-line distance − 0.5 mm > 1.0 mm, i.e. more than 1.5 mm from the emitted
// outer-wall centre-line. Wall geometry is the slice boundary, which sits about
// half a bead outside that centre-line, so the interior threshold grows with the
// bead (see WallGeometry::safe_mm).
constexpr double GATE_NEAR_MM              = 1.0;
constexpr double GATE_HALF_BEAD_MM         = 0.5;
constexpr double GATE_BELT_BAND_GCODE_MM   = 0.25;
constexpr double SAFE_INTERIOR_MM          = 1.85;
constexpr double SAFE_ARACHNE_MARGIN_MM    = 0.45;
constexpr double SAFE_BELT_BAND_VIRT_MM    = 0.12;
constexpr double BELT_CONTACT_SPEED_MM_S   = 20.0;

// Machine XY length of a segment given in virtual (pre-inverse) millimetres.
// Equals hypot(dx_mach, dy_mach). The previous shear formula
// hypot(dx, dy / cos²θ) equals sqrt(dx_mach² + 2 dy_mach²) at 45°.
double machine_xy_length(const Vec2d &from_virt_mm, const Vec2d &to_virt_mm, double angle_deg);

// The pre-fix length, kept so tests can show the efix.py scale is exactly
// machine_xy_length / legacy_shear_extrusion_length.
double legacy_shear_extrusion_length(const Vec2d &from_virt_mm, const Vec2d &to_virt_mm, double angle_rad);

// Virtual length for non-belt printers, machine XY length for belt printers.
double extrusion_length_for_move(bool belt, const Vec2d &from_virt_mm, const Vec2d &to_virt_mm, double angle_deg);

struct WallHit {
    bool   found    = false;
    double dist_mm  = 1e9;
    Point  point    = Point(0, 0);
};

// Outer-wall geometry for one layer, in scaled virtual XY (the same space as
// extrusion polylines). Holes are not part of the outer wall: the gate only
// measures distance to the external perimeter.
struct WallGeometry {
    ExPolygons                 slices;
    Polygons                   contours;
    Points                     samples;
    coord_t                    ymin             = 0;
    coord_t                    belt_band        = 0;
    coord_t                    bead             = 0;
    // Virtual mm from the slice contour. A point farther inside than this is
    // still interior after the gate subtracts the outer-wall inset.
    double                     safe_mm          = SAFE_INTERIOR_MM;
    Point                      belt_anchor      = Point(0, 0);
    Point                      inner_anchor     = Point(0, 0);
    bool                       has_inner        = false;
    Polygons                   deep_contours;
    bool                       valid            = false;
    const void                *source           = nullptr;

    coord_t                    grid_origin_x    = 0;
    coord_t                    grid_origin_y    = 0;
    coord_t                    grid_cell        = 1;
    int                        grid_w           = 0;
    int                        grid_h           = 0;
    std::vector<std::vector<Point>> grid;
};

WallGeometry make_wall_geometry(const ExPolygons &slices, double bead_mm);

WallHit  nearest_wall(const Point &p, const WallGeometry &wall);
bool     nearest_is_belt(const Point &p, const WallGeometry &wall);
bool     is_interior(const Point &p, const WallGeometry &wall);
bool     is_belt_contact(const Point &p, const WallGeometry &wall);
// Gate-safe: on the belt contact, or far enough inside that belt_gate2 calls it interior.
bool     is_safe_event(const Point &p, const WallGeometry &wall);

// Vertex (or the belt anchor, if none of the vertices is safe) to open a closed loop on.
Point    belt_seam_point(const Points &loop_pts, const WallGeometry &wall);

// Join open polylines into closed loops seamed on the belt, or leave a polyline
// whose endpoints are already safe. Closed results have first == last, and that
// point is on the belt contact when the contour can reach it.
Polylines plan_belt_polylines(Polylines paths, const WallGeometry &wall);

// Single-path safety net: rotate a closed polyline onto the belt, or extend an
// open polyline's unsafe ends to the belt contact.
void     prepare_polyline_for_belt(Polyline &pl, const WallGeometry &wall);

// Travel from `from` to `to`. Vertices are safe when the endpoints are safe.
// Segments stay inside the slice, at least one bead off a non-belt wall, or run
// along the belt contact.
Polyline route_travel(const Point &from, const Point &to, const WallGeometry &wall);

// Polyline from `end` (inclusive) to a gate-safe point. Empty when `end` is already
// safe or the wall is missing. Wall-adjacent ends go to the belt contact; ends that
// already sit inside the part jog inward to the interior.
Polyline connect_end_to_safe(const Point &end, const WallGeometry &wall);

// Outer-wall feedrate. A segment whose both ends lie in the belt-contact band
// (0.25 mm of machine Y, plus half a bead so the inset perimeter still matches)
// is capped at 20 mm/s. Every other segment keeps `feed_mm_min`.
double belt_outer_feedrate(const Point &a, const Point &b, double feed_mm_min, const WallGeometry &wall);

} // namespace BeltPrinter
} // namespace Slic3r

#endif
