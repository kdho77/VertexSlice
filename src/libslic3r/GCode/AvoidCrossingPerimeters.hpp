#ifndef slic3r_AvoidCrossingPerimeters_hpp_
#define slic3r_AvoidCrossingPerimeters_hpp_

#include "../libslic3r.h"
#include "../ExPolygon.hpp"
#include "../EdgeGrid.hpp"

#include <string>
#include <vector>

namespace Slic3r {

// Forward declarations.
class GCode;
class Layer;
class Point;

class AvoidCrossingPerimeters
{
public:
    // Routing around the objects vs. inside a single object.
    void        use_external_mp(bool use = true) { m_use_external_mp = use; };
    bool        used_external_mp() { return m_use_external_mp; }
    void        use_external_mp_once()  { m_use_external_mp_once = true; }
    bool        used_external_mp_once() { return m_use_external_mp_once; }
    void        disable_once()          { m_disabled_once = true; }
    bool        disabled_once() const   { return m_disabled_once; }
    void        reset_once_modifiers()  { m_use_external_mp_once = false; m_disabled_once = false; }

    void        init_layer(const Layer &layer);

    Polyline    travel_to(const GCode& gcodegen, const Point& point)
    {
        bool could_be_wipe_disabled;
        return this->travel_to(gcodegen, point, &could_be_wipe_disabled);
    }

    Polyline    travel_to(const GCode& gcodegen, const Point& point, bool* could_be_wipe_disabled);

    // One travel that could not stay inside the inner wall, or a routed travel whose
    // detour exceeded the 20 mm cap (routed == true, reason == "cap").
    struct CombLogEntry {
        std::string reason;
        int         layer_id  = -1;
        double      from_x    = 0.;
        double      from_y    = 0.;
        double      to_x      = 0.;
        double      to_y      = 0.;
        double      detour_mm = 0.;
        bool        routed    = false;
    };

    ~AvoidCrossingPerimeters();

    // Innermost loops shrunk by inner_shrink, with the Arachne/open-path outline fallback
    // and the same top-surface cut-out as get_boundary(). Lengths are scaled coordinates.
    static ExPolygons build_comb_region(const ExPolygons &innermost_loops,
                                        double            inner_shrink_scaled,
                                        const ExPolygons &open_fallback_lslices,
                                        double            outline_shrink_scaled,
                                        bool              fill_open_islands,
                                        const ExPolygons &top_surfaces,
                                        double            top_cut_shrink_scaled);

    // Flag off: returns `original` unchanged and writes nothing.
    // Flag on: route inside `comb` when any inside path exists (the 20 mm cap may be exceeded).
    // A deep shrink that collapses is retried at shallower insets, down to the wall centerline.
    // Different islands set island_lift_pending() and keep the straight XY move so the caller can
    // retract and lift (belt-normal / z-hop) instead of crossing the wall on the surface.
    // `layer` may be null; multi-ring routing then uses the ring walk instead of avoid_perimeters().
    Polyline apply_comb_fallback(bool              enabled,
                                 const ExPolygons &comb,
                                 const Polyline   &original,
                                 int               layer_id,
                                 const Layer      *layer);

    const std::vector<CombLogEntry> &comb_log() const { return m_comb_log; }
    bool island_lift_pending() const { return m_island_lift; }
    // Returns true once when the last fallback asked for a retract + lift, then clears the flag.
    bool consume_island_lift()
    {
        const bool lift = m_island_lift;
        m_island_lift   = false;
        return lift;
    }

    struct Boundary {
        // Collection of boundaries used for detection of crossing perimeters for travels
        Polygons                        boundaries;
        // Bounding box of boundaries
        BoundingBoxf                    bbox;
        // Precomputed distances of all points in boundaries
        std::vector<std::vector<float>> boundaries_params;
        // Used for detection of intersection between line and any polygon from boundaries
        EdgeGrid::Grid                  grid;

        void clear()
        {
            boundaries.clear();
            boundaries_params.clear();
        }
    };

private:
    bool           m_use_external_mp { false };
    // just for the next travel move
    bool           m_use_external_mp_once { false };
    // this flag disables reduce_crossing_wall just for the next travel move
    // we enable it by default for the first travel move in print
    bool           m_disabled_once { true };

    // Lslices offseted by half an external perimeter width. Used for detection if line or polyline is inside of any polygon.
    ExPolygons               m_lslices_offset;
    std::vector<BoundingBox> m_lslices_offset_bboxes;
    // Used for detection of line or polyline is inside of any polygon.
    EdgeGrid::Grid m_grid_lslice;
    // Store all needed data for travels inside object
    Boundary m_internal;
    // Store all needed data for travels outside object
    Boundary m_external;

    // Innermost-wall region used by the combing fallback. Built lazily on the first
    // flagged travel of the layer. Empty when the flag is off.
    ExPolygons               m_comb;
    std::vector<BoundingBox> m_comb_bboxes;
    EdgeGrid::Grid           m_grid_comb;
    Boundary                 m_comb_boundary;
    bool                     m_comb_init { false };
    std::vector<CombLogEntry> m_comb_log;
    bool                     m_comb_summary_emitted { false };
    // Straight XY move between disconnected comb islands. Caller emits retract + lift.
    bool                     m_island_lift { false };

    void ensure_comb(const Layer &layer);
    void emit_comb_log(const CombLogEntry &entry);
};

} // namespace Slic3r

#endif // slic3r_AvoidCrossingPerimeters_hpp_
