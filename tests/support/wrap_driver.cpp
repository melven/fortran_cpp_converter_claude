// C++ counterpart of tests/corpus/wrapped/ref_driver.F90: the solver is a wrapped Fortran object.
#include <cstdio>
#include <memory>
#include <stdexcept>

#include "mysolver_mod.hpp"
#include "solver_user_mod.hpp"

extern "C" project_MySolver* project_MySolver_create(int n);
extern "C" void project_MySolver_release(project_MySolver* h);

using namespace project;

int main()
{
  project_MySolver* handle = project_MySolver_create(3);
  std::unique_ptr<MySolver> ps = MySolver_private::wrapFromC(*handle);
  MySolver& s = *ps;
  vec x(3), y;
  x << 1.0, -2.0, 0.5;
  std::printf("%24.15E\n", run_solver(s, x));
  s.apply(x, y);
  std::printf("%24.15E%24.15E%24.15E\n", y(0), y(1), y(2));
  std::printf("%24.15E\n", s.shift());
  std::printf("%24.15E%24.15E%24.15E\n", double(s.n()), s.diag()(2), s.weights()(2));   // constants
  std::printf("%24.15E%24.15E%24.15E\n", double(s.params().maxit), s.params().tol, s.params().w(1));
  std::printf("%24.15E\n", s.weighted(x));                         // optional arrays absent
  vec w(3), r;
  w << 1.0, 0.5, 2.0;
  scalar tw = s.weighted(x, std::ref(r), w);                       // present
  std::printf("%24.15E%24.15E%24.15E%24.15E\n", tw, r(0), r(1), r(2));
  std::printf("%24.15E\n", s.weighted(x));                         // absent again (no stale pointer)
  Eigen::Array<bool, Eigen::Dynamic, 1> pos;
  s.positive(x, pos);                                               // logical out array
  std::printf("%24.15E%24.15E%24.15E\n", s.masked_sum(x), s.masked_sum(x, pos), pos(2) ? 1.0 : 0.0);
  s.verbose(true);                                                  // logical setter
  std::printf("%d\n", s.verbose() && s.is_verbose() ? 1 : 0);
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
  ps.reset();
  project_MySolver_release(handle);
  return 0;
}
