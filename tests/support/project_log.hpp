// stand-in for the project's error logging
#pragma once
#include <cstdio>

inline void project_log_error(const char* cls, const char* msg)
{
  std::fprintf(stderr, "%s: %s\n", cls, msg);
}

#include <string>

// stand-in for the project's C++ logger
class Logger final
{
public:
  explicit Logger(const std::string& name) : name_(name) {}
  void log_error(const std::string& msg) const { std::fprintf(stderr, "%s: %s\n", name_.c_str(), msg.c_str()); }

private:
  std::string name_;
};
