// Stand-in for the project's C++ error handling macros (test support only).
#pragma once
#include <stdexcept>

#define PROJECT_ASSERT(cond) \
  do { if (!(cond)) throw std::logic_error("assertion failed: " #cond); } while (false)
#define PROJECT_CHK_ARGUMENT(cond) \
  do { if (!(cond)) throw std::invalid_argument("invalid argument: " #cond); } while (false)
