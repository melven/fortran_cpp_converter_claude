// Wrapped class hierarchy: body (root, deferred volume) and sphere (derived), both wrapped Fortran.
#include <cstdio>
#include <stdexcept>

#include "sphere_mod.hpp"

extern "C" project_body project_body_create_sphere(double r, double m);

using namespace project;

int main()
{
  sphere s(project_body_create_sphere(2.0, 3.0));
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
  return 0;
}
