// C++ counterpart of tests/corpus/wrapped/ref_driver.F90: the solver is a wrapped Fortran object.
#include <cstdio>
#include <stdexcept>

#include "mysolver_mod.hpp"
#include "solver_user_mod.hpp"

extern "C" project_MySolver project_MySolver_create(int n);

using namespace project;

int main()
{
  MySolver s(project_MySolver_create(3));
  vec x(3), y;
  x << 1.0, -2.0, 0.5;
  std::printf("%24.15E\n", run_solver(s, x));
  s.apply(x, y);
  std::printf("%24.15E%24.15E%24.15E\n", y(0), y(1), y(2));
  std::printf("%24.15E\n", s.shift());
  try
  {
    vec wrong(2);
    s.apply(wrong, y);                         // size check in Fortran -> ierr -> exception
    std::printf("no exception\n");
  }
  catch (const std::exception& e)
  {
    std::printf("%s\n", e.what());
  }
  return 0;
}
