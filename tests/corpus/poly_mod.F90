#include "project_macros.h"
!> Polynomials with coefficients 0..n: exercises arrays with lower bound 0.
module poly_mod
  implicit none
  private

  public :: poly_type, poly_eval, poly_derivative, moments_table

  type :: poly_type
    integer :: degree = -1
    real, allocatable :: coeff(:)      !< coefficients 0..degree
  contains
    procedure :: set => poly_set
    procedure :: eval => poly_eval_method
  end type poly_type

contains

  subroutine poly_set(this, c, ierr)
    class(poly_type), intent(inout) :: this
    real, intent(in) :: c(0:)
    integer, intent(out) :: ierr

    ierr = 0
    this%degree = ubound(c, 1)
    if (allocated(this%coeff)) deallocate(this%coeff)
    PROJECT_ALLOCATE(this%coeff(0:this%degree), ierr)
    this%coeff = c
    PROJECT_ASSERT(this%coeff(this%degree) == c(size(c) - 1), ierr)
  end subroutine poly_set

  function poly_eval_method(this, x) result(y)
    class(poly_type), intent(in) :: this
    real, intent(in) :: x
    real :: y
    y = poly_eval(this%coeff, x) + 0.0 * this%coeff(0)
  end function poly_eval_method

  !> Horner scheme
  function poly_eval(c, x) result(y)
    real, intent(in) :: c(0:)
    real, intent(in) :: x
    real :: y
    integer :: k

    y = c(ubound(c, 1))
    do k = ubound(c, 1) - 1, lbound(c, 1), -1
      y = y * x + c(k)
    end do
  end function poly_eval

  subroutine poly_derivative(c, d)
    real, intent(in) :: c(0:)
    real, intent(out) :: d(0:)
    integer :: k, n
    real :: w(0:size(c) - 1)

    n = size(c) - 1
    w = 0.0
    do k = 1, n
      w(k - 1) = k * c(k)
    end do
    d(0:n - 1) = w(:n - 1)
    d(n:) = 0.0
  end subroutine poly_derivative

  !> table(i, j) = x(i)**j * sum_k c(k) * x(i)**k + c(0), i = 1..m, j = 0..1
  subroutine moments_table(c, x, table, hist)
    real, intent(in) :: c(0:), x(:)
    real, intent(out) :: table(:, 0:)
    real, intent(out) :: hist(:, :, 0:)
    real, allocatable :: p(:, :)
    integer :: i, j, k

    allocate(p(0:ubound(c, 1), size(x)))
    do i = 1, size(x)
      do k = 0, ubound(c, 1)
        p(k, i) = c(k) * x(i)**k
      end do
    end do
    do j = 0, ubound(table, 2)
      do i = 1, size(x)
        table(i, j) = sum(p(:, i)) * x(i)**j + p(0, i)
      end do
    end do
    do k = lbound(hist, 3), ubound(hist, 3)
      hist(:, :, k) = (k + 1) * table(1:2, 0:1)
    end do
    hist(1, 2, 0) = sum(c(1:)) + c(0) + maxval(p(0:1, 1)) + size(p, 1)
  end subroutine moments_table

end module poly_mod
