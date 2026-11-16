// Transitional wrappers: let translated code keep its LAPACK calls (same argument lists as
// in Fortran) until they are replaced by Eigen decompositions by hand.
#pragma once
#include <Eigen/Dense>

extern "C" {
void dgesv_(const int* n, const int* nrhs, double* a, const int* lda, int* ipiv, double* b,
            const int* ldb, int* info);
void dgetrf_(const int* m, const int* n, double* a, const int* lda, int* ipiv, int* info);
void dgetrs_(const char* trans, const int* n, const int* nrhs, const double* a, const int* lda,
             const int* ipiv, double* b, const int* ldb, int* info);
}

namespace project {

inline void dgesv(int n, int nrhs, Eigen::Ref<Eigen::MatrixXd> A, int lda, Eigen::Ref<Eigen::VectorXi> ipiv,
                  Eigen::Ref<Eigen::MatrixXd> B, int ldb, int& info)
{
  dgesv_(&n, &nrhs, A.data(), &lda, ipiv.data(), B.data(), &ldb, &info);
}

inline void dgetrf(int m, int n, Eigen::Ref<Eigen::MatrixXd> A, int lda, Eigen::Ref<Eigen::VectorXi> ipiv,
                   int& info)
{
  dgetrf_(&m, &n, A.data(), &lda, ipiv.data(), &info);
}

inline void dgetrs(const char* trans, int n, int nrhs, const Eigen::Ref<const Eigen::MatrixXd>& A, int lda,
                   const Eigen::Ref<const Eigen::VectorXi>& ipiv, Eigen::Ref<Eigen::MatrixXd> B, int ldb,
                   int& info)
{
  dgetrs_(trans, &n, &nrhs, A.data(), &lda, ipiv.data(), B.data(), &ldb, &info);
}

}  // namespace project
