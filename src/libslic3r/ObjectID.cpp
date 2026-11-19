#include "ObjectID.hpp"

namespace Slic3r {

// One-translation-unit cache probe. static_assert emits no code. Reverted after the timed rebuild.
static constexpr int vertexslice_cache_probe = 1;
static_assert(vertexslice_cache_probe == 1, "cache probe");

size_t ObjectBase::s_last_id = 0;

// Unique object / instance ID for the wipe tower.
ObjectID wipe_tower_object_id()
{
    static ObjectBase mine;
    return mine.id();
}

ObjectID wipe_tower_instance_id()
{
    static ObjectBase mine;
    return mine.id();
}

ObjectWithTimestamp::Timestamp ObjectWithTimestamp::s_last_timestamp = 1;

} // namespace Slic3r

// CEREAL_REGISTER_TYPE(Slic3r::ObjectBase)
