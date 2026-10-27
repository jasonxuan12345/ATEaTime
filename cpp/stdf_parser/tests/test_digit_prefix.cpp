/// Phase 7A unit tests: starts_with_digit_prefix
#include <cassert>
#include <cstdint>
#include <cstdio>

inline bool starts_with_digit_prefix(std::uint32_t num, std::uint32_t prefix) noexcept {
    if (prefix == 0) return num == 0;
    if (num < prefix) return false;
    std::uint64_t scale = prefix;
    while (scale <= static_cast<std::uint64_t>(num) / 10) {
        scale *= 10;
    }
    return static_cast<std::uint32_t>(num / scale) == prefix;
}

int main() {
    // Should match
    assert(starts_with_digit_prefix(700, 700));
    assert(starts_with_digit_prefix(7000, 700));
    assert(starts_with_digit_prefix(7001, 700));
    assert(starts_with_digit_prefix(70000, 700));
    assert(starts_with_digit_prefix(70001, 700));
    assert(starts_with_digit_prefix(70099, 700));
    assert(starts_with_digit_prefix(700123, 700));

    // Should NOT match
    assert(!starts_with_digit_prefix(70, 700));
    assert(!starts_with_digit_prefix(699, 700));
    assert(!starts_with_digit_prefix(701, 700));
    assert(!starts_with_digit_prefix(1700, 700));
    assert(!starts_with_digit_prefix(27001, 700));

    // Edge cases
    assert(starts_with_digit_prefix(1, 1));
    assert(starts_with_digit_prefix(10, 1));
    assert(starts_with_digit_prefix(100, 1));
    assert(!starts_with_digit_prefix(0, 1));
    assert(starts_with_digit_prefix(0, 0));
    assert(!starts_with_digit_prefix(1, 0));
    assert(starts_with_digit_prefix(12345, 12));
    assert(!starts_with_digit_prefix(12345, 123));

    std::printf("test_digit_prefix: all passed\n");
    return 0;
}
