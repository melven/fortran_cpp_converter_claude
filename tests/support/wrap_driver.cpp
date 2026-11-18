// C++ counterpart of tests/corpus/wrapped/ref_driver.F90: the solver is a wrapped Fortran object.
#include <cstdio>
#include <memory>
#include <stdexcept>

#include "mysolver_mod.hpp"
#include "solver_user_mod.hpp"

extern "C" project_MySolver* project_MySolver_create(int n);

using namespace project;

int main()
{
  std::unique_ptr<MySolver> ps = MySolver_private::wrapFromC(*project_MySolver_create(3));
  MySolver& s = *ps;
  vec x(3), y;
  x << 1.0, -2.0, 0.5;
  std::printf("%24.15E\n", run_solver(s, x));
  s.apply(x, y);
  std::printf("%24.15E%24.15E%24.15E\n", y(0), y(1), y(2));
  std::printf("%24.15E\n", s.shift());
  std::printf("%24.15E%24.15E%24.15E\n", double(s.n()), s.diag()(2), s.weights()(2));   // constants
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
