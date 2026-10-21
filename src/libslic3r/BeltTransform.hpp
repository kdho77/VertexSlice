#ifndef slic3r_BeltTransform_hpp_
#define slic3r_BeltTransform_hpp_

#include "libslic3r.h"
#include "Point.hpp"

#include <cmath>

namespace Slic3r {

// Cura ApexLight reference (layer height 0.46 mm on a 45° belt): the first
// extrusion sits on the belt at Y 0.016 and each layer advances Z by 0.65.
inline constexpr double BELT_CURA_FIRST_Y_MM = 0.016;
inline constexpr double BELT_CURA_Z_PITCH_MM = 0.65;

// Virtual slice spacing is layer_height / cos(45°) = layer_height * √2.
inline double belt_geometric_pitch_mm(double layer_height_mm)
{
    return layer_height_mm * 1.41421356237;
}

// 0.46 mm layers are the Cura pitch (0.6505 virtual, emitted as 0.65).
// Other layer heights keep their geometric pitch so the part is not stretched.
inline double belt_machine_z_pitch_mm(double layer_height_mm)
{
    const double geom = belt_geometric_pitch_mm(layer_height_mm);
    if (std::abs(geom - BELT_CURA_Z_PITCH_MM) <= 0.02)
        return BELT_CURA_Z_PITCH_MM;
    return geom;
}

// Machine Z of object layer k (0 = first extruded layer).
inline double belt_layer_machine_z(int layer_index, double layer_height_mm)
{
    if (layer_index < 0)
        layer_index = 0;
    return (static_cast<double>(layer_index) + 1.0) * belt_machine_z_pitch_mm(layer_height_mm);
}

// Added to a contact point's machine Y so that point emits at the Cura belt line.
inline double belt_y_offset_to_cura(double contact_y_mach)
{
    return BELT_CURA_FIRST_Y_MM - contact_y_mach;
}

class BeltTransform {
public:
    // Create the Forward transformation matrix (Machine to Virtual Slicer Space).
    // This rotates the coordinate system so the belt plane becomes the XY plane.
    // angle_degree: Usually 45.0 degrees.
    static Transform3d make_forward_transform(double angle_degree);

    // Create the Inverse transformation matrix (Virtual Slicer Space to Machine).
    // NOTE: This is NOT just a rigid rotation inverse. It is a shear transformation
    // required to map the sliced layers onto the moving belt.
    //
    // Formulas:
    // Y_machine = z_virtual / sin(alpha)  [Belt Movement]
    // Z_machine = y_virtual - z_virtual / tan(alpha) [Head Movement]
    // X_machine = x_virtual
    //
    // However, if we follow the generic shear matrix approach:
    static Transform3d make_inverse_transform(double angle_degree);

    // Apply inverse transform to a single point explicitly (High precision)
    static Vec3d inverse_transform_point(const Vec3d& virtual_pt, double angle_degree);

    // Get the Z-shift value from config (applied in trafo_centered after belt transform)
    static double get_trafo_z_shift();

    // Live machine offsets. y is adjusted per layer so the belt contact emits at
    // BELT_CURA_FIRST_Y_MM. reset_y_mach_offset restores the ini value.
    static double z_mach_offset();
    static double y_mach_offset();
    static void   set_y_mach_offset(double y_mm);
    static void   reset_y_mach_offset();

    // Force reload of configuration from belt_transform.ini
    static void reload_config();
};

} // namespace Slic3r

#endif // slic3r_BeltTransform_hpp_
