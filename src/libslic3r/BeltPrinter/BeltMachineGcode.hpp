#ifndef slic3r_BeltPrinter_BeltMachineGcode_hpp_
#define slic3r_BeltPrinter_BeltMachineGcode_hpp_

#include <string>

namespace Slic3r {

// Machine start/end restored after the Klipper switch stopped injecting
// temperature commands. Klipper does not add M104/M109 on its own, so they
// live in this block. No M140 S35. Temperature is the filament's own
// initial-layer nozzle temperature (ApexLight 260, ApexCore 225).
inline std::string belt_machine_start_gcode(int nozzle_temp_c)
{
    return std::string("M201 X5000 Y5000 Z100 E5000\n")
        + "M203 X500 Y500 Z20 E60\n"
        + "M205 X10.00 Y10.00 Z0.40 E2.50\n"
        + "PRINT_START\n"
        + "M104 S" + std::to_string(nozzle_temp_c) + "\n"
        + "M105\n"
        + "M109 S" + std::to_string(nozzle_temp_c) + "\n"
        + "SET_PRESSURE_ADVANCE ADVANCE=0.05\n";
}

inline std::string belt_machine_end_gcode()
{
    return std::string("M107\n")
        + "G92 E0\n"
        + "G1 E-1\n"
        + "G90\n"
        + "G91\n"
        + "G92 Z0\n"
        + "G28 Y\n"
        + "G28 X\n"
        + "M104 S0\n"
        + "M140 S0\n"
        + "G92 Z0\n"
        + "M82\n"
        + "PRINT_END\n";
}

} // namespace Slic3r

#endif
