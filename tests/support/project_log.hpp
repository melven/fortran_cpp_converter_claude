// stand-in for the project's error logging
#pragma once
#include <cstdio>

inline void project_log_error(const char* cls, const char* msg)
{
  std::fprintf(stderr, "%s: %s\n", cls, msg);
}
