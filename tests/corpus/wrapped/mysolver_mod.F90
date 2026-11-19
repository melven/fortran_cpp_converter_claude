#include "project_macros.h"
!> Solver class that stays in Fortran: wrapped for C++.
module mysolver_mod
  implicit none
  private

  public :: MySolver_type, residual_norm, scale_vector, tagged_length

  !> iteration parameters
  type :: SolverParams_type
    integer :: maxit = 10          !< maximum number of iterations
    real :: tol = 1.0e-8
    logical :: restart = .true.
    real :: w(2) = [0.5, 0.25]
  end type SolverParams_type

  type :: MySolver_type
    integer :: n = 0                   !< problem size
    !> spectral shift
    real :: shift = 0.0
    logical :: verbose = .false.
    real, allocatable :: diag(:)       !< diagonal
    real, allocatable :: coupling(:, :)
    character(len=32) :: label = ''
    real :: weights(3) = [1.0, 2.0, 3.0]   !< fixed weights
    type(SolverParams_type) :: params      !< iteration parameters
  contains
    procedure :: init => mysolver_init
    procedure :: apply => mysolver_apply
    procedure :: scale => mysolver_scale
    procedure :: norm => mysolver_norm
    procedure :: set_label => mysolver_set_label
    procedure :: label_length => mysolver_label_length
    procedure :: stats => mysolver_stats
    procedure :: is_verbose => mysolver_is_verbose
    procedure :: apply_block => mysolver_apply_block
    procedure :: set_verbose => mysolver_set_verbose
    procedure :: weighted => mysolver_weighted
    procedure :: masked_sum => mysolver_masked_sum
    procedure :: positive => mysolver_positive
  end type MySolver_type

contains

  subroutine mysolver_init(this, n, ierr)
    class(MySolver_type), intent(out) :: this
    integer, intent(in) :: n
    integer, intent(out) :: ierr
    integer :: i, j
    ierr = 0
    this%n = n
    PROJECT_ALLOCATE(this%diag(n), ierr)
    PROJECT_ALLOCATE(this%coupling(n, 2), ierr)
    do i = 1, n
      this%diag(i) = 1.0 + i
      do j = 1, 2
        this%coupling(i, j) = 0.1 * i - 0.2 * j
      end do
    end do
  end subroutine mysolver_init

  !> y = (diag + shift) * x + coupling * coupling^T x
  subroutine mysolver_apply(this, x, y, ierr)
    class(MySolver_type), intent(in) :: this
    real, intent(in) :: x(:)          !< input vector
    real, intent(out) :: y(:)         !< result
    integer, intent(out) :: ierr
    ierr = 0
    PROJECT_CHK_ARGUMENT(size(x) == this%n, ierr)
    PROJECT_CHK_ARGUMENT(size(y) == size(x), ierr)
    y = (this%diag + this%shift) * x + matmul(this%coupling, matmul(transpose(this%coupling), x))
  end subroutine mysolver_apply

  subroutine mysolver_scale(this, factor, ierr)
    class(MySolver_type), intent(inout) :: this
    real, intent(in) :: factor
    integer, intent(out) :: ierr
    ierr = 0
    PROJECT_CHK_ARGUMENT(factor > 0.0, ierr)
    this%diag = factor * this%diag
  end subroutine mysolver_scale

  function mysolver_norm(this, x) result(r)
    class(MySolver_type), intent(in) :: this
    real, intent(in) :: x(this%n)
    real :: r
    r = sqrt(sum(this%diag * x**2))
  end function mysolver_norm

  subroutine mysolver_set_label(this, label, ierr)
    class(MySolver_type), intent(inout) :: this
    character(len=*), intent(in) :: label
    integer, intent(out) :: ierr
    ierr = 0
    this%label = label
  end subroutine mysolver_set_label

  function mysolver_label_length(this) result(k)
    class(MySolver_type), intent(in) :: this
    integer :: k
    k = len_trim(this%label)
  end function mysolver_label_length

  subroutine mysolver_stats(this, mn, mx, tol, ierr)
    class(MySolver_type), intent(in) :: this
    real, intent(out) :: mn, mx
    real, intent(in), optional :: tol
    integer, intent(out) :: ierr
    ierr = 0
    mn = minval(this%diag)
    mx = maxval(this%diag)
    if (present(tol)) mx = mx + tol
  end subroutine mysolver_stats

  function mysolver_is_verbose(this) result(v)
    class(MySolver_type), intent(in) :: this
    logical :: v
    v = this%verbose
  end function mysolver_is_verbose

  subroutine mysolver_set_verbose(this, v, old)
    class(MySolver_type), intent(inout) :: this
    logical, intent(in) :: v
    logical, intent(out) :: old
    old = this%verbose
    this%verbose = v
  end subroutine mysolver_set_verbose

  !> b(:, k) = diag * a(:, k)
  subroutine mysolver_apply_block(this, a, b, ierr)
    class(MySolver_type), intent(in) :: this
    real, intent(in) :: a(:, :)
    real, intent(out) :: b(:, :)
    integer, intent(out) :: ierr
    integer :: k
    ierr = 0
    PROJECT_CHK_ARGUMENT(size(a, 1) == this%n .and. size(a, 2) == 2, ierr)
    PROJECT_CHK_ARGUMENT(size(b, 1) == this%n, ierr)
    PROJECT_CHK_ARGUMENT(size(b, 2) == 2, ierr)
    do k = 1, 2
      b(:, k) = this%diag * a(:, k)
    end do
  end subroutine mysolver_apply_block

  !> maximum norm of r - b
  function residual_norm(r, b, n) result(res)
    integer, intent(in) :: n
    real, intent(in) :: r(n), b(n)
    real :: res
    res = maxval(abs(r - b))
  end function residual_norm

  subroutine scale_vector(x, factor, ierr)
    real, intent(inout) :: x(:)
    real, intent(in) :: factor
    integer, intent(out) :: ierr
    ierr = 0
    PROJECT_CHK_ARGUMENT(factor > 0.0, ierr)
    x = factor * x
  end subroutine scale_vector

  !> length of "tag: msg" (tag optional)
  function tagged_length(msg, tag) result(k)
    character(len=*), intent(in) :: msg
    character(len=*), intent(in), optional :: tag
    integer :: k
    k = len_trim(msg)
    if (present(tag)) k = k + len_trim(tag) + 2
  end function tagged_length

  !> sum of (diag + shift) * x * w
  real function mysolver_weighted(this, x, r, w) result(total)
    class(MySolver_type), intent(in) :: this
    real, intent(in) :: x(this%n)
    real, optional, intent(out) :: r(this%n)     !< the terms (optional)
    real, optional, intent(in) :: w(this%n)      !< weights (optional, default 1)
    real :: t(this%n)
    t = (this%diag + this%shift) * x
    if (present(w)) t = t * w
    if (present(r)) r = t
    total = sum(t)
  end function mysolver_weighted
  !> sum of x where modelMask (all if absent)
  real function mysolver_masked_sum(this, x, modelMask) result(total)
    class(MySolver_type), intent(in) :: this
    real, intent(in) :: x(this%n)
    logical, optional, intent(in) :: modelMask(:)    !< entries to use
    if (present(modelMask)) then
      total = sum(x, mask=modelMask)
    else
      total = sum(x)
    end if
  end function mysolver_masked_sum

  subroutine mysolver_positive(this, x, pos)
    class(MySolver_type), intent(in) :: this
    real, intent(in) :: x(this%n)
    logical, intent(out) :: pos(this%n)              !< x > 0
    pos = x > 0.0
  end subroutine mysolver_positive
end module mysolver_mod
