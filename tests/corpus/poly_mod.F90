#include "project_macros.h"
!> Polynomials with coefficients 0..n: exercises arrays with lower bound 0.
module poly_mod
  implicit none
  private

  public :: poly_type, poly_eval, poly_derivative, moments_table, gather_shifted, weighted_norm, overlap_demo

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
    !> coefficients 0..n
    real, intent(in) :: c(0:)
    real, intent(in) :: x     !< evaluation point
    real :: y                 !< value of the polynomial
    integer :: k              !< loop index

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

  !> ghost(-1:n): values with two ghost entries in front; picks entries through an index array
  !> idx(0:m) of Fortran indices (idx(0) is not used)
  subroutine gather_shifted(x, res)
    real, intent(in) :: x(:)
    real, intent(out) :: res(:)
    real, allocatable :: ghost(:)
    integer, allocatable :: idx(:)
    integer :: n, m, k

    n = size(x)
    m = size(res)
    allocate(ghost(-1:n), idx(0:m))
    ghost(-1) = -10.0
    ghost(0) = -20.0
    ghost(1:n) = x
    idx(0) = 0
    do k = 1, m
      idx(k) = k - 2
    end do
    res = ghost(idx(1:))
    res(1) = res(1) + ghost(lbound(ghost, 1)) + ubound(ghost, 1) + sum(ghost(-1:0))
    ghost(idx(1:2)) = 1.5 * ghost(idx(1:2))
    res(m) = res(m) + ghost(-1) + ghost(idx(2))
  end subroutine gather_shifted

  !> internal procedures with host association
  function weighted_norm(x, w) result(r)
    real, intent(in) :: x(:), w
    real :: r
    integer :: k, calls

    calls = 0
    r = 0.0
    do k = 1, size(x)
      call accumulate(x(k))
    end do
    r = sqrt(r) + scaled(real(calls))
  contains
    !> adds the weighted square of v
    subroutine accumulate(v)
      real, intent(in) :: v
      r = r + w * sq(v)
      calls = calls + 1
    end subroutine accumulate

    function sq(v) result(s)
      real, intent(in) :: v
      real :: s
      s = v * v
    end function sq

    function scaled(v) result(s)
      real, intent(in) :: v
      real :: s
      s = 1.0e-3 * v
    end function scaled
  end function weighted_norm

  !> assignments whose right-hand side reads the assigned array (Fortran evaluates it first)
  subroutine overlap_demo(x, a)
    real, intent(inout) :: x(:), a(:, :)
    integer :: n
    n = size(x)
    x(2:n) = x(1:n - 1)
    x = x(n:1:-1)
    a = a + transpose(a)
    a = transpose(a)
    a(:, 1) = matmul(a, a(:, 2))
    a(2, :) = a(1, :) * 2.0
    a(1:2, 3) = a(2:3, 3) + a(1:2, 1)
  end subroutine overlap_demo

end module poly_mod
