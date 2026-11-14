// C++ counterpart of tests/corpus/driver.F90 (hand-written); prints the same numbers.
#include <cstdio>
#include <memory>

#include "block_lu_mod.hpp"
#include "expm_mod.hpp"
#include "shape_mod.hpp"

using namespace project;

static void print(const Eigen::Ref<const mat>& m)
{
  for (Eigen::Index i = 0; i < m.size(); ++i) std::printf("%24.15E", m.data()[i]);
  std::printf("\n");
}

int main()
{
  mat A(3, 3), B(3, 2), E(3, 3), M(3, 2), blk(2, 2);
  A << 0.1, 1.2, 2.0, -0.3, 0.4, 0.3, 0.7, -0.5, -1.1;
  B << 1.0, -1.0, 0.0, 0.5, 2.0, 0.25;
  expm(A, 1.7, E);
  print(E);
  expm_moment(A, B, 0.9, M);
  print(M);
  block_lu lu(3, 2);
  for (int i = 1; i <= 3; ++i) {
    blk << 4.0 + i, -1.0, 1.0, 3.0;
    lu.set_block(i, i, blk);
    if (i < 3) {
      blk << 0.5, 0.2, 0.1 * i, -0.3;
      lu.set_block(i, i + 1, blk);
      blk << -0.4, 0.1 * i, 0.3, 0.6;
      lu.set_block(i + 1, i, blk);
    }
  }
  lu.factorize();
  vec x(6);
  x << 1.0, 2.0, 3.0, 4.0, 5.0, 6.0;
  lu.solve(x);
  print(x);
  std::unique_ptr<shape> s1, s2;
  make_shape(1, s1);
  make_shape(2, s2);
  s1->scale(2.0);
  s2->scale(3.0);
  std::printf("%24.15E%24.15E%24.15E\n", total_area(*s1, *s2), s1->scaled_area(2.0), s2->scaled_area());
  std::printf("%4d%4d\n", describe(*s1), describe(*s2));
  return 0;
}
