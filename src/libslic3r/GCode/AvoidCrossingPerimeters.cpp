#include "../Layer.hpp"
#include "../GCode.hpp"
#include "../EdgeGrid.hpp"
#include "../Print.hpp"
#include "../Polygon.hpp"
#include "../ExPolygon.hpp"
#include "../Geometry.hpp"
#include "../ClipperUtils.hpp"
#include "../SVG.hpp"
#include "AvoidCrossingPerimeters.hpp"

#include <cmath>
#include <numeric>
#include <unordered_set>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <limits>
#include <mutex>
#include <sstream>
#include <boost/range/adaptor/reversed.hpp>

namespace Slic3r {

struct TravelPoint
{
    Point point;
    // Index of the polygon containing this point. A negative value indicates that the point is not on any border.
    int   border_idx;
    // simplify_travel() doesn't remove this point.
    bool do_not_remove = false;
};

struct Intersection
{
    // Index of the polygon containing this point of intersection.
    size_t border_idx;
    // Index of the line on the polygon containing this point of intersection.
    size_t line_idx;
    // Point of intersection.
    Point  point;
    // Distance from the first point in the corresponding boundary
    float  distance;
    // simplify_travel() doesn't remove this point.
    bool do_not_remove = false;
};

struct ClosestLine
{
    // Index of the polygon containing this line.
    size_t border_idx;
    // Index of this line on the polygon containing it.
    size_t line_idx;
    // Closest point on the line.
    Point  point;
};

// Finding all intersections of a set of contours with a line segment.
struct AllIntersectionsVisitor
{
    AllIntersectionsVisitor(const EdgeGrid::Grid &grid, std::vector<Intersection> &intersections) : grid(grid), intersections(intersections)
    {
        intersection_set.reserve(intersections.capacity());
    }

    AllIntersectionsVisitor(const EdgeGrid::Grid &grid, std::vector<Intersection> &intersections, const Line &travel_line)
        : grid(grid), intersections(intersections), travel_line(travel_line)
    {
        intersection_set.reserve(intersections.capacity());
    }

    void reset() {
        intersection_set.clear();
    }

    bool operator()(coord_t iy, coord_t ix)
    {
        // Called with a row and column of the grid cell, which is intersected by a line.
        auto cell_data_range = grid.cell_data_range(iy, ix);
        for (auto it_contour_and_segment = cell_data_range.first; it_contour_and_segment != cell_data_range.second; ++it_contour_and_segment) {
            Point intersection_point;
            if (travel_line.intersection(grid.line(*it_contour_and_segment), &intersection_point) &&
                intersection_set.find(*it_contour_and_segment) == intersection_set.end()) {
                intersections.push_back({ it_contour_and_segment->first, it_contour_and_segment->second, intersection_point });
                intersection_set.insert(*it_contour_and_segment);
            }
        }
        // Continue traversing the grid along the edge.
        return true;
    }

    const EdgeGrid::Grid                                                                 &grid;
    std::vector<Intersection>                                                            &intersections;
    Line                                                                                  travel_line;
    std::unordered_set<std::pair<size_t, size_t>, boost::hash<std::pair<size_t, size_t>>> intersection_set;
};

// Visitor to check for any collision of a line segment with any contour stored inside the edge_grid.
struct FirstIntersectionVisitor
{
    explicit FirstIntersectionVisitor(const EdgeGrid::Grid &grid) : grid(grid) {}

    bool operator()(coord_t iy, coord_t ix)
    {
        assert(pt_current != nullptr);
        assert(pt_next != nullptr);
        // Called with a row and column of the grid cell, which is intersected by a line.
        auto cell_data_range = grid.cell_data_range(iy, ix);
        this->intersect      = false;
        for (auto it_contour_and_segment = cell_data_range.first; it_contour_and_segment != cell_data_range.second; ++it_contour_and_segment) {
            // End points of the line segment and their vector.
            auto segment = grid.segment(*it_contour_and_segment);
            if (Geometry::segments_intersect(segment.first, segment.second, *pt_current, *pt_next)) {
                this->intersect = true;
                return false;
            }
        }
        // Continue traversing the grid along the edge.
        return true;
    }

    const EdgeGrid::Grid &grid;
    const Slic3r::Point  *pt_current = nullptr;
    const Slic3r::Point  *pt_next    = nullptr;
    bool                  intersect  = false;
};

// Visitor to create a list of closet lines to a defined point.
struct MinDistanceVisitor
{
    explicit MinDistanceVisitor(const EdgeGrid::Grid &grid, const Point &center, double max_distance_squared)
        : grid(grid), center(center), max_distance_squared(max_distance_squared)
    {}

    void init()
    {
        this->closest_lines.clear();
        this->closest_lines_set.clear();
    }

    bool operator()(coord_t iy, coord_t ix)
    {
        // Called with a row and column of the grid cell, which is inside a bounding box.
        auto cell_data_range = grid.cell_data_range(iy, ix);
        for (auto it_contour_and_segment = cell_data_range.first; it_contour_and_segment != cell_data_range.second; ++it_contour_and_segment) {
            // End points of the line segment and their vector.
            auto  segment = grid.segment(*it_contour_and_segment);
            Point closest_point;
            if (closest_lines_set.find(*it_contour_and_segment) == closest_lines_set.end() &&
                line_alg::distance_to_squared(Line(segment.first, segment.second), center, &closest_point) <= this->max_distance_squared) {
                closest_lines.push_back({it_contour_and_segment->first, it_contour_and_segment->second, closest_point});
                closest_lines_set.insert(*it_contour_and_segment);
            }
        }
        // Continue traversing the grid along the edge.
        return true;
    }

    const EdgeGrid::Grid &                                                                grid;
    const Slic3r::Point                                                                   center;
    std::vector<ClosestLine>                                                              closest_lines;
    std::unordered_set<std::pair<size_t, size_t>, boost::hash<std::pair<size_t, size_t>>> closest_lines_set;
    double                                                                                max_distance_squared = std::numeric_limits<double>::max();
};

// Returns sorted list of closest lines to a passed point within a passed radius
static std::vector<ClosestLine> get_closest_lines_in_radius(const EdgeGrid::Grid &grid, const Point &center, float search_radius)
{
    Point              radius_vector(search_radius, search_radius);
    MinDistanceVisitor visitor(grid, center, search_radius * search_radius);
    grid.visit_cells_intersecting_box(BoundingBox(center - radius_vector, center + radius_vector), visitor);
    std::sort(visitor.closest_lines.begin(), visitor.closest_lines.end(), [&center](const auto &l, const auto &r) {
        return (center - l.point).template cast<double>().squaredNorm() < (center - r.point).template cast<double>().squaredNorm();
    });

    return visitor.closest_lines;
}

// When the offset is too big, then original travel doesn't have to cross created boundaries.
// For these cases, this function adds another intersection with lines around the start and the end point of the original travel.
static std::vector<Intersection> extend_for_closest_lines(const std::vector<Intersection>         &intersections,
                                                          const AvoidCrossingPerimeters::Boundary &boundary,
                                                          const Point                             &start,
                                                          const Point                             &end,
                                                          const float                              search_radius)
{
    const std::vector<ClosestLine> start_lines = get_closest_lines_in_radius(boundary.grid, start, search_radius);
    const std::vector<ClosestLine> end_lines   = get_closest_lines_in_radius(boundary.grid, end, search_radius);

    // Compute distance to the closest point in the ClosestLine from begin of contour.
    auto compute_distance = [&boundary](const ClosestLine &closest_line) -> float {
        float dist_from_line_begin = (closest_line.point - boundary.boundaries[closest_line.border_idx][closest_line.line_idx]).cast<float>().norm();
        return boundary.boundaries_params[closest_line.border_idx][closest_line.line_idx] + dist_from_line_begin;
    };

    // It tries to find closest lines for both start point and end point of the travel which has the same border_idx
    auto endpoints_close_to_same_boundary = [&start_lines, &end_lines]() -> std::pair<size_t, size_t> {
        std::unordered_set<size_t> boundaries_from_start;
        for (const ClosestLine &cl_start : start_lines)
            boundaries_from_start.insert(cl_start.border_idx);
        for (const ClosestLine &cl_end : end_lines)
            if (boundaries_from_start.find(cl_end.border_idx) != boundaries_from_start.end())
                for (const ClosestLine &cl_start : start_lines)
                    if (cl_start.border_idx == cl_end.border_idx) {
                        size_t cl_start_idx = &cl_start - &start_lines.front();
                        size_t cl_end_idx   = &cl_end - &end_lines.front();
                        return std::make_pair(cl_start_idx, cl_end_idx);
                    }
        return std::make_pair(std::numeric_limits<size_t>::max(), std::numeric_limits<size_t>::max());
    };

    // If the existing two lines within the search radius start and end point belong to the same boundary,
    // discard all intersection points because the whole detour could be on one boundary.
    if (!start_lines.empty() && !end_lines.empty()) {
        std::pair<size_t, size_t> cl_indices = endpoints_close_to_same_boundary();
        if (cl_indices.first != std::numeric_limits<size_t>::max()) {
            assert(cl_indices.second != std::numeric_limits<size_t>::max());
            const ClosestLine &cl_start = start_lines[cl_indices.first];
            const ClosestLine &cl_end   = end_lines[cl_indices.second];
            std::vector<Intersection> new_intersections;
            new_intersections.push_back({cl_start.border_idx, cl_start.line_idx, cl_start.point, compute_distance(cl_start), true});
            new_intersections.push_back({cl_end.border_idx, cl_end.line_idx, cl_end.point, compute_distance(cl_end), true});
            return new_intersections;
        }
    }

    // Returns ClosestLine which is closer to the point "close_to" then point inside passed Intersection.
    auto get_closer = [&search_radius](const std::vector<ClosestLine> &closest_lines, const Intersection &intersection,
                                       const Point &close_to) -> size_t {
        for (const ClosestLine &cl : closest_lines) {
            double old_dist = (close_to - intersection.point).cast<float>().squaredNorm();
            if (cl.border_idx == intersection.border_idx && old_dist <= (search_radius * search_radius) &&
                (close_to - cl.point).cast<float>().squaredNorm() < old_dist)
                return &cl - &closest_lines.front();
        }
        return std::numeric_limits<size_t>::max();
    };

    // Try to find ClosestLine with same boundary_idx as any existing Intersection
    auto find_closest_line_with_same_boundary_idx = [](const std::vector<ClosestLine> & closest_lines,
                                                       const std::vector<Intersection> &intersections, const bool reverse) -> size_t {
        std::unordered_set<size_t> boundaries_indices;
        for (const ClosestLine &closest_line : closest_lines)
            boundaries_indices.insert(closest_line.border_idx);

        // This function must be called only in the case that exists closest_line with boundary_idx equals to intersection.border_idx
        auto find_closest_line_index = [&closest_lines](const Intersection &intersection) -> size_t {
            for (const ClosestLine &closest_line : closest_lines)
                if (closest_line.border_idx == intersection.border_idx) return &closest_line - &closest_lines.front();
            // This is an invalid state.
            assert(false);
            return std::numeric_limits<size_t>::max();
        };

        if (reverse) {
            for (const Intersection &intersection : boost::adaptors::reverse(intersections))
                if (boundaries_indices.find(intersection.border_idx) != boundaries_indices.end())
                    return find_closest_line_index(intersection);
        } else {
            for (const Intersection &intersection : intersections)
                if (boundaries_indices.find(intersection.border_idx) != boundaries_indices.end())
                    return find_closest_line_index(intersection);
        }
        return std::numeric_limits<size_t>::max();
    };

    std::vector<Intersection> new_intersections = intersections;
    if (!new_intersections.empty() && !start_lines.empty()) {
        size_t cl_start_idx = get_closer(start_lines, new_intersections.front(), start);
        if (cl_start_idx != std::numeric_limits<size_t>::max()) {
            // If there is any ClosestLine around the start point closer to the Intersection, then replace this Intersection with ClosestLine.
            const ClosestLine &cl_start = start_lines[cl_start_idx];
            new_intersections.front()   = {cl_start.border_idx, cl_start.line_idx, cl_start.point, compute_distance(cl_start), true};
        } else {
            // Check if there is any ClosestLine with the same boundary_idx as any Intersection. If this ClosestLine exists, then add it to the
            // vector of intersections. This allows in some cases when it is more than one around ClosestLine start point chose that one which
            // minimizes the number of contours (also length of the detour) in result detour. If there doesn't exist any ClosestLine like this, then
            // use the first one, which is the closest one to the start point.
            size_t             start_closest_lines_idx = find_closest_line_with_same_boundary_idx(start_lines, new_intersections, true);
            const ClosestLine &cl_start                = (start_closest_lines_idx != std::numeric_limits<size_t>::max()) ? start_lines[start_closest_lines_idx] : start_lines.front();
            new_intersections.insert(new_intersections.begin(), {cl_start.border_idx, cl_start.line_idx, cl_start.point, compute_distance(cl_start), true});
        }
    }

    if (!new_intersections.empty() && !end_lines.empty()) {
        size_t cl_end_idx = get_closer(end_lines, new_intersections.back(), end);
        if (cl_end_idx != std::numeric_limits<size_t>::max()) {
            // If there is any ClosestLine around the end point closer to the Intersection, then replace this Intersection with ClosestLine.
            const ClosestLine &cl_end = end_lines[cl_end_idx];
            new_intersections.back()  = {cl_end.border_idx, cl_end.line_idx, cl_end.point, compute_distance(cl_end), true};
        } else {
            // Check if there is any ClosestLine with the same boundary_idx as any Intersection. If this ClosestLine exists, then add it to the
            // vector of intersections. This allows in some cases when it is more than one around ClosestLine end point chose that one which
            // minimizes the number of contours (also length of the detour) in result detour. If there doesn't exist any ClosestLine like this, then
            // use the first one, which is the closest one to the end point.
            size_t             end_closest_lines_idx = find_closest_line_with_same_boundary_idx(end_lines, new_intersections, false);
            const ClosestLine &cl_end                = (end_closest_lines_idx != std::numeric_limits<size_t>::max()) ? end_lines[end_closest_lines_idx] : end_lines.front();
            new_intersections.push_back({cl_end.border_idx, cl_end.line_idx, cl_end.point, compute_distance(cl_end), true});
        }
    }
    return new_intersections;
}

// point_idx is the index from which is different vertex is searched.
template<bool forward>
static Point find_first_different_vertex(const Polygon &polygon, const size_t point_idx, const Point &point)
{
    assert(point_idx < polygon.size());
    // Solve case when vertex on passed index point_idx is different that pass point. This helps the following code keep simple.
    if (point != polygon.points[point_idx])
        return polygon.points[point_idx];

    auto line_idx = (int(point_idx) + 1) % int(polygon.points.size());
    assert(line_idx != int(point_idx));
    if constexpr (forward)
        for (; point == polygon.points[line_idx] && line_idx != int(point_idx); line_idx = line_idx + 1 < int(polygon.points.size()) ? line_idx + 1 : 0);
    else
        for (; point == polygon.points[line_idx] && line_idx != int(point_idx); line_idx = line_idx - 1 >= 0 ? line_idx - 1 : int(polygon.points.size()) - 1);
    assert(point != polygon.points[line_idx]);
    return polygon.points[line_idx];
}

static Vec2d three_points_inward_normal(const Point &left, const Point &middle, const Point &right)
{
    assert(left != middle);
    assert(middle != right);
    return (perp(Point(middle - left)).cast<double>().normalized() + perp(Point(right - middle)).cast<double>().normalized()).normalized();
}

// Compute normal of the polygon's vertex in an inward direction
static Vec2d get_polygon_vertex_inward_normal(const Polygon &polygon, const size_t point_idx)
{
    const size_t left_idx  = prev_idx_modulo(point_idx, polygon.points);
    const size_t right_idx = next_idx_modulo(point_idx, polygon.points);
    const Point &middle    = polygon.points[point_idx];
    const Point &left      = find_first_different_vertex<false>(polygon, left_idx, middle);
    const Point &right     = find_first_different_vertex<true>(polygon, right_idx, middle);
    return three_points_inward_normal(left, middle, right);
}

// Compute offset of point_idx of the polygon in a direction of inward normal
static Point get_polygon_vertex_offset(const Polygon &polygon, const size_t point_idx, const int offset)
{
    return polygon.points[point_idx] + (get_polygon_vertex_inward_normal(polygon, point_idx) * double(offset)).cast<coord_t>();
}

// Compute offset (in the direction of inward normal) of the point(passed on "middle") based on the nearest points laying on the polygon (left_idx and right_idx).
static Point get_middle_point_offset(const Polygon &polygon, const size_t left_idx, const size_t right_idx, const Point &middle, const coord_t offset)
{
    const Point &left  = find_first_different_vertex<false>(polygon, left_idx, middle);
    const Point &right = find_first_different_vertex<true>(polygon, right_idx, middle);
    return middle + (three_points_inward_normal(left, middle, right) * double(offset)).cast<coord_t>();
}

static Polyline to_polyline(const std::vector<TravelPoint> &travel)
{
    Polyline result;
    result.points.reserve(travel.size());
    for (const TravelPoint &t_point : travel)
        result.append(t_point.point);
    return result;
}

// #define AVOID_CROSSING_PERIMETERS_DEBUG_OUTPUT

#ifdef AVOID_CROSSING_PERIMETERS_DEBUG_OUTPUT
static void export_travel_to_svg(const Polygons                  &boundary,
                                 const Line                      &original_travel,
                                 const Polyline                  &result_travel,
                                 const std::vector<Intersection> &intersections,
                                 const std::string               &path)
{
    BoundingBox   bbox = get_extents(boundary);
    ::Slic3r::SVG svg(path, bbox);
    svg.draw_outline(boundary, "green");
    svg.draw(original_travel, "blue");
    svg.draw(result_travel, "red");
    svg.draw(original_travel.a, "black");
    svg.draw(original_travel.b, "grey");

    for (const Intersection &intersection : intersections)
        svg.draw(intersection.point, "lightseagreen");
}

static void export_travel_to_svg(const Polygons                  &boundary,
                                 const Line                      &original_travel,
                                 const std::vector<TravelPoint>  &result_travel,
                                 const std::vector<Intersection> &intersections,
                                 const std::string               &path)
{
    export_travel_to_svg(boundary, original_travel, to_polyline(result_travel), intersections, path);
}
#endif /* AVOID_CROSSING_PERIMETERS_DEBUG_OUTPUT */

// Returns a direction of the shortest path along the polygon boundary
enum class Direction { Forward, Backward };
// Returns a direction of the shortest path along the polygon boundary
static Direction get_shortest_direction(const AvoidCrossingPerimeters::Boundary &boundary,
                                        const Intersection                      &intersection_first,
                                        const Intersection                      &intersection_second,
                                        float                                    contour_length)
{
    assert(intersection_first.border_idx == intersection_second.border_idx);
    const Polygon &poly        = boundary.boundaries[intersection_first.border_idx];
    float          dist_first  = intersection_first.distance;
    float          dist_second = intersection_second.distance;

    assert(dist_first  >= 0.f && dist_first  <= contour_length);
    assert(dist_second >= 0.f && dist_second <= contour_length);

    bool reversed = false;
    if (dist_first > dist_second) {
        std::swap(dist_first, dist_second);
        reversed = true;
    }
    float total_length_forward  = dist_second - dist_first;
    float total_length_backward = dist_first + contour_length - dist_second;
    if (reversed) std::swap(total_length_forward, total_length_backward);

    total_length_forward  -= (intersection_first.point - poly[intersection_first.line_idx]).cast<float>().norm();
    total_length_backward -= (poly[(intersection_first.line_idx + 1) % poly.size()] - intersection_first.point).cast<float>().norm();

    total_length_forward  -= (poly[(intersection_second.line_idx + 1) % poly.size()] - intersection_second.point).cast<float>().norm();
    total_length_backward -= (intersection_second.point - poly[intersection_second.line_idx]).cast<float>().norm();

    if (total_length_forward < total_length_backward) return Direction::Forward;
    return Direction::Backward;
}

Polyline ConvertBBoxToPolyline(const BoundingBoxf &bbox)
{
    Point left_bottom = bbox.min.cast<coord_t>();
    Point left_up(bbox.min.cast<coord_t>()[0], bbox.max.cast<coord_t>()[1]);
    Point right_up = bbox.max.cast<coord_t>();
    Point right_bottom(bbox.max.cast<coord_t>()[0], bbox.min.cast<coord_t>()[1]);

    return Polyline({left_bottom, right_bottom, right_up, left_up, left_bottom});
}


// Straighten the travel path as long as it does not collide with the contours stored in edge_grid.
static std::vector<TravelPoint> simplify_travel(const AvoidCrossingPerimeters::Boundary &boundary, const std::vector<TravelPoint> &travel)
{
    FirstIntersectionVisitor visitor(boundary.grid);
    std::vector<TravelPoint> simplified_path;
    simplified_path.reserve(travel.size());
    simplified_path.emplace_back(travel.front());
    // Try to skip some points in the path.
    //FIXME maybe use a binary search to trim the line?
    //FIXME how about searching tangent point at long segments? 
    for (size_t point_idx = 1; point_idx < travel.size(); ++point_idx) {
        const Point &current_point = travel[point_idx - 1].point;
        TravelPoint  next          = travel[point_idx];

        visitor.pt_current = &current_point;

        if (!next.do_not_remove)
            for (size_t point_idx_2 = point_idx + 1; point_idx_2 < travel.size(); ++point_idx_2) {
            // Workaround for some issue in MSVC 19.29.30037 32-bit compiler.
#if defined(_WIN32) && !defined(_WIN64)
                if (bool volatile do_not_remove = travel[point_idx_2].do_not_remove; do_not_remove) break;
#else
                if (travel[point_idx_2].do_not_remove) break;
#endif
                if (travel[point_idx_2].point == current_point) {
                    next      = travel[point_idx_2];
                    point_idx = point_idx_2;
                    continue;
                }

                visitor.pt_next = &travel[point_idx_2].point;
                boundary.grid.visit_cells_intersecting_line(*visitor.pt_current, *visitor.pt_next, visitor);
                // Check if deleting point causes crossing a boundary
                if (!visitor.intersect) {
                    next      = travel[point_idx_2];
                    point_idx = point_idx_2;
                }
            }

        simplified_path.emplace_back(next);
    }

    return simplified_path;
}

// called by get_perimeter_spacing() / get_perimeter_spacing_external()
static inline float get_default_perimeter_spacing(const PrintObject &print_object)
{
    std::vector<unsigned int> printing_extruders = print_object.object_extruders();
    assert(!printing_extruders.empty());
    float avg_extruder = 0;
    for(unsigned int extruder_id : printing_extruders)
        avg_extruder += float(scale_(print_object.print()->config().nozzle_diameter.get_at(extruder_id)));
    avg_extruder /= printing_extruders.size();
    return avg_extruder;
}

// called by get_boundary() / avoid_perimeters_inner()
static float get_perimeter_spacing(const Layer &layer)
{
    size_t regions_count     = 0;
    float  perimeter_spacing = 0.f;
    for (const LayerRegion *layer_region : layer.regions())
        if (layer_region != nullptr && !layer_region->slices.empty()) {
            perimeter_spacing += layer_region->flow(frPerimeter).scaled_spacing();
            ++regions_count;
        }

    assert(perimeter_spacing >= 0.f);
    if (regions_count != 0)
        perimeter_spacing /= float(regions_count);
    else
        perimeter_spacing = get_default_perimeter_spacing(*layer.object());
    return perimeter_spacing;
}

// called by get_boundary_external()
static float get_perimeter_spacing_external(const Layer &layer)
{
    size_t regions_count     = 0;
    float  perimeter_spacing = 0.f;
    for (const PrintObject *object : layer.object()->print()->objects())
        if (const Layer *l = object->get_layer_at_printz(layer.print_z, EPSILON); l)
            for (const LayerRegion *layer_region : l->regions())
                if (layer_region != nullptr && !layer_region->slices.empty()) {
                    perimeter_spacing += layer_region->flow(frPerimeter).scaled_spacing();
                    ++ regions_count;
                }

    assert(perimeter_spacing >= 0.f);
    if (regions_count != 0)
        perimeter_spacing /= float(regions_count);
    else
        perimeter_spacing = get_default_perimeter_spacing(*layer.object());
    return perimeter_spacing;
}

static float get_external_perimeter_width(const Layer &layer)
{
    size_t regions_count   = 0;
    float  perimeter_width = 0.f;
    for (const LayerRegion *layer_region : layer.regions())
        if (layer_region != nullptr && !layer_region->slices.empty()) {
            perimeter_width += float(layer_region->flow(frExternalPerimeter).scaled_width());
            ++regions_count;
        }

    assert(perimeter_width >= 0.f);
    if (regions_count != 0)
        perimeter_width /= float(regions_count);
    else
        perimeter_width = get_default_perimeter_spacing(*layer.object());
    return perimeter_width;
}

static float get_internal_perimeter_width(const Layer &layer)
{
    size_t regions_count   = 0;
    float  perimeter_width = 0.f;
    for (const LayerRegion *layer_region : layer.regions())
        if (layer_region != nullptr && !layer_region->slices.empty()) {
            perimeter_width += float(layer_region->flow(frPerimeter).scaled_width());
            ++regions_count;
        }

    assert(perimeter_width >= 0.f);
    if (regions_count != 0)
        return perimeter_width / float(regions_count);
    return get_external_perimeter_width(layer);
}

static size_t avoid_perimeters_inner(
    const AvoidCrossingPerimeters::Boundary &boundary, const Point &start_point, const Point &end_point, const Layer &layer, std::vector<TravelPoint> &result_out)
{
    const Polygons       &boundaries = boundary.boundaries;
    const EdgeGrid::Grid &edge_grid  = boundary.grid;
    Point                 start = start_point, end = end_point;
    // Find all intersections between boundaries and the line segment, sort them along the line segment.
    std::vector<Intersection> intersections;
    {
        intersections.reserve(boundaries.size());
        AllIntersectionsVisitor visitor(edge_grid, intersections, Line(start, end));
        edge_grid.visit_cells_intersecting_line(start, end, visitor);
        Vec2d dir = (end - start).cast<double>();
        // if do not intersect due to the boundaries inner-offset, try to find the closest point to do intersect again!
        if (intersections.empty()) {
            // try to find the closest point on boundaries to start/end with distance less than extend_distance, which is noted as new start_point/end_point
            auto                           search_radius         = 1.5 * get_perimeter_spacing(layer);
            const std::vector<ClosestLine> closest_line_to_start = get_closest_lines_in_radius(boundary.grid, start, search_radius);
            const std::vector<ClosestLine> closest_line_to_end   = get_closest_lines_in_radius(boundary.grid, end, search_radius);
            if (!(closest_line_to_start.empty() && closest_line_to_end.empty())) {
                auto new_start_point = closest_line_to_start.empty() ? start : closest_line_to_start.front().point;
                auto new_end_point   = closest_line_to_end.empty() ? end : closest_line_to_end.front().point;
                dir                  = (new_end_point - new_start_point).cast<double>();
                auto unit_direction  = dir.normalized();
                // out-offset new_start_point/new_end_point epsilon along the Line(new_start_point, new_end_point) for right intersection!
                new_start_point = new_start_point - (unit_direction * double(coord_t(SCALED_EPSILON))).cast<coord_t>();
                new_end_point   = new_end_point + (unit_direction * double(coord_t(SCALED_EPSILON))).cast<coord_t>();
                AllIntersectionsVisitor visitor(edge_grid, intersections, Line(new_start_point, new_end_point));
                edge_grid.visit_cells_intersecting_line(new_start_point, new_end_point, visitor);
                if (!intersections.empty()) {
                    start = new_start_point;
                    end   = new_end_point;
                }
            }
        }

        for (Intersection &intersection : intersections) {
            float dist_from_line_begin = (intersection.point - boundary.boundaries[intersection.border_idx][intersection.line_idx]).cast<float>().norm();
            intersection.distance      = boundary.boundaries_params[intersection.border_idx][intersection.line_idx] + dist_from_line_begin;
        }
        std::sort(intersections.begin(), intersections.end(), [dir](const auto &l, const auto &r) { return (r.point - l.point).template cast<double>().dot(dir) > 0.; });

        // Search radius should always be at least equals to the value of offset used for computing boundaries.
        const float search_radius = 2.f * get_perimeter_spacing(layer);
        // When the offset is too big, then original travel doesn't have to cross created boundaries.
        // These cases are fixed by calling extend_for_closest_lines.
        intersections = extend_for_closest_lines(intersections, boundary, start, end, search_radius);
    }

    std::vector<TravelPoint> result;
    result.push_back({start, -1});

#if 0
    auto crossing_boundary_from_inside = [&boundary](const Point &start, const Intersection &intersection) {
        const Polygon &poly             = boundary.boundaries[intersection.border_idx];
        Vec2d          poly_line        = Line(poly[intersection.line_idx], poly[(intersection.line_idx + 1) % poly.size()]).normal().cast<double>();
        Vec2d          intersection_vec = (intersection.point - start).cast<double>();
        return poly_line.normalized().dot(intersection_vec.normalized()) >= 0;
    };
#endif

    for (auto it_first = intersections.begin(); it_first != intersections.end(); ++it_first) {
        // The entry point to the boundary polygon
        const Intersection &intersection_first = *it_first;
        //        if(!crossing_boundary_from_inside(start, intersection_first))
        //            continue;
        // Skip the it_first from the search for the farthest exit point from the boundary polygon
        auto it_last_item = std::make_reverse_iterator(it_first) - 1;
        // Search for the farthest intersection different from it_first but with the same border_idx
        auto it_second_r = std::find_if(intersections.rbegin(), it_last_item,
                                        [&intersection_first](const Intersection &intersection) { return intersection_first.border_idx == intersection.border_idx; });

        // Append the first intersection into the path
        size_t left_idx  = intersection_first.line_idx;
        size_t right_idx = intersection_first.line_idx + 1 == boundaries[intersection_first.border_idx].points.size() ? 0 : intersection_first.line_idx + 1;
        // Offset of the polygon's point using get_middle_point_offset is used to simplify the calculation of intersection between the
        // boundary and the travel. The appended point is translated in the direction of inward normal. This translation ensures that the
        // appended point will be inside the polygon and not on the polygon border.
        result.push_back({get_middle_point_offset(boundaries[intersection_first.border_idx], left_idx, right_idx, intersection_first.point, coord_t(SCALED_EPSILON)),
                          int(intersection_first.border_idx), intersection_first.do_not_remove});

        // Check if intersection line also exit the boundary polygon
        if (it_second_r != it_last_item) {
            // Transform reverse iterator to forward
            auto it_second = it_second_r.base() - 1;
            // The exit point from the boundary polygon
            const Intersection &intersection_second = *it_second;
            Direction           shortest_direction  = get_shortest_direction(boundary, intersection_first, intersection_second,
                                                                             boundary.boundaries_params[intersection_first.border_idx].back());
            // Append the path around the border into the path
            if (shortest_direction == Direction::Forward)
                for (int line_idx = int(intersection_first.line_idx); line_idx != int(intersection_second.line_idx);
                     line_idx     = line_idx + 1 < int(boundaries[intersection_first.border_idx].size()) ? line_idx + 1 : 0)
                    result.push_back(
                        {get_polygon_vertex_offset(boundaries[intersection_first.border_idx],
                                                   (line_idx + 1 == int(boundaries[intersection_first.border_idx].points.size())) ? 0 : (line_idx + 1), coord_t(SCALED_EPSILON)),
                             int(intersection_first.border_idx)});
            else
                for (int line_idx = int(intersection_first.line_idx); line_idx != int(intersection_second.line_idx);
                     line_idx     = line_idx - 1 >= 0 ? line_idx - 1 : int(boundaries[intersection_first.border_idx].size()) - 1)
                    result.push_back(
                        {get_polygon_vertex_offset(boundaries[intersection_second.border_idx], line_idx + 0, coord_t(SCALED_EPSILON)), int(intersection_first.border_idx)});

            // Append the farthest intersection into the path
            left_idx  = intersection_second.line_idx;
            right_idx = (intersection_second.line_idx >= (boundaries[intersection_second.border_idx].points.size() - 1)) ? 0 : (intersection_second.line_idx + 1);
            result.push_back({get_middle_point_offset(boundaries[intersection_second.border_idx], left_idx, right_idx, intersection_second.point, coord_t(SCALED_EPSILON)),
                              int(intersection_second.border_idx), intersection_second.do_not_remove});
            // Skip intersections in between
            it_first = it_second;
        }
    }

    result.push_back({end, -1});

    auto result_polyline = to_polyline(result);
    (void) result_polyline;

#ifdef AVOID_CROSSING_PERIMETERS_DEBUG_OUTPUT
    {
        static int iRun = 0;
        export_travel_to_svg(boundaries, Line(start, end), result, intersections, debug_out_path("AvoidCrossingPerimetersInner-initial-%d-%d.svg", layer.id(), iRun++));
    }
#endif /* AVOID_CROSSING_PERIMETERS_DEBUG_OUTPUT */

    if (!intersections.empty()) result = simplify_travel(boundary, result);

    auto simplified_result_polyline = to_polyline(result);
    (void) simplified_result_polyline;

#ifdef AVOID_CROSSING_PERIMETERS_DEBUG_OUTPUT
    {
        static int iRun = 0;
        export_travel_to_svg(boundaries, Line(start, end), result, intersections, debug_out_path("AvoidCrossingPerimetersInner-final-%d-%d.svg", layer.id(), iRun++));
    }
#endif /* AVOID_CROSSING_PERIMETERS_DEBUG_OUTPUT */

    append(result_out, std::move(result));
    return intersections.size();
}

// Called by AvoidCrossingPerimeters::travel_to()
static size_t avoid_perimeters(const AvoidCrossingPerimeters::Boundary &boundary,
                               const Point                             &start,
                               const Point                             &end,
                               const Layer                             &layer,
                               Polyline                                &result_out)
{
    // Travel line is completely or partially inside the bounding box.
    std::vector<TravelPoint> path;
    size_t num_intersections = avoid_perimeters_inner(boundary, start, end, layer, path);
    result_out = to_polyline(path);


#ifdef AVOID_CROSSING_PERIMETERS_DEBUG_OUTPUT
    {
        static int iRun = 0;
        export_travel_to_svg(boundary.boundaries, Line(start, end), path, {}, debug_out_path("AvoidCrossingPerimeters-final-%d-%d.svg", layer.id(), iRun ++));
    }
#endif /* AVOID_CROSSING_PERIMETERS_DEBUG_OUTPUT */

    return num_intersections;
}

// Check if anyone of ExPolygons contains whole travel.
// called by need_wipe() and AvoidCrossingPerimeters::travel_to()
// FIXME Lukas H.: Maybe similar approach could also be used for ExPolygon::contains()
static bool any_expolygon_contains(const ExPolygons               &ex_polygons,
                                   const std::vector<BoundingBox> &ex_polygons_bboxes,
                                   const EdgeGrid::Grid            &grid_lslice,
                                   const Line                      &travel)
{
    assert(ex_polygons.size() == ex_polygons_bboxes.size());
    if(!grid_lslice.bbox().contains(travel.a) || !grid_lslice.bbox().contains(travel.b))
        return false;

    FirstIntersectionVisitor visitor(grid_lslice);
    visitor.pt_current = &travel.a;
    visitor.pt_next    = &travel.b;
    grid_lslice.visit_cells_intersecting_line(*visitor.pt_current, *visitor.pt_next, visitor);
    if (!visitor.intersect) {
        for (const ExPolygon &ex_polygon : ex_polygons) {
            const BoundingBox &bbox = ex_polygons_bboxes[&ex_polygon - &ex_polygons.front()];
            if (bbox.contains(travel.a) && bbox.contains(travel.b) && ex_polygon.contains(travel.a))
                return true;
        }
    }
    return false;
}

// Check if anyone of ExPolygons contains whole travel.
// called by need_wipe()
static bool any_expolygon_contains(const ExPolygons &ex_polygons, const std::vector<BoundingBox> &ex_polygons_bboxes, const EdgeGrid::Grid &grid_lslice, const Polyline &travel)
{
    assert(ex_polygons.size() == ex_polygons_bboxes.size());
    if(std::any_of(travel.points.begin(), travel.points.end(), [&grid_lslice](const Point &point) { return !grid_lslice.bbox().contains(point); }))
        return false;

    FirstIntersectionVisitor visitor(grid_lslice);
    bool any_intersection = false;
    for (size_t line_idx = 1; line_idx < travel.size(); ++line_idx) {
        visitor.pt_current = &travel.points[line_idx - 1];
        visitor.pt_next    = &travel.points[line_idx];
        grid_lslice.visit_cells_intersecting_line(*visitor.pt_current, *visitor.pt_next, visitor);
        any_intersection = visitor.intersect;
        if (any_intersection) break;
    }

    if (!any_intersection) {
        for (const ExPolygon &ex_polygon : ex_polygons) {
            const BoundingBox &bbox = ex_polygons_bboxes[&ex_polygon - &ex_polygons.front()];
            if (std::all_of(travel.points.begin(), travel.points.end(), [&bbox](const Point &point) { return bbox.contains(point); }) &&
                ex_polygon.contains(travel.points.front()))
                return true;
        }
    }
    return false;
}

static bool need_wipe(const GCode                    &gcodegen,
                      const ExPolygons               &lslices_offset,
                      const std::vector<BoundingBox> &lslices_offset_bboxes,
                      const EdgeGrid::Grid           &grid_lslices_offset,
                      const Line                     &original_travel,
                      const Polyline                 &result_travel,
                      const size_t                    intersection_count)
{
    bool z_lift_enabled = gcodegen.config().z_hop.get_at(gcodegen.writer().filament()->id()) > 0.;
    bool wipe_needed    = false;

    // If the original unmodified path doesn't have any intersection with boundary, then it is entirely inside the object otherwise is entirely
    // outside the object.
    if (intersection_count > 0) {
        // The original layer is intersected with defined boundaries. Then it is necessary to make a detailed test.
        // If the z-lift is enabled, then a wipe is needed when the original travel leads above the holes.
        if (z_lift_enabled) {
            if (any_expolygon_contains(lslices_offset, lslices_offset_bboxes, grid_lslices_offset, original_travel)) {
                // Check if original_travel and result_travel are not same.
                // If both are the same, then it is possible to skip testing of result_travel
                wipe_needed = !(result_travel.size() > 2 && result_travel.first_point() == original_travel.a && result_travel.last_point() == original_travel.b) &&
                              !any_expolygon_contains(lslices_offset, lslices_offset_bboxes, grid_lslices_offset, result_travel);
            } else {
                wipe_needed = true;
            }
        } else {
            wipe_needed = !any_expolygon_contains(lslices_offset, lslices_offset_bboxes, grid_lslices_offset, result_travel);
        }
    }

    return wipe_needed;
}

// Adds points around all vertices so that the offset affects only small sections around these vertices.
static void resample_polygon(Polygon &polygon, double dist_from_vertex, double max_allowed_distance)
{
    Points resampled_poly;
    resampled_poly.reserve(3 * polygon.size());
    for (size_t pt_idx = 0; pt_idx < polygon.size(); ++pt_idx) {
        resampled_poly.emplace_back(polygon[pt_idx]);

        const Point &p1          = polygon[pt_idx];
        const Point &p2          = polygon[next_idx_modulo(pt_idx, polygon.size())];
        const Vec2d  line_vec    = (p2 - p1).cast<double>();
        double       line_length = line_vec.norm();
        const Vector vertex_offset_vec = (line_vec.normalized() * dist_from_vertex).cast<coord_t>();
        if (line_length > 2 * dist_from_vertex && vertex_offset_vec != Vector(0, 0)) {
            resampled_poly.emplace_back(p1 + vertex_offset_vec);

            const Vec2d  new_vertex_vec        = (p2 - p1 - 2 * vertex_offset_vec).cast<double>();
            const double new_vertex_vec_length = new_vertex_vec.norm();
            if (new_vertex_vec_length > max_allowed_distance) {
                const Vec2d &prev_point  = resampled_poly.back().cast<double>();
                const size_t parts_count = size_t(ceil(new_vertex_vec_length / max_allowed_distance));
                for (size_t part_idx = 1; part_idx < parts_count; ++part_idx) {
                    const double part_param = double(part_idx) / double(parts_count);
                    const Vec2d  new_point  = prev_point + new_vertex_vec * part_param;
                    resampled_poly.emplace_back(new_point.cast<coord_t>());
                }
            }

            resampled_poly.emplace_back(p2 - vertex_offset_vec);
        }
    }
    polygon.points = std::move(resampled_poly);
}

static void resample_expolygon(ExPolygon &ex_polygon, double dist_from_vertex, double max_allowed_distance)
{
    resample_polygon(ex_polygon.contour, dist_from_vertex, max_allowed_distance);
    for (Polygon &polygon : ex_polygon.holes)
        resample_polygon(polygon, dist_from_vertex, max_allowed_distance);
}

static void resample_expolygons(ExPolygons &ex_polygons, double dist_from_vertex, double max_allowed_distance)
{
    for (ExPolygon &ex_poly : ex_polygons)
        resample_expolygon(ex_poly, dist_from_vertex, max_allowed_distance);
}

static void precompute_polygon_distances(const Polygon &polygon, std::vector<float> &polygon_distances_out)
{
    polygon_distances_out.assign(polygon.size() + 1, 0.f);
    for (size_t point_idx = 1; point_idx < polygon.size(); ++point_idx)
        polygon_distances_out[point_idx] = polygon_distances_out[point_idx - 1] + float((polygon[point_idx] - polygon[point_idx - 1]).cast<double>().norm());
    polygon_distances_out.back() = polygon_distances_out[polygon.size() - 1] + float((polygon.points.back() - polygon.points.front()).cast<double>().norm());
}

static void precompute_expolygon_distances(const ExPolygon &ex_polygon, std::vector<std::vector<float>> &expolygon_distances_out)
{
    expolygon_distances_out.assign(ex_polygon.holes.size() + 1, std::vector<float>());
    precompute_polygon_distances(ex_polygon.contour, expolygon_distances_out.front());
    for (size_t hole_idx = 0; hole_idx < ex_polygon.holes.size(); ++hole_idx)
        precompute_polygon_distances(ex_polygon.holes[hole_idx], expolygon_distances_out[hole_idx + 1]);
}

// It is highly based on the function contour_distance2 from the ElephantFootCompensation.cpp
static std::vector<float> contour_distance(const EdgeGrid::Grid     &grid,
                                    const std::vector<float> &poly_distances,
                                    const size_t              contour_idx,
                                    const Polygon            &polygon,
                                    double                    compensation,
                                    double                    search_radius)
{
    assert(! polygon.empty());
    assert(polygon.size() >= 2);

    std::vector<float> out;

    if (polygon.size() > 2)
    {
        struct Visitor {
            Visitor(const EdgeGrid::Grid &grid, const size_t contour_idx, const std::vector<float> &polygon_distances, double dist_same_contour_accept, double dist_same_contour_reject) :
                grid(grid), idx_contour(contour_idx), contour(grid.contours()[contour_idx]), boundary_parameters(polygon_distances), dist_same_contour_accept(dist_same_contour_accept), dist_same_contour_reject(dist_same_contour_reject) {}

            void init(const Points &contour, const Point &apoint)
            {
                this->idx_point  = &apoint - contour.data();
                this->point      = apoint;
                this->found      = false;
                this->dir_inside = this->dir_inside_at_point(contour, this->idx_point);
                this->distance   = std::numeric_limits<double>::max();
            }

            bool operator()(coord_t iy, coord_t ix)
            {
                // Called with a row and colum of the grid cell, which is intersected by a line.
                auto cell_data_range = this->grid.cell_data_range(iy, ix);
                for (auto it_contour_and_segment = cell_data_range.first; it_contour_and_segment != cell_data_range.second;
                     ++it_contour_and_segment) {
                    // End points of the line segment and their vector.
                    std::pair<const Point &, const Point &> segment = this->grid.segment(*it_contour_and_segment);
                    const Vec2d  v        = (segment.second - segment.first).cast<double>();
                    const Vec2d  va       = (this->point - segment.first).cast<double>();
                    const double l2       = v.squaredNorm(); // avoid a sqrt
                    const double t        = (l2 == 0.0) ? 0. : std::clamp(va.dot(v) / l2, 0., 1.);
                    // Closest point from this->point to the segment.
                    const Vec2d  foot     = segment.first.cast<double>() + t * v;
                    const Vec2d  bisector = foot - this->point.cast<double>();
                    const double dist     = bisector.norm();

                    if ((!this->found || dist < this->distance) && this->dir_inside.dot(bisector) > 0) {
                        bool accept = true;
                        if (it_contour_and_segment->first == idx_contour) {
                            // Complex case: The closest segment originates from the same contour as the starting point.
                            // Reject the closest point if its distance along the contour is reasonable compared to the current contour bisector
                            // (this->pt, foot).
                            const EdgeGrid::Contour &contour   = grid.contours()[it_contour_and_segment->first];
                            double                   param_lo  = boundary_parameters[this->idx_point];
                            double                   param_hi  = t * sqrt(l2);
                            double                   param_end = boundary_parameters.back();
                            const size_t ipt = it_contour_and_segment->second;
                            if (contour.begin() + ipt + 1 < contour.end())
                                param_hi += boundary_parameters[ipt];
                            if (param_lo > param_hi)
                                std::swap(param_lo, param_hi);
                            assert(param_lo > -SCALED_EPSILON && param_lo <= param_end + SCALED_EPSILON);
                            assert(param_hi > -SCALED_EPSILON && param_hi <= param_end + SCALED_EPSILON);
                            double dist_along_contour = std::min(param_hi - param_lo, param_lo + param_end - param_hi);
                            if (dist_along_contour < dist_same_contour_accept)
                                accept = false;
                            else if (dist < dist_same_contour_reject + SCALED_EPSILON) {
                                // this->point is close to foot. This point will only be accepted if the path along the contour is significantly
                                // longer than the bisector. That is, the path shall not bulge away from the bisector too much.
                                // Bulge is estimated by 0.6 of the circle circumference drawn around the bisector.
                                // Test whether the contour is convex or concave.
                                bool inside = (t == 0.) ? this->inside_corner(contour, ipt, this->point) :
                                              (t == 1.) ? this->inside_corner(contour, contour.segment_idx_next(ipt), this->point) :
                                                          this->left_of_segment(contour, ipt, this->point);
                                accept      = inside && dist_along_contour > 0.6 * M_PI * dist;
                            }
                        }
                        if (accept && (!this->found || dist < this->distance)) {
                            // Simple case: Just measure the shortest distance.
                            this->distance = dist;
                            this->found    = true;
                        }
                    }
                }
                // Continue traversing the grid.
                return true;
            }

            const EdgeGrid::Grid 			   &grid;
            const size_t 		  				idx_contour;
            const EdgeGrid::Contour            &contour;

            const std::vector<float>           &boundary_parameters;
            const double                        dist_same_contour_accept;
            const double 						dist_same_contour_reject;

            size_t 								idx_point;
            Point			      				point;
            // Direction inside the contour from idx_point, not normalized.
            Vec2d								dir_inside;
            bool 								found;
            double 								distance;

        private:
            static Vec2d dir_inside_at_point(const Points &contour, size_t i)
            {
                size_t iprev = prev_idx_modulo(i, contour);
                size_t inext = next_idx_modulo(i, contour);
                Vec2d  v1    = (contour[i] - contour[iprev]).cast<double>();
                Vec2d  v2    = (contour[inext] - contour[i]).cast<double>();
                return Vec2d(-v1.y() - v2.y(), v1.x() + v2.x());
            }

            static bool inside_corner(const EdgeGrid::Contour &contour, size_t i, const Point &pt_oposite)
            {
                const Vec2d pt         = pt_oposite.cast<double>();
                const Point &pt_prev   = contour.segment_prev(i);
                const Point &pt_this   = contour.segment_start(i);
                const Point &pt_next   = contour.segment_end(i);
                Vec2d       v1         = (pt_this - pt_prev).cast<double>();
                Vec2d       v2         = (pt_next - pt_this).cast<double>();
                bool        left_of_v1 = cross2(v1, pt - pt_prev.cast<double>()) > 0.;
                bool        left_of_v2 = cross2(v2, pt - pt_this.cast<double>()) > 0.;
                return cross2(v1, v2) > 0 ? left_of_v1 && left_of_v2 : // convex corner
                                            left_of_v1 || left_of_v2;                   // concave corner
            }

            static bool left_of_segment(const EdgeGrid::Contour &contour, size_t i, const Point &pt_oposite)
            {
                const Vec2d  pt      = pt_oposite.cast<double>();
                const Point &pt_this = contour.segment_start(i);
                const Point &pt_next = contour.segment_end(i);
                Vec2d        v       = (pt_next - pt_this).cast<double>();
                return cross2(v, pt - pt_this.cast<double>()) > 0.;
            }
        } visitor(grid, contour_idx, poly_distances, 0.5 * compensation * M_PI, search_radius);

        out.reserve(polygon.size());
        Point radius_vector(search_radius, search_radius);
        for (const Point &pt : polygon.points) {
            visitor.init(polygon.points, pt);
            grid.visit_cells_intersecting_box(BoundingBox(pt - radius_vector, pt + radius_vector), visitor);
            out.emplace_back(float(visitor.found ? std::min(visitor.distance, search_radius) : search_radius));
        }
    }

    return out;
}

// Polygon offset which ensures that if a polygon breaks up into several separate parts, the original polygon will be used in these places.
// ExPolygons are handled one by one so returned ExPolygons could intersect.
static ExPolygons inner_offset(const ExPolygons &ex_polygons, double offset_dis)
{
    // try different offset_dis distances
    const std::vector<double> min_contour_width_values = {offset_dis / 2., offset_dis, 2. * offset_dis + SCALED_EPSILON};
    ExPolygons                ex_poly_result           = ex_polygons;
    // remove too small holes from the ex_poly
    for (ExPolygon &ex_poly : ex_poly_result) {
        for (auto iter = ex_poly.holes.begin(); iter != ex_poly.holes.end();) {
            auto out_offset_holes = offset(*iter, scale_(0.1f));
            if (out_offset_holes.empty()) {
                iter = ex_poly.holes.erase(iter);
            } else {
                ++iter;
            }
        }
    }
    resample_expolygons(ex_poly_result, offset_dis / 2, scaled<double>(0.5));

    for (ExPolygon &ex_poly : ex_poly_result) {
        BoundingBox bbox(get_extents(ex_poly));
        bbox.offset(SCALED_EPSILON);

        // Filter out expolygons smaller than 0.1mm^2
        if (Vec2d bbox_size = bbox.size().cast<double>(); bbox_size.x() * bbox_size.y() < Slic3r::sqr(scale_(0.1f))) continue;

        for (const double &min_contour_width : min_contour_width_values) {
            const size_t min_contour_width_idx = &min_contour_width - &min_contour_width_values.front();
            const double search_radius         = 2. * (offset_dis + min_contour_width);

            EdgeGrid::Grid grid;
            grid.set_bbox(bbox);
            grid.create(ex_poly, coord_t(0.7 * search_radius));

            std::vector<std::vector<float>> ex_poly_distances;
            precompute_expolygon_distances(ex_poly, ex_poly_distances);

            std::vector<std::vector<float>> offsets;
            offsets.reserve(ex_poly.holes.size() + 1);
            for (size_t idx_contour = 0; idx_contour <= ex_poly.holes.size(); ++idx_contour) {
                const Polygon &poly = (idx_contour == 0) ? ex_poly.contour : ex_poly.holes[idx_contour - 1];
                assert(poly.is_counter_clockwise() == (idx_contour == 0));
                std::vector<float> distances = contour_distance(grid, ex_poly_distances[idx_contour], idx_contour, poly, offset_dis, search_radius);
                for (float &distance : distances) {
                    if (distance < min_contour_width)
                        distance = 0.0f;
                    else if (distance > min_contour_width + 2. * offset_dis)
                        distance = -float(offset_dis);
                    else
                        distance = -(distance - float(min_contour_width)) / 2.f;
                }
                offsets.emplace_back(distances);
            }

            ExPolygons offset_ex_poly = variable_offset_inner_ex(ex_poly, offsets);
            // If variable_offset_inner_ex produces empty result, then original ex_polygon is used
            if (offset_ex_poly.size() == 1 && offset_ex_poly.front().holes.size() == ex_poly.holes.size()) {
                ex_poly = std::move(offset_ex_poly.front());
                break;
            } else if ((min_contour_width_idx + 1) < min_contour_width_values.size()) {
                continue; // Try the next round with bigger min_contour_width.
            } else if (offset_ex_poly.size() == 1) {
                ex_poly = std::move(offset_ex_poly.front());
                break;
            } else if (offset_ex_poly.size() > 1) {
                // fix_after_inner_offset called inside variable_offset_inner_ex sometimes produces
                // tiny artefacts polygons, so these artefacts are removed.
                double max_area     = offset_ex_poly.front().area();
                size_t max_area_idx = 0;
                for (size_t poly_idx = 1; poly_idx < offset_ex_poly.size(); ++poly_idx) {
                    double area = offset_ex_poly[poly_idx].area();
                    if (max_area < area) {
                        max_area     = area;
                        max_area_idx = poly_idx;
                    }
                }
                ex_poly = std::move(offset_ex_poly[max_area_idx]);
                break;
            }
        }
    }
    return ex_poly_result;
}

//#define INCLUDE_SUPPORTS_IN_BOUNDARY

// called by AvoidCrossingPerimeters::travel_to()
static ExPolygons get_boundary(const Layer &layer, float perimeter_spacing)
{
    // const float perimeter_spacing = get_perimeter_spacing(layer);
    const float perimeter_offset  = perimeter_spacing / 2.f;
    auto const *support_layer     = dynamic_cast<const SupportLayer *>(&layer);
    ExPolygons  boundary          = union_ex(inner_offset(layer.lslices, 1.5 * perimeter_spacing));
    if(support_layer) {
#ifdef INCLUDE_SUPPORTS_IN_BOUNDARY
        append(boundary, inner_offset(support_layer->support_islands, 1.5 * perimeter_spacing));
#endif
        auto *layer_below = layer.object()->get_first_layer_bellow_printz(layer.print_z, EPSILON);
        if (layer_below)
            append(boundary, inner_offset(layer_below->lslices, 1.5 * perimeter_spacing));
        // After calling inner_offset it is necessary to call union_ex because of the possibility of intersection ExPolygons
        boundary = union_ex(boundary);
    }
    // Collect all top layers that will not be crossed.
    size_t      polygons_count    = 0;
    for (const LayerRegion *layer_region : layer.regions())
        for (const Surface &surface : layer_region->fill_surfaces.surfaces)
            if (surface.is_top()) ++polygons_count;

    if (polygons_count > 0) {
        ExPolygons top_layer_polygons;
        top_layer_polygons.reserve(polygons_count);
        for (const LayerRegion *layer_region : layer.regions())
            for (const Surface &surface : layer_region->fill_surfaces.surfaces)
                if (surface.is_top()) top_layer_polygons.emplace_back(surface.expolygon);

        top_layer_polygons = union_ex(top_layer_polygons);
        return diff_ex(boundary, offset_ex(top_layer_polygons, -1.2 * perimeter_offset));
    }

    return boundary;
}

struct ClosedWallLoop {
    Polygon poly;
    int     inset_idx = -1;
    bool    hole      = false;
};

static void collect_closed_wall_loops(const ExtrusionEntity *entity, std::vector<ClosedWallLoop> &out, bool &saw_open)
{
    if (entity == nullptr)
        return;
    if (entity->is_collection()) {
        const auto *collection = static_cast<const ExtrusionEntityCollection *>(entity);
        for (const ExtrusionEntity *child : collection->entities)
            collect_closed_wall_loops(child, out, saw_open);
        return;
    }

    auto accept_closed = [&](Polygon poly, bool hole_role) {
        if (poly.size() < 3)
            return;
        ClosedWallLoop loop;
        loop.poly      = std::move(poly);
        loop.inset_idx = entity->inset_idx;
        loop.hole      = hole_role || loop.poly.is_clockwise();
        if (loop.hole) {
            if (loop.poly.is_counter_clockwise())
                loop.poly.reverse();
        } else if (!loop.poly.is_counter_clockwise()) {
            loop.poly.reverse();
        }
        out.emplace_back(std::move(loop));
    };

    if (entity->is_loop()) {
        const auto *loop = static_cast<const ExtrusionLoop *>(entity);
        accept_closed(loop->polygon(), (loop->loop_role() & elrHole) != 0);
        return;
    }

    Polyline pl = entity->as_polyline();
    if (pl.size() >= 4 && pl.first_point().distance_to(pl.last_point()) <= scale_(0.05)) {
        if (pl.first_point() != pl.last_point())
            pl.points.back() = pl.points.front();
        pl.points.pop_back();
        accept_closed(Polygon(std::move(pl.points)), false);
    } else if (pl.size() >= 2) {
        saw_open = true;
    }
}

// Geometric innermost wall: leaf contours, and hole loops that no other hole contains
// (the hole wall next to the infill). inset_idx is consulted only as a tie break when
// several loops share the same nesting and a non-negative inset is present.
static ExPolygons innermost_wall_expolygons(const std::vector<ClosedWallLoop> &loops)
{
    std::vector<size_t> contours;
    std::vector<size_t> holes;
    contours.reserve(loops.size());
    holes.reserve(loops.size());
    for (size_t i = 0; i < loops.size(); ++i)
        (loops[i].hole ? holes : contours).push_back(i);

    auto contains_poly = [](const Polygon &outer, const Polygon &inner) {
        if (inner.size() < 3)
            return false;
        return outer.contains(inner.points.front()) && outer.contains(inner.points[inner.size() / 2]);
    };

    std::vector<char> keep_contour(contours.size(), 1);
    for (size_t i = 0; i < contours.size(); ++i)
        for (size_t j = 0; j < contours.size(); ++j)
            if (i != j && contains_poly(loops[contours[i]].poly, loops[contours[j]].poly))
                keep_contour[i] = 0;

    std::vector<char> keep_hole(holes.size(), 1);
    for (size_t i = 0; i < holes.size(); ++i)
        for (size_t j = 0; j < holes.size(); ++j)
            if (i != j && contains_poly(loops[holes[j]].poly, loops[holes[i]].poly))
                keep_hole[i] = 0;

    // Leaf contours are the innermost wall of each island (highest inset for concentric
    // walls). Hole loops that contain another hole are the wall next to the infill.
    ExPolygons out;
    for (size_t i = 0; i < contours.size(); ++i) {
        if (!keep_contour[i])
            continue;
        const ClosedWallLoop &loop = loops[contours[i]];
        ExPolygon ex;
        ex.contour = loop.poly;
        for (size_t h = 0; h < holes.size(); ++h) {
            if (!keep_hole[h])
                continue;
            const ClosedWallLoop &hole = loops[holes[h]];
            if (ex.contour.contains(hole.poly.points.front()))
                ex.holes.emplace_back(hole.poly);
        }
        out.emplace_back(std::move(ex));
    }
    return out.empty() ? out : union_ex(out);
}

// Innermost perimeter loops, shrunk by half an inner-wall width. Open Arachne paths and
// missing perimeters fall back to the slice outline shrunk by 1.5x the outer wall width.
// Top surfaces are cut out the same way as get_boundary().
static ExPolygons get_comb_boundary(const Layer &layer)
{
    std::vector<ClosedWallLoop> loops;
    bool saw_open = false;
    for (const LayerRegion *layer_region : layer.regions())
        if (layer_region != nullptr)
            collect_closed_wall_loops(&layer_region->perimeters, loops, saw_open);

    const ExPolygons innermost = innermost_wall_expolygons(loops);
    const float      inner_w   = get_internal_perimeter_width(layer);
    const float      ext_w     = get_external_perimeter_width(layer);
    const float      spacing   = get_perimeter_spacing(layer);

    ExPolygons tops;
    for (const LayerRegion *layer_region : layer.regions())
        if (layer_region != nullptr)
            for (const Surface &surface : layer_region->fill_surfaces.surfaces)
                if (surface.is_top())
                    tops.emplace_back(surface.expolygon);
    if (!tops.empty())
        tops = union_ex(tops);

    const bool use_outline = innermost.empty() || saw_open;
    return AvoidCrossingPerimeters::build_comb_region(
        innermost,
        0.5 * double(inner_w),
        use_outline ? layer.lslices : ExPolygons{},
        1.5 * double(ext_w),
        saw_open && !innermost.empty(),
        tops,
        1.2 * double(spacing) / 2.);
}

// called by AvoidCrossingPerimeters::travel_to()
static Polygons get_boundary_external(const Layer &layer)
{
    const float perimeter_spacing = get_perimeter_spacing_external(layer);
    const float perimeter_offset  = perimeter_spacing / 2.f;
    auto const *support_layer     = dynamic_cast<const SupportLayer *>(&layer);
    Polygons    boundary;
#ifdef INCLUDE_SUPPORTS_IN_BOUNDARY
    ExPolygons  supports_boundary;
#endif
    // Collect all holes for all printed objects and their instances, which will be printed at the same time as passed "layer".
    for (const PrintObject *object : layer.object()->print()->objects()) {
        Polygons   holes_per_obj;
#ifdef INCLUDE_SUPPORTS_IN_BOUNDARY
        ExPolygons supports_per_obj;
#endif
        if (const Layer *l = object->get_layer_at_printz(layer.print_z, EPSILON); l)
            for (const ExPolygon &island : l->lslices)
                append(holes_per_obj, island.holes);
        if (support_layer) {
            auto *layer_below = object->get_first_layer_bellow_printz(layer.print_z, EPSILON);
            if (layer_below)
                for (const ExPolygon &island : layer_below->lslices)
                    append(holes_per_obj, island.holes);
#ifdef INCLUDE_SUPPORTS_IN_BOUNDARY
            append(supports_per_obj, support_layer->support_islands);
#endif
        }

        // After 7ff76d07684858fd937ef2f5d863f105a10f798e, when expand is called on CW polygons (holes), they are shrunk
        // instead of expanded because union that makes CCW from CW isn't called anymore. So let's make it CCW.
        polygons_reverse(holes_per_obj);

        for (const PrintInstance &instance : object->instances()) {
            size_t boundary_idx = boundary.size();
            append(boundary, holes_per_obj);
            for (; boundary_idx < boundary.size(); ++boundary_idx)
                boundary[boundary_idx].translate(instance.shift);
#ifdef INCLUDE_SUPPORTS_IN_BOUNDARY
            size_t support_idx = supports_boundary.size();
            append(supports_boundary, supports_per_obj);
            for (; support_idx < supports_boundary.size(); ++support_idx)
                supports_boundary[support_idx].translate(instance.shift);
#endif
        }
    }

    // Used offset_ex for cases when another object will be in the hole of another polygon
    boundary = expand(boundary, perimeter_offset);
    // Reverse all polygons for making normals point from the polygon out.
    for (Polygon &poly : boundary)
        poly.reverse();
#ifdef INCLUDE_SUPPORTS_IN_BOUNDARY
    append(boundary, to_polygons(inner_offset(supports_boundary, perimeter_offset)));
#endif
    return boundary;
}

static void init_boundary_distances(AvoidCrossingPerimeters::Boundary *boundary)
{
    boundary->boundaries_params.assign(boundary->boundaries.size(), std::vector<float>());
    for (size_t poly_idx = 0; poly_idx < boundary->boundaries.size(); ++poly_idx)
        precompute_polygon_distances(boundary->boundaries[poly_idx], boundary->boundaries_params[poly_idx]);
}

void init_boundary(AvoidCrossingPerimeters::Boundary *boundary, Polygons &&boundary_polygons)
{
    boundary->clear();
    boundary->boundaries = std::move(boundary_polygons);

    BoundingBox bbox(get_extents(boundary->boundaries));
    bbox.offset(SCALED_EPSILON);
    boundary->bbox = BoundingBoxf(bbox.min.cast<double>(), bbox.max.cast<double>());
    boundary->grid.set_bbox(bbox);
    // FIXME 1mm grid?
    boundary->grid.create(boundary->boundaries, coord_t(scale_(1.)));
    init_boundary_distances(boundary);
}

static void init_boundary(AvoidCrossingPerimeters::Boundary *boundary, Polygons &&boundary_polygons, const std::vector<Point>& merge_poins)
{
    boundary->clear();
    boundary->boundaries = std::move(boundary_polygons);

    BoundingBox bbox(get_extents(boundary->boundaries));
    for (const auto& merge_point : merge_poins) {
        bbox.merge(merge_point);
    }
    bbox.offset(bbox.radius());
    boundary->bbox = BoundingBoxf(bbox.min.cast<double>(), bbox.max.cast<double>());
    boundary->grid.set_bbox(bbox);
    // FIXME 1mm grid?
    boundary->grid.create(boundary->boundaries, coord_t(scale_(1.)));
    init_boundary_distances(boundary);
}

static constexpr double COMB_DETOUR_CAP_MM = 20.0;
// Outer-wall points sit outside a shrunk comb. 6 mm still will not bridge a real gap between islands.
static constexpr double COMB_SNAP_MM       = 6.0;

static void drop_degenerate_expolygons(ExPolygons &src)
{
    ExPolygons kept;
    kept.reserve(src.size());
    for (ExPolygon &ex : src) {
        if (ex.contour.size() < 3)
            continue;
        Polygons holes;
        holes.reserve(ex.holes.size());
        for (Polygon &hole : ex.holes)
            if (hole.size() >= 3)
                holes.emplace_back(std::move(hole));
        ex.holes = std::move(holes);
        kept.emplace_back(std::move(ex));
    }
    src.swap(kept);
}

// Deepest inset of one island that does not collapse and does not split it.
// A full wall shrink on a thin insole disappears. Jumping from that straight to a 0.5 mm
// inset leaves the travel on the outer-band boundary, so search for the deepest inset that
// still holds together. That channel is the wall centerline. Shrink 0 is the unshrunk loop.
static ExPolygons offset_progressive_one(const ExPolygons &src, double max_shrink)
{
    if (src.empty())
        return {};

    const ExPolygons center = union_ex(src);
    if (center.empty())
        return {};
    if (max_shrink <= 0.)
        return center;

    auto at = [&](double shrink) -> ExPolygons {
        if (shrink <= double(SCALED_EPSILON))
            return center;
        return union_ex(offset_ex(src, -float(shrink)));
    };
    // A vanishing channel cannot be walked (the boundary offset falls outside it) and
    // sits on the 0.5 mm band. Keep at least ~0.1 mm of width, which is the centerline.
    // Island count must stay put: dropping a thin heel while a thicker island survives
    // would send that heel's travels across the wall.
    auto holds = [&](const ExPolygons &trial) {
        if (trial.size() != center.size())
            return false;
        const ExPolygons core = union_ex(offset_ex(trial, -float(scale_(0.05))));
        return core.size() == center.size();
    };

    ExPolygons best = center;
    double     lo   = 0.;
    double     hi   = max_shrink;
    for (int i = 0; i < 16; ++i) {
        const double mid   = 0.5 * (lo + hi);
        ExPolygons   trial = at(mid);
        if (holds(trial)) {
            best = std::move(trial);
            lo   = mid;
        } else {
            hi = mid;
        }
    }
    return best;
}

// Each island is shrunk on its own so a thin heel keeps its centerline while a thicker
// region can inset further.
static ExPolygons offset_progressive(const ExPolygons &src, double max_shrink)
{
    if (src.empty())
        return {};
    const ExPolygons center = union_ex(src);
    if (center.size() <= 1)
        return offset_progressive_one(center, max_shrink);
    ExPolygons out;
    for (const ExPolygon &island : center)
        append(out, offset_progressive_one(ExPolygons{island}, max_shrink));
    return union_ex(out);
}

static void emit_comb_log_line(const std::string &line)
{
    std::fwrite(line.data(), 1, line.size(), stderr);
    std::fwrite("\n", 1, 1, stderr);
    std::fflush(stderr);
    const char *path = std::getenv("ORCA_COMB_LOG");
    if (path == nullptr || path[0] == '\0')
        return;
    static std::mutex file_mutex;
    std::lock_guard<std::mutex> lock(file_mutex);
    std::ofstream out(path, std::ios::app);
    if (out)
        out << line << '\n';
}

ExPolygons AvoidCrossingPerimeters::build_comb_region(const ExPolygons &innermost_loops,
                                                      double            inner_shrink_scaled,
                                                      const ExPolygons &open_fallback_lslices,
                                                      double            outline_shrink_scaled,
                                                      bool              fill_open_islands,
                                                      const ExPolygons &top_surfaces,
                                                      double            top_cut_shrink_scaled)
{
    ExPolygons comb;
    if (!innermost_loops.empty())
        comb = offset_progressive(innermost_loops, inner_shrink_scaled);

    if (comb.empty()) {
        if (!open_fallback_lslices.empty())
            comb = offset_progressive(open_fallback_lslices, outline_shrink_scaled);
    } else if (fill_open_islands && !open_fallback_lslices.empty() && outline_shrink_scaled > 0.) {
        // Closed loops cover their own islands. Open Arachne paths elsewhere use the outline fallback.
        const ExPolygons near = offset_ex(innermost_loops, float(scale_(1.)));
        const ExPolygons far  = diff_ex(open_fallback_lslices, near);
        if (!far.empty()) {
            append(comb, offset_progressive(far, outline_shrink_scaled));
            comb = union_ex(comb);
        }
    }

    if (!comb.empty() && !top_surfaces.empty()) {
        const ExPolygons tops = union_ex(top_surfaces);
        const ExPolygons cut  = top_cut_shrink_scaled > 0. ? offset_ex(tops, -float(top_cut_shrink_scaled)) : tops;
        const ExPolygons punched = diff_ex(comb, cut);
        // A top cut that erases a thin insole would force a straight move through the wall.
        if (!punched.empty())
            comb = punched;
    }
    drop_degenerate_expolygons(comb);
    return comb;
}

void AvoidCrossingPerimeters::emit_comb_log(const CombLogEntry &entry)
{
    m_comb_log.push_back(entry);
    std::ostringstream ss;
    ss << std::fixed << std::setprecision(3);
    if (entry.lifted) {
        ss << "COMB_LIFT reason=" << entry.reason
           << " layer=" << entry.layer_id
           << " from=(" << entry.from_x << "," << entry.from_y << ")"
           << " to=(" << entry.to_x << "," << entry.to_y << ")";
        if (entry.detour_mm > 0.)
            ss << " detour_mm=" << entry.detour_mm;
    } else if (entry.routed && entry.reason == "cap") {
        ss << "COMB_CAP reason=cap layer=" << entry.layer_id
           << " from=(" << entry.from_x << "," << entry.from_y << ")"
           << " to=(" << entry.to_x << "," << entry.to_y << ")"
           << " detour_mm=" << entry.detour_mm;
    } else {
        ss << "COMB_UNROUTED reason=" << entry.reason
           << " layer=" << entry.layer_id
           << " from=(" << entry.from_x << "," << entry.from_y << ")"
           << " to=(" << entry.to_x << "," << entry.to_y << ")";
    }
    emit_comb_log_line(ss.str());
}

AvoidCrossingPerimeters::~AvoidCrossingPerimeters()
{
    if (m_comb_summary_emitted || m_comb_log.empty())
        return;
    m_comb_summary_emitted = true;
    size_t unrouted = 0;
    size_t caps     = 0;
    size_t lifts    = 0;
    for (const CombLogEntry &entry : m_comb_log) {
        if (entry.lifted)
            ++lifts;
        else if (entry.reason == "cap" && entry.routed)
            ++caps;
        else
            ++unrouted;
    }
    std::ostringstream ss;
    ss << "COMB_UNROUTED_SUMMARY unrouted=" << unrouted << " cap_exceeded=" << caps << " lift=" << lifts;
    emit_comb_log_line(ss.str());
}

void AvoidCrossingPerimeters::ensure_comb(const Layer &layer)
{
    if (m_comb_init)
        return;
    m_comb_init = true;
    m_comb = get_comb_boundary(layer);
    drop_degenerate_expolygons(m_comb);
    // Outer wall minus the 0.5 mm band. A routed travel that misses this region is lifted.
    m_safe = union_ex(offset_ex(layer.lslices, -float(scale_(0.5))));
    drop_degenerate_expolygons(m_safe);
    m_comb_bboxes.clear();
    m_comb_bboxes.reserve(m_comb.size());
    for (const ExPolygon &ex : m_comb)
        m_comb_bboxes.emplace_back(get_extents(ex));
    m_comb_boundary.clear();
    if (m_comb.empty())
        return;
    init_boundary(&m_comb_boundary, to_polygons(m_comb));
    BoundingBox bbox(get_extents(m_comb));
    bbox.offset(scale_(1.));
    m_grid_comb.set_bbox(bbox);
    m_grid_comb.create(m_comb, coord_t(scale_(1.)));
}

static void dedup_polyline(Polyline &pl)
{
    if (pl.size() < 2)
        return;
    Points kept;
    kept.reserve(pl.size());
    kept.push_back(pl.points.front());
    for (size_t i = 1; i < pl.size(); ++i)
        if (pl.points[i] != kept.back())
            kept.push_back(pl.points[i]);
    if (kept.size() >= 2)
        pl.points.swap(kept);
}

static bool internal_polyline_inside(const ExPolygon &ex, const Polyline &pl)
{
    if (pl.size() < 2)
        return false;
    size_t first = 0;
    size_t last  = pl.size() - 1;
    if (!ex.contains(pl.points.front()) && pl.size() >= 3)
        first = 1;
    if (!ex.contains(pl.points.back()) && last > first)
        --last;
    if (last <= first)
        return ex.contains(pl.points[first]);
    Polyline sub;
    sub.points.reserve(last - first + 1);
    for (size_t i = first; i <= last; ++i)
        sub.points.push_back(pl.points[i]);
    if (ex.contains(sub))
        return true;
    const Polylines outside = diff_pl(sub, ex);
    double out_len = 0.;
    for (const Polyline &piece : outside)
        out_len += piece.length();
    return out_len < scale_(0.05);
}

struct RingProjection {
    size_t ring  = 0;
    size_t seg   = 0;
    Point  point;
    double dist2 = std::numeric_limits<double>::max();
    bool   ok    = false;
};

static RingProjection project_onto_boundary(const AvoidCrossingPerimeters::Boundary &boundary, const Point &pt)
{
    RingProjection best;
    for (size_t ri = 0; ri < boundary.boundaries.size(); ++ri) {
        const Polygon &poly = boundary.boundaries[ri];
        if (poly.size() < 2)
            continue;
        for (size_t si = 0; si < poly.size(); ++si) {
            Point closest;
            const double d2 = Line(poly.points[si], poly.points[(si + 1) % poly.size()]).distance_to_squared(pt, &closest);
            if (d2 < best.dist2) {
                best.ring  = ri;
                best.seg   = si;
                best.point = closest;
                best.dist2 = d2;
                best.ok    = true;
            }
        }
    }
    return best;
}

static int locate_comb_island(const ExPolygons &comb, const std::vector<BoundingBox> &bboxes, const Point &pt, Point &snapped, bool &inside)
{
    inside  = false;
    snapped = pt;
    for (size_t i = 0; i < comb.size(); ++i) {
        if ((bboxes.empty() || bboxes[i].contains(pt)) && comb[i].contains(pt)) {
            inside = true;
            return int(i);
        }
    }
    const double snap_limit2 = double(scale_(COMB_SNAP_MM)) * double(scale_(COMB_SNAP_MM));
    int    best_i = -1;
    double best_d = snap_limit2;
    Point  best_pt;
    for (size_t i = 0; i < comb.size(); ++i) {
        const ExPolygon &ex = comb[i];
        // projection_onto() reads first_point(). An empty ring (seen after a failed inset) segfaults.
        if (ex.contour.size() < 2)
            continue;
        Point p = pt.projection_onto(ex.contour);
        double d = (p - pt).cast<double>().squaredNorm();
        for (const Polygon &hole : ex.holes) {
            if (hole.size() < 2)
                continue;
            const Point ph = pt.projection_onto(hole);
            const double dh = (ph - pt).cast<double>().squaredNorm();
            if (dh < d) {
                d = dh;
                p = ph;
            }
        }
        if (d <= best_d) {
            best_d  = d;
            best_i  = int(i);
            best_pt = p;
        }
    }
    if (best_i < 0)
        return -1;
    snapped = best_pt;
    return best_i;
}

static Point offset_into_material(const Polygon &poly, size_t left_idx, size_t right_idx, const Point &middle);

// Walk already-found boundary hits, taking the shorter direction on each ring, then simplify.
// Contours are CCW and holes are CW, so the existing left-normal offset moves into the material.
static Polyline walk_boundary_hits(const AvoidCrossingPerimeters::Boundary &boundary,
                                   std::vector<Intersection>                intersections,
                                   const Point                             &start,
                                   const Point                             &end)
{
    Polyline empty;
    if (intersections.size() < 2 || boundary.boundaries.empty())
        return empty;

    const Vec2d dir = (end - start).cast<double>();
    std::sort(intersections.begin(), intersections.end(), [dir](const Intersection &l, const Intersection &r) {
        return (r.point - l.point).template cast<double>().dot(dir) > 0.;
    });
    {
        std::vector<Intersection> unique;
        unique.reserve(intersections.size());
        for (const Intersection &hit : intersections) {
            if (!unique.empty() && unique.back().border_idx == hit.border_idx && unique.back().point.distance_to(hit.point) <= scale_(0.01))
                continue;
            unique.push_back(hit);
        }
        intersections.swap(unique);
    }
    if (intersections.size() < 2)
        return empty;

    const Polygons &boundaries = boundary.boundaries;
    std::vector<TravelPoint> result;
    result.push_back({start, -1});
    size_t paired = 0;
    for (auto it_first = intersections.begin(); it_first != intersections.end(); ++it_first) {
        const Intersection &intersection_first = *it_first;
        auto it_last_item = std::make_reverse_iterator(it_first) - 1;
        auto it_second_r  = std::find_if(intersections.rbegin(), it_last_item, [&intersection_first](const Intersection &intersection) {
            return intersection_first.border_idx == intersection.border_idx;
        });
        if (it_second_r == it_last_item)
            continue;
        // base() == begin() means the reverse iterator is rend(); base()-1 would be before begin().
        if (it_second_r.base() == intersections.begin())
            continue;
        if (intersection_first.border_idx >= boundaries.size())
            continue;

        const Polygon &poly = boundaries[intersection_first.border_idx];
        if (poly.size() < 3)
            continue;
        if (intersection_first.line_idx >= poly.size())
            continue;

        auto it_second = it_second_r.base() - 1;
        if (it_second < intersections.begin() || it_second >= intersections.end())
            continue;
        const Intersection &intersection_second = *it_second;
        if (intersection_second.line_idx >= poly.size() || intersection_second.border_idx != intersection_first.border_idx)
            continue;

        size_t left_idx  = intersection_first.line_idx;
        size_t right_idx = intersection_first.line_idx + 1 == poly.points.size() ? 0 : intersection_first.line_idx + 1;
        result.push_back({offset_into_material(poly, left_idx, right_idx, intersection_first.point),
                          int(intersection_first.border_idx), intersection_first.do_not_remove});

        float contour_length = 0.f;
        if (intersection_first.border_idx < boundary.boundaries_params.size() &&
            !boundary.boundaries_params[intersection_first.border_idx].empty())
            contour_length = boundary.boundaries_params[intersection_first.border_idx].back();
        const Direction shortest = get_shortest_direction(boundary, intersection_first, intersection_second, contour_length);
        // A line_idx past the polygon never equals the cursor, so the walk would not terminate.
        const int step_limit = int(poly.size()) + 1;
        int       steps      = 0;
        bool      finished   = false;
        if (shortest == Direction::Forward) {
            for (int line_idx = int(intersection_first.line_idx); line_idx != int(intersection_second.line_idx);) {
                if (++steps > step_limit)
                    break;
                const int next = line_idx + 1 < int(poly.size()) ? line_idx + 1 : 0;
                const size_t vertex = (line_idx + 1 == int(poly.points.size())) ? 0 : size_t(line_idx + 1);
                result.push_back({offset_into_material(poly, (vertex + poly.size() - 1) % poly.size(), (vertex + 1) % poly.size(), poly.points[vertex]),
                                  int(intersection_first.border_idx)});
                line_idx = next;
                if (line_idx == int(intersection_second.line_idx))
                    finished = true;
            }
        } else {
            for (int line_idx = int(intersection_first.line_idx); line_idx != int(intersection_second.line_idx);) {
                if (++steps > step_limit)
                    break;
                const int next = line_idx - 1 >= 0 ? line_idx - 1 : int(poly.size()) - 1;
                const size_t vertex = size_t(line_idx);
                result.push_back({offset_into_material(poly, (vertex + poly.size() - 1) % poly.size(), (vertex + 1) % poly.size(), poly.points[vertex]),
                                  int(intersection_first.border_idx)});
                line_idx = next;
                if (line_idx == int(intersection_second.line_idx))
                    finished = true;
            }
        }
        if (!finished && steps > step_limit)
            return empty;
        left_idx  = intersection_second.line_idx;
        right_idx = (intersection_second.line_idx >= poly.points.size() - 1) ? 0 : (intersection_second.line_idx + 1);
        result.push_back({offset_into_material(poly, left_idx, right_idx, intersection_second.point),
                          int(intersection_second.border_idx), intersection_second.do_not_remove});
        it_first = it_second;
        ++paired;
    }
    result.push_back({end, -1});
    if (paired == 0)
        return empty;

    std::vector<TravelPoint> simplified = simplify_travel(boundary, result);
    Polyline out = to_polyline(simplified.empty() ? result : simplified);
    dedup_polyline(out);
    if (!out.empty()) {
        out.points.front() = start;
        out.points.back()  = end;
    }
    return out;
}

static std::vector<Intersection> intersections_with_boundary(const AvoidCrossingPerimeters::Boundary &boundary,
                                                            const Point                             &start,
                                                            const Point                             &end)
{
    std::vector<Intersection> intersections;
    const Line travel(start, end);
    for (size_t ri = 0; ri < boundary.boundaries.size(); ++ri) {
        const Polygon &poly = boundary.boundaries[ri];
        if (poly.size() < 2 || ri >= boundary.boundaries_params.size())
            continue;
        for (size_t li = 0; li < poly.size(); ++li) {
            Point ip;
            const Line edge(poly.points[li], poly.points[(li + 1) % poly.size()]);
            if (!travel.intersection(edge, &ip))
                continue;
            if (li >= boundary.boundaries_params[ri].size())
                continue;
            Intersection hit;
            hit.border_idx    = ri;
            hit.line_idx      = li;
            hit.point         = ip;
            hit.distance      = boundary.boundaries_params[ri][li] + float((ip - poly.points[li]).cast<double>().norm());
            hit.do_not_remove = false;
            intersections.push_back(hit);
        }
    }
    return intersections;
}

static Intersection intersection_from_projection(const AvoidCrossingPerimeters::Boundary &boundary, const RingProjection &proj)
{
    Intersection hit;
    const Polygon &poly = boundary.boundaries[proj.ring];
    hit.border_idx    = proj.ring;
    hit.line_idx      = proj.seg;
    hit.point         = proj.point;
    const float base  = (proj.ring < boundary.boundaries_params.size() && proj.seg < boundary.boundaries_params[proj.ring].size())
                            ? boundary.boundaries_params[proj.ring][proj.seg]
                            : 0.f;
    hit.distance      = base + float((proj.point - poly.points[proj.seg]).cast<double>().norm());
    hit.do_not_remove = true;
    return hit;
}

// Inward offset used by the comb walk. A collapsed vertex has a zero normal; casting that
// NaN to a coordinate is undefined and segfaults. Leave the vertex where it is.
static Point offset_into_material(const Polygon &poly, size_t left_idx, size_t right_idx, const Point &middle)
{
    if (poly.size() < 3 || left_idx >= poly.size() || right_idx >= poly.size())
        return middle;
    const Point &left  = find_first_different_vertex<false>(poly, left_idx, middle);
    const Point &right = find_first_different_vertex<true>(poly, right_idx, middle);
    if (left == middle || middle == right)
        return middle;
    const Vec2d n = three_points_inward_normal(left, middle, right);
    if (!std::isfinite(n.x()) || !std::isfinite(n.y()) || n.squaredNorm() <= 1e-12)
        return middle;
    return middle + (n * double(SCALED_EPSILON)).cast<coord_t>();
}

static AvoidCrossingPerimeters::Boundary boundary_from_expolygon(const ExPolygon &ex)
{
    Polygons rings;
    rings.reserve(ex.holes.size() + 1);
    if (ex.contour.size() >= 3) {
        Polygon contour = ex.contour;
        if (!contour.is_counter_clockwise())
            contour.reverse();
        rings.push_back(std::move(contour));
    }
    for (Polygon hole : ex.holes) {
        // An empty hole makes precompute_polygon_distances index off the front. Skip it.
        if (hole.size() < 3)
            continue;
        // CW holes: the left normal used by get_polygon_vertex_offset points into the material.
        if (hole.is_counter_clockwise())
            hole.reverse();
        rings.push_back(std::move(hole));
    }
    AvoidCrossingPerimeters::Boundary boundary;
    if (!rings.empty())
        init_boundary(&boundary, std::move(rings));
    return boundary;
}

static size_t distinct_rings(const std::vector<Intersection> &hits)
{
    std::unordered_set<size_t> rings;
    for (const Intersection &hit : hits)
        rings.insert(hit.border_idx);
    return rings.size();
}

// Route start→end so the travel stays in `ex`. Endpoints in the wall band are joined by the
// shortest connector; the rest of the path follows the ring, shorter direction first.
static Polyline route_inside_expolygon(const ExPolygon &ex, const Point &start, const Point &end, const Layer *layer)
{
    if (ex.contains(Line(start, end)))
        return Polyline{start, end};

    AvoidCrossingPerimeters::Boundary boundary = boundary_from_expolygon(ex);
    std::vector<Intersection> hits = intersections_with_boundary(boundary, start, end);

    auto accept = [&](Polyline pl) {
        dedup_polyline(pl);
        if (pl.size() >= 2 && internal_polyline_inside(ex, pl))
            return pl;
        return Polyline{};
    };

    if (hits.size() >= 2) {
        if (layer != nullptr && distinct_rings(hits) > 1 && !boundary.boundaries.empty()) {
            Polyline avoided;
            avoid_perimeters(boundary, start, end, *layer, avoided);
            if (avoided.size() >= 2) {
                avoided.points.front() = start;
                avoided.points.back()  = end;
                Polyline ok = accept(std::move(avoided));
                if (!ok.empty())
                    return ok;
            }
        }
        Polyline walked = accept(walk_boundary_hits(boundary, hits, start, end));
        if (!walked.empty())
            return walked;
    }

    const RingProjection ps = project_onto_boundary(boundary, start);
    const RingProjection pe = project_onto_boundary(boundary, end);
    if (ps.ok && pe.ok && ps.ring == pe.ring && ps.point.distance_to(pe.point) > scale_(0.01)) {
        std::vector<Intersection> injected{intersection_from_projection(boundary, ps), intersection_from_projection(boundary, pe)};
        Polyline walked = accept(walk_boundary_hits(boundary, std::move(injected), start, end));
        if (!walked.empty())
            return walked;
        Polyline along{start, ps.point, pe.point, end};
        dedup_polyline(along);
        Polyline ok = accept(std::move(along));
        if (!ok.empty())
            return ok;
    }
    return Polyline{};
}

// The whole travel, endpoints included, has to sit in `safe`. A connector that only
// crosses the 0.5 mm band still counts as a band travel, so it is not usable.
static bool travel_inside_safe(const ExPolygons &safe, const Polyline &pl)
{
    if (safe.empty() || pl.size() < 2)
        return false;
    const Polylines outside = diff_pl(Polylines{pl}, safe);
    double out_len = 0.;
    for (const Polyline &piece : outside)
        out_len += piece.length();
    return out_len <= scale_(0.05);
}

Polyline AvoidCrossingPerimeters::apply_comb_fallback(bool              enabled,
                                                      const ExPolygons &comb,
                                                      const Polyline   &original,
                                                      int               layer_id,
                                                      const Layer      *layer,
                                                      const ExPolygons *safe)
{
    if (!enabled || original.size() < 2)
        return original;

    m_island_lift = false;

    const Point start = original.points.front();
    const Point end   = original.points.back();
    auto log_entry = [&](const std::string &reason, bool routed, double detour_mm) {
        CombLogEntry entry;
        entry.reason    = reason;
        entry.layer_id  = layer_id;
        entry.from_x    = unscale_(start.x());
        entry.from_y    = unscale_(start.y());
        entry.to_x      = unscale_(end.x());
        entry.to_y      = unscale_(end.y());
        entry.detour_mm = detour_mm;
        entry.routed    = routed;
        entry.lifted    = m_island_lift;
        this->emit_comb_log(entry);
    };
    // Keep the straight XY move and ask the caller to retract and lift.
    auto lift = [&](const std::string &reason, double detour_mm) {
        m_island_lift = true;
        log_entry(reason, true, detour_mm);
        return original;
    };

    // `safe` is the outer wall minus the 0.5 mm band. nullptr checks the comb itself.
    const ExPolygons &limit = (safe != nullptr) ? *safe : comb;
    if (comb.empty() || limit.empty())
        return lift("collapsed", 0.);

    std::vector<BoundingBox> bboxes;
    bboxes.reserve(comb.size());
    for (const ExPolygon &ex : comb)
        bboxes.emplace_back(get_extents(ex));

    Point snap_start, snap_end;
    bool  in_start = false, in_end = false;
    const int island_start = locate_comb_island(comb, bboxes, start, snap_start, in_start);
    const int island_end   = locate_comb_island(comb, bboxes, end, snap_end, in_end);
    (void) snap_start;
    (void) snap_end;
    (void) in_start;
    (void) in_end;
    if (island_start < 0 || island_end < 0)
        return lift("collapsed", 0.);
    if (island_start != island_end) {
        // No inside path joins the islands. Lift (z-hop, or a belt-normal lift) instead of
        // crossing the wall on the surface.
        return lift("island", 0.);
    }

    const ExPolygon &ex = comb[size_t(island_start)];
    // Inside the safe region is not enough: the comb may have a top-surface hole the
    // straight move would still cross. Both checks have to pass.
    if (travel_inside_safe(limit, original) && internal_polyline_inside(ex, original) &&
        ex.contains(original.points.front()) && ex.contains(original.points.back()))
        return original;

    Polyline routed = route_inside_expolygon(ex, start, end, layer);
    if (routed.size() >= 2 && travel_inside_safe(limit, routed)) {
        const double detour_mm = unscale_(routed.length() - Line(start, end).length());
        // Over the cap is still used, but only because it stayed inside the safe region.
        if (detour_mm > COMB_DETOUR_CAP_MM)
            log_entry("cap", true, detour_mm);
        return routed;
    }

    // The candidate left the part or crossed the 0.5 mm band. Do not travel it.
    // A long detour is reported (reason=cap) and lifted rather than drawn outside.
    const double detour_mm = routed.size() >= 2 ? unscale_(routed.length() - Line(start, end).length()) : 0.;
    if (routed.size() >= 2 && detour_mm > COMB_DETOUR_CAP_MM)
        return lift("cap", detour_mm);
    return lift(routed.size() >= 2 ? "outside" : "collapsed", detour_mm);
}

// Plan travel, which avoids perimeter crossings by following the boundaries of the layer.
Polyline AvoidCrossingPerimeters::travel_to(const GCode &gcodegen, const Point &point, bool *could_be_wipe_disabled)
{
    // If use_external, then perform the path planning in the world coordinate system (correcting for the gcodegen offset).
    // Otherwise perform the path planning in the coordinate system of the active object.
    // GCode::travel_to() can call this twice (again after a wipe). Keep a lift the first
    // call already requested; clearing it here dropped the hop and the next route then
    // crashed on the degenerate ring the island hop had just left behind.
    const bool lift_sticky = m_island_lift;
    m_island_lift = false;
    bool        use_external  = m_use_external_mp || m_use_external_mp_once;
    Point       scaled_origin = use_external ? Point::new_scale(gcodegen.origin()(0), gcodegen.origin()(1)) : Point(0, 0);
    const Point start         = gcodegen.last_pos() + scaled_origin;
    const Point end           = point + scaled_origin;
    const Line  travel(start, end);
    Polyline result_pl;
    size_t   travel_intersection_count = 0;
    Vec2d startf = start.cast<double>();
    Vec2d endf   = end  .cast<double>();

    const ExPolygons               &lslices          = gcodegen.layer()->lslices;
    const std::vector<BoundingBox> &lslices_bboxes   = gcodegen.layer()->lslices_bboxes;
    bool                            is_support_layer = (dynamic_cast<const SupportLayer *>(gcodegen.layer()) != nullptr);
    if (!use_external && (is_support_layer || (!m_lslices_offset.empty() && !any_expolygon_contains(m_lslices_offset, m_lslices_offset_bboxes, m_grid_lslice, travel)))) {
        // Initialize m_internal only when it is necessary.
        if (m_internal.boundaries.empty()) {
            init_boundary(&m_internal, to_polygons(get_boundary(*gcodegen.layer(), get_perimeter_spacing(*gcodegen.layer()))), {start, end});
        } else if (!(m_internal.bbox.contains(startf) && m_internal.bbox.contains(endf))) {
            // check if start and end are in bbox, if not, merge start and end points to bbox
            m_internal.clear();
            init_boundary(&m_internal, to_polygons(get_boundary(*gcodegen.layer(), get_perimeter_spacing(*gcodegen.layer()))), {start, end});
        }

        if (!m_internal.boundaries.empty()) {
            travel_intersection_count = avoid_perimeters(m_internal, start, end, *gcodegen.layer(), result_pl);
            result_pl.points.front()  = start;
            result_pl.points.back()   = end;
        }
    } else if (use_external) {
        // Initialize m_external only when exist any external travel for the current layer.
        if (m_external.boundaries.empty()) {
            init_boundary(&m_external, get_boundary_external(*gcodegen.layer()), {start, end});
        } else if (!(m_external.bbox.contains(startf) && m_external.bbox.contains(endf))) {
            // check if start and end are in bbox
            m_external.clear();
            init_boundary(&m_external, get_boundary_external(*gcodegen.layer()), {start, end});
        }
        
        // Trim the travel line by the bounding box.
        if (!m_external.boundaries.empty()) 
        {
            travel_intersection_count = avoid_perimeters(m_external, start, end, *gcodegen.layer(), result_pl);
            result_pl.points.front()  = start;
            result_pl.points.back()   = end;
            
        }
    }

    if(result_pl.empty()) {
        // Travel line is completely outside the bounding box.
        result_pl                 = {start, end};
        travel_intersection_count = 0;
    }

    const ConfigOptionFloatOrPercent &opt_max_detour             = gcodegen.config().max_travel_detour_distance;
    bool                              max_detour_length_exceeded = false;
    if (opt_max_detour.value > 0) {
        double direct_length     = travel.length();
        double detour            = result_pl.length() - direct_length;
        double max_detour_length = opt_max_detour.percent ?
            direct_length * 0.01 * opt_max_detour.value :
            scale_(opt_max_detour.value);
        if (detour > max_detour_length) {
            result_pl = {start, end};
            max_detour_length_exceeded = true;
        }
    }

    // Per-travel combing fallback. Runs only after the existing route and detour check.
    // Flag off leaves result_pl untouched, so the G-code stays byte-identical.
    bool fallback_rerouted = false;
    if (!use_external && !is_support_layer && gcodegen.config().avoid_crossing_wall_fallback.value) {
        ensure_comb(*gcodegen.layer());
        const bool already_inside = !m_comb.empty() && result_pl.size() >= 2 &&
                                    any_expolygon_contains(m_comb, m_comb_bboxes, m_grid_comb, result_pl);
        if (!already_inside) {
            const Polyline before = result_pl;
            Polyline routed = apply_comb_fallback(true, m_comb, result_pl, int(gcodegen.layer()->id()), gcodegen.layer(), &m_safe);
            if (routed.size() >= 2 && routed.points != before.points) {
                result_pl = std::move(routed);
                fallback_rerouted = true;
                max_detour_length_exceeded = false;
            }
        }
    }

    if (use_external) {
        result_pl.translate(-scaled_origin);
        *could_be_wipe_disabled = false;
    } else if (max_detour_length_exceeded && !fallback_rerouted) {
        *could_be_wipe_disabled = false;
    } else
        *could_be_wipe_disabled = !need_wipe(gcodegen, m_lslices_offset, m_lslices_offset_bboxes, m_grid_lslice, travel, result_pl, travel_intersection_count);

    if (lift_sticky)
        m_island_lift = true;
    return result_pl;
}

// ************************************* AvoidCrossingPerimeters::init_layer() *****************************************

void AvoidCrossingPerimeters::init_layer(const Layer &layer)
{
    m_internal.clear();
    m_external.clear();
    m_comb.clear();
    m_safe.clear();
    m_comb_bboxes.clear();
    m_comb_boundary.clear();
    m_comb_init   = false;
    m_island_lift = false;

    m_lslices_offset.clear();
    m_lslices_offset_bboxes.clear();
    for (auto coeff : {0.6f, 0.5f, 0.45f}) {
        m_lslices_offset = offset_ex(layer.lslices, -get_external_perimeter_width(layer) * coeff);
        if (!m_lslices_offset.empty()) break;
    }    
    m_lslices_offset_bboxes.reserve(m_lslices_offset.size());
    for (const auto &ex_polygon : m_lslices_offset) m_lslices_offset_bboxes.emplace_back(get_extents(ex_polygon));

    BoundingBox bbox_slice(get_extents(layer.lslices));
    bbox_slice.offset(SCALED_EPSILON);

    m_grid_lslice.set_bbox(bbox_slice);
    //FIXME 1mm grid?
    m_grid_lslice.create(m_lslices_offset, coord_t(scale_(1.)));
}

#if 0
static double travel_length(const std::vector<TravelPoint> &travel) {
    double total_length = 0;
    for (size_t idx = 1; idx < travel.size(); ++idx)
        total_length += (travel[idx].point - travel[idx - 1].point).cast<double>().norm();

    return total_length;
}

// Called by avoid_perimeters() and by simplify_travel_heuristics().
static size_t avoid_perimeters_inner(const AvoidCrossingPerimeters::Boundary &boundary,
                                     const Point              &start,
                                     const Point              &end,
                                     std::vector<TravelPoint> &result_out)
{
    const Polygons           &boundaries = boundary.boundaries;
    const EdgeGrid::Grid     &edge_grid = boundary.grid;
    // Find all intersections between boundaries and the line segment, sort them along the line segment.
    std::vector<Intersection> intersections;
    {
        intersections.reserve(boundaries.size());
        AllIntersectionsVisitor visitor(edge_grid, intersections, Line(start, end));
        edge_grid.visit_cells_intersecting_line(start, end, visitor);
        Vec2d dir = (end - start).cast<double>();
        for (Intersection &intersection : intersections)
            intersection.distance = boundary.boundaries_params[intersection.border_idx][intersection.line_idx];
        std::sort(intersections.begin(), intersections.end(), [dir](const auto &l, const auto &r) { return (r.point - l.point).template cast<double>().dot(dir) > 0.; });
    }

    std::vector<TravelPoint> result;
    result.push_back({start, -1});
    for (auto it_first = intersections.begin(); it_first != intersections.end(); ++it_first) {
        // The entry point to the boundary polygon
        const Intersection &intersection_first = *it_first;
        // Skip the it_first from the search for the farthest exit point from the boundary polygon
        auto it_last_item = std::make_reverse_iterator(it_first) - 1;
        // Search for the farthest intersection different from it_first but with the same border_idx
        auto it_second_r  = std::find_if(intersections.rbegin(), it_last_item, [&intersection_first](const Intersection &intersection) {
            return intersection_first.border_idx == intersection.border_idx;
        });

        // Append the first intersection into the path
        size_t left_idx  = intersection_first.line_idx;
        size_t right_idx = intersection_first.line_idx + 1 == boundaries[intersection_first.border_idx].points.size() ? 0 : intersection_first.line_idx + 1;
        // Offset of the polygon's point using get_middle_point_offset is used to simplify the calculation of intersection between the
        // boundary and the travel. The appended point is translated in the direction of inward normal. This translation ensures that the
        // appended point will be inside the polygon and not on the polygon border.
        result.push_back({get_middle_point_offset(boundaries[intersection_first.border_idx], left_idx, right_idx, intersection_first.point, coord_t(SCALED_EPSILON)), int(intersection_first.border_idx)});

        // Check if intersection line also exit the boundary polygon
        if (it_second_r != it_last_item) {
            // Transform reverse iterator to forward
            auto it_second = it_second_r.base() - 1;
            // The exit point from the boundary polygon
            const Intersection &intersection_second = *it_second;
            Direction           shortest_direction  = get_shortest_direction(boundary, intersection_first, intersection_second,
                                                                  boundary.boundaries_params[intersection_first.border_idx].back());
            // Append the path around the border into the path
            if (shortest_direction == Direction::Forward)
                for (int line_idx = int(intersection_first.line_idx); line_idx != int(intersection_second.line_idx);
                    line_idx      = line_idx + 1 < int(boundaries[intersection_first.border_idx].size()) ? line_idx + 1 : 0)
                    result.push_back({get_polygon_vertex_offset(boundaries[intersection_first.border_idx],
                                                                (line_idx + 1 == int(boundaries[intersection_first.border_idx].points.size())) ? 0 : (line_idx + 1), coord_t(SCALED_EPSILON)), int(intersection_first.border_idx)});
            else
                for (int line_idx = int(intersection_first.line_idx); line_idx != int(intersection_second.line_idx);
                    line_idx      = line_idx - 1 >= 0 ? line_idx - 1 : int(boundaries[intersection_first.border_idx].size()) - 1)
                    result.push_back({get_polygon_vertex_offset(boundaries[intersection_second.border_idx], line_idx + 0, coord_t(SCALED_EPSILON)), int(intersection_first.border_idx)});

            // Append the farthest intersection into the path
            left_idx  = intersection_second.line_idx;
            right_idx = (intersection_second.line_idx >= (boundaries[intersection_second.border_idx].points.size() - 1)) ? 0 : (intersection_second.line_idx + 1);
            result.push_back({get_middle_point_offset(boundaries[intersection_second.border_idx], left_idx, right_idx, intersection_second.point, coord_t(SCALED_EPSILON)), int(intersection_second.border_idx)});
            // Skip intersections in between
            it_first = it_second;
        }
    }

    result.push_back({end, -1});

#ifdef AVOID_CROSSING_PERIMETERS_DEBUG_OUTPUT
    {
        static int iRun = 0;
        export_travel_to_svg(boundaries, Line(start, end), result, intersections,
                             debug_out_path("AvoidCrossingPerimetersInner-initial-%d.svg", iRun++));
    }
#endif /* AVOID_CROSSING_PERIMETERS_DEBUG_OUTPUT */

    if (! intersections.empty())
        result = simplify_travel(boundary, result);

#ifdef AVOID_CROSSING_PERIMETERS_DEBUG_OUTPUT
    {
        static int iRun = 0;
        export_travel_to_svg(boundaries, Line(start, end), result, intersections,
                             debug_out_path("AvoidCrossingPerimetersInner-final-%d.svg", iRun++));
    }
#endif /* AVOID_CROSSING_PERIMETERS_DEBUG_OUTPUT */

    append(result_out, std::move(result));
    return intersections.size();
}

static std::vector<TravelPoint> simplify_travel_heuristics(const AvoidCrossingPerimeters::Boundary &boundary,
                                                           const std::vector<TravelPoint> &travel)
{
    std::vector<TravelPoint>  simplified_path;
    std::vector<Intersection> intersections;
    AllIntersectionsVisitor   visitor(boundary.grid, intersections);
    simplified_path.reserve(travel.size());
    simplified_path.emplace_back(travel.front());
    for (size_t point_idx = 1; point_idx < travel.size(); ++point_idx) {
        // Skip all indexes on the same polygon
        while (point_idx < travel.size() && travel[point_idx - 1].border_idx == travel[point_idx].border_idx) {
            simplified_path.emplace_back(travel[point_idx]);
            point_idx++;
        }

        if (point_idx < travel.size()) {
            const TravelPoint       &current                 = travel[point_idx - 1];
            const TravelPoint       &next                    = travel[point_idx];
            TravelPoint              new_next                = next;
            size_t                   new_point_idx           = point_idx;
            double                   path_length             = (next.point - current.point).cast<double>().norm();
            double                   new_path_shorter_by     = 0.;
            size_t                   border_idx_change_count = 0;
            std::vector<TravelPoint> shortcut;
            for (size_t point_idx_2 = point_idx + 1; point_idx_2 < travel.size(); ++point_idx_2) {
                const TravelPoint &possible_new_next = travel[point_idx_2];
                if (travel[point_idx_2 - 1].border_idx != travel[point_idx_2].border_idx)
                    border_idx_change_count++;

                if (border_idx_change_count >= 2)
                    break;

                path_length += (possible_new_next.point - travel[point_idx_2 - 1].point).cast<double>().norm();
                double shortcut_length = (possible_new_next.point - current.point).cast<double>().norm();
                if ((path_length - shortcut_length) <= scale_(10.0))
                    continue;

                intersections.clear();
                visitor.reset();
                visitor.travel_line.a       = current.point;
                visitor.travel_line.b       = possible_new_next.point;
                boundary.grid.visit_cells_intersecting_line(visitor.travel_line.a, visitor.travel_line.b, visitor);
                if (!intersections.empty()) {
                    Vec2d dir = (visitor.travel_line.b - visitor.travel_line.a).cast<double>();
                    std::sort(intersections.begin(), intersections.end(), [dir](const auto &l, const auto &r) { return (r.point - l.point).template cast<double>().dot(dir) > 0.; });
                    size_t last_border_idx_count = 0;
                    for (const Intersection &intersection : intersections)
                        if (int(intersection.border_idx) == possible_new_next.border_idx)
                            ++last_border_idx_count;

                    if (last_border_idx_count > 0)
                        continue;

                    std::vector<TravelPoint> possible_shortcut;
                    avoid_perimeters_inner(boundary, current.point, possible_new_next.point, possible_shortcut);
                    double shortcut_travel = travel_length(possible_shortcut);
                    if (path_length > shortcut_travel && path_length - shortcut_travel > new_path_shorter_by) {
                        new_path_shorter_by = path_length - shortcut_travel;
                        shortcut            = possible_shortcut;
                        new_next            = possible_new_next;
                        new_point_idx       = point_idx_2;
                    }
                }
            }

            if (!shortcut.empty()) {
                assert(shortcut.size() >= 2);
                simplified_path.insert(simplified_path.end(), shortcut.begin() + 1, shortcut.end() - 1);
                point_idx = new_point_idx;
            }

            simplified_path.emplace_back(new_next);
        }
    }

    return simplified_path;
}

// Called by AvoidCrossingPerimeters::travel_to()
static size_t avoid_perimeters(const AvoidCrossingPerimeters::Boundary &boundary,
                               const Point              &start,
                               const Point              &end,
                               Polyline                 &result_out)
{
    // Travel line is completely or partially inside the bounding box.
    std::vector<TravelPoint> path;
    size_t num_intersections = avoid_perimeters_inner(boundary, start, end, path);
    if (num_intersections) {
        path = simplify_travel_heuristics(boundary, path);
        std::reverse(path.begin(), path.end());
        path = simplify_travel_heuristics(boundary, path);
        std::reverse(path.begin(), path.end());
    }

    result_out = to_polyline(path);

#ifdef AVOID_CROSSING_PERIMETERS_DEBUG_OUTPUT
    {
        static int iRun = 0;
        export_travel_to_svg(boundaries, Line(start, end), path, {}, debug_out_path("AvoidCrossingPerimeters-final-%d.svg", iRun ++));
    }
#endif /* AVOID_CROSSING_PERIMETERS_DEBUG_OUTPUT */

    return num_intersections;
}

// Plan travel, which avoids perimeter crossings by following the boundaries of the layer.
Polyline AvoidCrossingPerimeters::travel_to(const GCode &gcodegen, const Point &point, bool *could_be_wipe_disabled)
{
    // If use_external, then perform the path planning in the world coordinate system (correcting for the gcodegen offset).
    // Otherwise perform the path planning in the coordinate system of the active object.
    bool     use_external  = m_use_external_mp || m_use_external_mp_once;
    Point    scaled_origin = use_external ? Point::new_scale(gcodegen.origin()(0), gcodegen.origin()(1)) : Point(0, 0);
    Point    start         = gcodegen.last_pos() + scaled_origin;
    Point    end           = point + scaled_origin;
    Polyline result_pl;
    size_t   travel_intersection_count = 0;
    Vec2d startf = start.cast<double>();
    Vec2d endf   = end  .cast<double>();
    // Trim the travel line by the bounding box.
    if (Geometry::liang_barsky_line_clipping(startf, endf, (use_external ? m_external : m_internal).bbox)) {
        // Travel line is completely or partially inside the bounding box.
        //FIXME initialize m_boundaries / m_boundaries_external on demand?
        travel_intersection_count = avoid_perimeters((use_external ? m_external : m_internal), startf.cast<coord_t>(), endf.cast<coord_t>(),
                                                     result_pl);
        result_pl.points.front()  = start;
        result_pl.points.back()   = end;
    } else {
        // Travel line is completely outside the bounding box.
        result_pl                 = {start, end};
        travel_intersection_count = 0;
    }

    Line travel(start, end);
    double max_detour_length scale_(gcodegen.config().max_travel_detour_distance);
    if (max_detour_length > 0 && (result_pl.length() - travel.length()) > max_detour_length)
        result_pl = {start, end};

    if (use_external) {
        result_pl.translate(-scaled_origin);
        *could_be_wipe_disabled = false;
    } else
        *could_be_wipe_disabled = !need_wipe(gcodegen, m_grid_lslice, travel, result_pl, travel_intersection_count);

    return result_pl;
}

// called by AvoidCrossingPerimeters::init_layer()->get_boundary()/get_boundary_external()
static std::pair<Polygons, Polygons> split_expolygon(const ExPolygons &ex_polygons)
{
    Polygons contours, holes;
    contours.reserve(ex_polygons.size());
    holes.reserve(std::accumulate(ex_polygons.begin(), ex_polygons.end(), size_t(0),
                                  [](size_t sum, const ExPolygon &ex_poly) { return sum + ex_poly.holes.size(); }));
    for (const ExPolygon &ex_poly : ex_polygons) {
        contours.emplace_back(ex_poly.contour);
        append(holes, ex_poly.holes);
    }
    return std::make_pair(std::move(contours), std::move(holes));
}

// called by AvoidCrossingPerimeters::init_layer()
static ExPolygons get_boundary(const Layer &layer)
{
    const float perimeter_spacing = get_perimeter_spacing(layer);
    const float perimeter_offset  = perimeter_spacing / 2.f;
    size_t      polygons_count    = 0;
    for (const LayerRegion *layer_region : layer.regions())
        polygons_count += layer_region->slices.surfaces.size();

    ExPolygons boundary;
    boundary.reserve(polygons_count);
    for (const LayerRegion *layer_region : layer.regions())
        for (const Surface &surface : layer_region->slices.surfaces)
            boundary.emplace_back(surface.expolygon);

    boundary                      = union_ex(boundary);
    ExPolygons perimeter_boundary = offset_ex(boundary, -perimeter_offset);
    ExPolygons result_boundary;
    if (perimeter_boundary.size() != boundary.size()) {
        //FIXME ???
        // If any part of the polygon is missing after shrinking, then for misisng parts are is used the boundary of the slice.
        ExPolygons missing_perimeter_boundary = offset_ex(diff_ex(boundary,
                                                                  offset_ex(perimeter_boundary, perimeter_offset + float(SCALED_EPSILON) / 2.f)),
                                                          perimeter_offset + float(SCALED_EPSILON));
        perimeter_boundary                    = offset_ex(perimeter_boundary, perimeter_offset);
        append(perimeter_boundary, std::move(missing_perimeter_boundary));
        // By calling intersection_ex some artifacts arose by previous operations are removed.
        result_boundary = intersection_ex(offset_ex(perimeter_boundary, -perimeter_offset), boundary);
    } else {
        result_boundary = std::move(perimeter_boundary);
    }

    auto [contours, holes] = split_expolygon(boundary);
    // Add an outer boundary to avoid crossing perimeters from supports
    ExPolygons outer_boundary = union_ex(
        diff(offset(Geometry::convex_hull(contours), 2.f * perimeter_spacing), offset(contours, perimeter_spacing + perimeter_offset)));
    result_boundary.insert(result_boundary.end(), outer_boundary.begin(), outer_boundary.end());
    ExPolygons holes_boundary = offset_ex(holes, -perimeter_spacing);
    result_boundary.insert(result_boundary.end(), holes_boundary.begin(), holes_boundary.end());
    result_boundary = union_ex(result_boundary);

    // Collect all top layers that will not be crossed.
    polygons_count = 0;
    for (const LayerRegion *layer_region : layer.regions())
        for (const Surface &surface : layer_region->fill_surfaces.surfaces)
            if (surface.is_top()) ++polygons_count;

    if (polygons_count > 0) {
        ExPolygons top_layer_polygons;
        top_layer_polygons.reserve(polygons_count);
        for (const LayerRegion *layer_region : layer.regions())
            for (const Surface &surface : layer_region->fill_surfaces.surfaces)
                if (surface.is_top()) top_layer_polygons.emplace_back(surface.expolygon);

        top_layer_polygons = union_ex(top_layer_polygons);
        return diff_ex(result_boundary, offset_ex(top_layer_polygons, -perimeter_offset));
    }

    return result_boundary;
}

// called by AvoidCrossingPerimeters::init_layer()
static ExPolygons get_boundary_external(const Layer &layer)
{
    const float perimeter_spacing = get_perimeter_spacing_external(layer);
    const float perimeter_offset  = perimeter_spacing / 2.f;
    ExPolygons  boundary;
    // Collect all polygons for all printed objects and their instances, which will be printed at the same time as passed "layer".
    for (const PrintObject *object : layer.object()->print()->objects()) {
        ExPolygons polygons_per_obj;
        //FIXME with different layering, layers on other objects will not be found at this object's print_z.
        // Search an overlap of layers?
        if (const Layer* l = object->get_layer_at_printz(layer.print_z, EPSILON); l)
            for (const LayerRegion *layer_region : l->regions())
                for (const Surface &surface : layer_region->slices.surfaces)
                    polygons_per_obj.emplace_back(surface.expolygon);

        for (const PrintInstance &instance : object->instances()) {
            size_t boundary_idx = boundary.size();
            boundary.insert(boundary.end(), polygons_per_obj.begin(), polygons_per_obj.end());
            for (; boundary_idx < boundary.size(); ++boundary_idx)
                boundary[boundary_idx].translate(instance.shift);
        }
    }
    boundary               = union_ex(boundary);
    auto [contours, holes] = split_expolygon(boundary);
    // Polygons in which is possible traveling without crossing perimeters of another object.
    // A convex hull allows removing unnecessary detour caused by following the boundary of the object.
    ExPolygons result_boundary =
        diff_ex(offset(Geometry::convex_hull(contours), 2.f * perimeter_spacing),offset(contours,  perimeter_spacing + perimeter_offset));
    // All holes are extended for forcing travel around the outer perimeter of a hole when a hole is crossed.
    append(result_boundary, diff_ex(offset(holes, perimeter_spacing), offset(holes, perimeter_offset)));
    return union_ex(result_boundary);
}

void AvoidCrossingPerimeters::init_layer(const Layer &layer)
{
    m_internal.boundaries.clear();
    m_external.boundaries.clear();

    m_internal.boundaries = to_polygons(get_boundary(layer));
    m_external.boundaries = to_polygons(get_boundary_external(layer));

    BoundingBox bbox(get_extents(m_internal.boundaries));
    bbox.offset(SCALED_EPSILON);
    BoundingBox bbox_external = get_extents(m_external.boundaries);
    bbox_external.offset(SCALED_EPSILON);
    BoundingBox bbox_slice(get_extents(layer.lslices));
    bbox_slice.offset(SCALED_EPSILON);

    m_internal.bbox = BoundingBoxf(bbox.min.cast<double>(), bbox.max.cast<double>());
    m_external.bbox = BoundingBoxf(bbox_external.min.cast<double>(), bbox_external.max.cast<double>());

    m_internal.grid.set_bbox(bbox);
    //FIX1ME 1mm grid?
    m_internal.grid.create(m_internal.boundaries, coord_t(scale_(1.)));
    m_external.grid.set_bbox(bbox_external);
    //FIX1ME 1mm grid?
    m_external.grid.create(m_external.boundaries, coord_t(scale_(1.)));
    m_grid_lslice.set_bbox(bbox_slice);
    //FIX1ME 1mm grid?
    m_grid_lslice.create(layer.lslices, coord_t(scale_(1.)));

    init_boundary_distances(&m_internal);
    init_boundary_distances(&m_external);
}
#endif

} // namespace Slic3r
