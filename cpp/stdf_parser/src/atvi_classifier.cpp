// Frozen TEST-OUTCOME-001 classifier (mirrors
// deepyield/compute/test_item_outcome_classifier.py).
#include "deepyield_stdf/atvi_writer.hpp"

#include <cmath>
#include <optional>

namespace deepyield_stdf {

OutcomeClassification classify_test_item_outcome(
    std::optional<std::uint8_t> test_flag,
    std::optional<std::uint8_t> param_flag,
    std::optional<double> result_value,
    std::optional<double> low_limit,
    std::optional<double> high_limit,
    bool catalog_conflict,
    bool missing_catalog,
    bool missing_device) {
  const bool test_flag_valid = test_flag.has_value();
  const int test_flag_int = test_flag.value_or(0);
  const bool reject_test_flag = test_flag_valid && ((test_flag_int & 0x3E) != 0);
  const bool no_explicit = test_flag_valid && ((test_flag_int & 0x40) != 0);
  const bool explicit_fail =
      test_flag_valid && !reject_test_flag && !no_explicit && ((test_flag_int & 0x80) != 0);
  const bool explicit_pass =
      test_flag_valid && !reject_test_flag && !no_explicit && ((test_flag_int & 0x80) == 0) &&
      ((test_flag_int & 0x01) == 0);
  if (explicit_fail) return {"FAIL", "EXPLICIT"};
  if (explicit_pass) return {"PASS", "EXPLICIT"};

  const bool param_flag_valid = param_flag.has_value();
  const int param_flag_int = param_flag.value_or(0);
  const bool param_reject = param_flag_valid && ((param_flag_int & 0x07) != 0);
  const bool alternate_limits = param_flag_valid && ((param_flag_int & 0x20) != 0);
  const bool result_valid = result_value.has_value() && std::isfinite(*result_value);
  const bool low_valid = low_limit.has_value() && std::isfinite(*low_limit);
  const bool high_valid = high_limit.has_value() && std::isfinite(*high_limit);
  const bool invalid_limits =
      (low_limit.has_value() && !low_valid) || (high_limit.has_value() && !high_valid) ||
      (low_valid && high_valid && *low_limit > *high_limit);
  const bool has_limits = low_valid || high_valid;
  const bool eligible = !reject_test_flag && !missing_device && !catalog_conflict &&
                        !missing_catalog && !param_reject && !alternate_limits &&
                        param_flag_valid && result_valid && has_limits && !invalid_limits;
  if (eligible) {
    const double result = *result_value;
    if ((low_valid && result < *low_limit) || (high_valid && result > *high_limit)) {
      return {"FAIL", "INFERRED_LIMIT"};
    }
    return {"PASS", "INFERRED_LIMIT"};
  }
  return {"UNKNOWN", "UNAVAILABLE"};
}

std::string value_state_of(std::optional<double> value) {
  if (!value.has_value()) return "NULL";
  if (std::isfinite(*value)) return "FINITE";
  return "NON_FINITE";
}

}  // namespace deepyield_stdf
