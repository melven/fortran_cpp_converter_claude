!> @file: expm_mod.F90
!> @brief Matrix exponential (synthetic test corpus)
!! @date 2020-01-01
!
! Some licence text.
#include "project_macros.h"
!> Matrix exponential and moments of the matrix exponential.
module expm_mod
  implicit none
  private

  public :: expm, expm_moment, identity

  integer, parameter :: max_squarings = 60
  real, parameter :: pade_coeff(7) = [1.0, 0.5, 5.0/44.0, 1.0/66.0, 1.0/792.0, 1.0/15840.0, 1.0/665280.0]

contains

  !> identity matrix of size n
  function identity(n) result(eye)
    integer, intent(in) :: n
    real :: eye(n, n)
    integer :: i

    eye = 0.0
    do i = 1, n
      eye(i, i) = 1.0
    end do
  end function identity

  !> E = exp(t*A) using a diagonal Pade approximation with scaling and squaring
  subroutine expm(A, t, E, ierr)
    real, intent(in) :: A(:, :)
    real, intent(in) :: t
    real, intent(out) :: E(:, :)
    integer, intent(out) :: ierr

    real, allocatable :: As(:, :), N(:, :), D(:, :), P(:, :)
    real :: nrm, c
    integer :: m, s, k

    ierr = 0
    m = size(A, 1)
    PROJECT_CHK_ARGUMENT(size(A, 2) == m, ierr)
    PROJECT_CHK_ARGUMENT(size(E, 1) == m .and. size(E, 2) == m, ierr)

    PROJECT_ALLOCATE(As(m, m), ierr)
    PROJECT_ALLOCATE(N(m, m), ierr)
    PROJECT_ALLOCATE(D(m, m), ierr)
    PROJECT_ALLOCATE(P(m, m), ierr)

    ! scaling
    nrm = maxval(abs(A)) * abs(t) * m
    s = 0
    if (nrm > 0.5) then
      s = max(0, ceiling(log(nrm / 0.5) / log(2.0)))
    end if
    PROJECT_ASSERT(s <= max_squarings, ierr)
    As = (t / 2.0**s) * A

    ! Pade numerator and denominator
    P = identity(m)
    N = pade_coeff(1) * P
    D = N
    do k = 2, size(pade_coeff)
      c = pade_coeff(k)
      P = matmul(As, P)
      N = N + c * P
      if (mod(k, 2) == 0) then
        D = D - c * P
      else
        D = D + c * P
      end if
    end do

    PROJECT_CHK_IERR(call solve_inplace(D, N, ierr), ierr)

    ! squaring
    do k = 1, s
      N = matmul(N, N)
    end do
    E = N
  end subroutine expm

  !> M = int_0^t exp(s*A) * B ds, computed from an augmented matrix exponential
  subroutine expm_moment(A, B, t, M, ierr)
    real, intent(in) :: A(:, :), B(:, :)
    real, intent(in) :: t
    real, intent(out) :: M(:, :)
    integer, intent(out) :: ierr

    real, allocatable :: aug(:, :), eaug(:, :)
    integer :: n, p

    ierr = 0
    n = size(A, 1)
    p = size(B, 2)
    PROJECT_CHK_ARGUMENT(size(B, 1) == n, ierr)

    PROJECT_ALLOCATE(aug(n + p, n + p), ierr)
    PROJECT_ALLOCATE(eaug(n + p, n + p), ierr)
    aug = 0.0
    aug(1:n, 1:n) = A
    aug(1:n, n + 1:n + p) = B

    PROJECT_CHK_IERR(call expm(aug, t, eaug, ierr), ierr)
    M = eaug(1:n, n + 1:n + p)

    deallocate(aug, eaug)
  end subroutine expm_moment

  !> solve A X = B, B is overwritten with X
  subroutine solve_inplace(A, B, ierr)
    real, intent(inout) :: A(:, :)
    real, intent(inout) :: B(:, :)
    integer, intent(out) :: ierr

    integer, allocatable :: ipiv(:)
    integer :: n, info

    ierr = 0
    n = size(A, 1)
    PROJECT_ALLOCATE(ipiv(n), ierr)
    call dgesv(n, size(B, 2), A, n, ipiv, B, n, info)
    PROJECT_ASSERT(info == 0, ierr)
  end subroutine solve_inplace

end module expm_mod
