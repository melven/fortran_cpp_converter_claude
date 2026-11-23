// Wrapped class hierarchy: body (root, deferred volume) and sphere (derived), both wrapped Fortran.
#include <cstdio>
#include <memory>
#include <stdexcept>

#include "sphere_mod.hpp"

extern "C" project_body* project_body_create_sphere(double r, double m);
extern "C" void project_body_release(project_body* h);

using namespace project;

int main()
{
  project_body* handle = project_body_create_sphere(2.0, 3.0);
  std::unique_ptr<sphere> ps = sphere_private::wrapFromC(*handle);
  sphere& s = *ps;
  body& b = s;
  std::printf("%.15e %.15e %.15e\n", b.volume(), b.density(), s.surface());   // dispatch in Fortran
  s.grow(0.5);
  b.rescale(2.0);
  std::printf("%.15e %.15e %.15e\n", s.radius(), s.mass(), b.density());
  try
  {
    s.grow(-10.0);
  }
  catch (const std::exception& e)
  {
    std::printf("%s\n", e.what());
  }
  ps.reset();
  project_body_release(handle);
  return 0;
}
