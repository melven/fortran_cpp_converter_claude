// Stand-in for the project's EXPECT_NEAR / ASSERT_NEAR support for Eigen types (test support only).
#pragma once
#include <gtest/gtest.h>
#include <Eigen/Dense>

namespace testing {
namespace internal {

template <class A, class B>
AssertionResult DoubleNearPredFormat(const char* expr1, const char* expr2, const char* abs_error_expr,
                                     const Eigen::MatrixBase<A>& val1, const Eigen::MatrixBase<B>& val2,
                                     double abs_error)
{
  if (val1.rows() != val2.rows() || val1.cols() != val2.cols())
    return AssertionFailure() << expr1 << " and " << expr2 << " have different shapes";
  const double diff = val1.size() == 0 ? 0.0 : (val1 - val2).cwiseAbs().maxCoeff();
  if (diff <= abs_error) return AssertionSuccess();
  return AssertionFailure() << "The maximal difference between " << expr1 << " and " << expr2 << " is " << diff
                            << ", which exceeds " << abs_error_expr;
}

}  // namespace internal
}  // namespace testing
