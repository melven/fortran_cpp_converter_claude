// Stand-in for an existing C++ class that replaces a Fortran module (test support only).
#pragma once
#include "project_math.hpp"

namespace wrappers {

class Legacy {
public:
  void update(const Eigen::Ref<const project::vec>& v) { value_ = v.sum(); }
  project::scalar get_value() const { return value_; }
  void add(project::scalar x) { value_ += x; }

private:
  project::scalar value_ = 0;
};

inline void legacy_helper(Legacy& obj, project::scalar x) { obj.add(x); }

}  // namespace wrappers

namespace project {
using wrappers::legacy_helper;
}
