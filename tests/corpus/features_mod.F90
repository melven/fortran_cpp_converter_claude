#include "project_macros.h"
!> Collection of language features (compile-checked only).
module features_mod
  use shape_mod
  use block_lu_mod, only: block_lu_type
  use legacy_mod, only: legacy_type, legacy_helper, str
  implicit none
  private

  public :: stats_type, clamp, mean, norm_inf, accumulate, describe_kind, fill, apply_scale
  public :: holder_type, update_holder, setup_solver, grid_type, grid_demo
  public :: accurate_sum, count_positive, swap_rows, shape_ratio, use_legacy, first_index

  integer, parameter, public :: max_items = 10
  integer :: call_counter = 0

  interface clamp
    module procedure clamp_real, clamp_int
  end interface clamp

  type :: stats_type
    integer :: n = 0
    real :: total = 0.0
    real, allocatable :: samples(:)
    type(legacy_type) :: legacy
    class(shape_type), allocatable :: owner
  contains
    procedure :: add => stats_add
    procedure :: mean => stats_mean
    procedure, nopass :: version => stats_version
    final :: stats_cleanup
  end type stats_type

  type :: holder_type
    type(stats_type) :: inner
    type(stats_type), pointer :: owned => null()
  end type holder_type

  type :: grid_type
    real, allocatable :: blocks(:, :, :, :)       !< dense blocks (bs, bs, i, j)
    type(stats_type), allocatable :: cells(:, :)
    real, allocatable :: spare(:)
  end type grid_type

contains

  subroutine grid_demo(g, n, v, iperm, ierr)
    type(grid_type), intent(inout) :: g
    integer, intent(in) :: n
    real, intent(inout) :: v(:)
    integer, intent(in) :: iperm(:)
    integer, intent(out) :: ierr
    real, allocatable :: tmp(:)
    integer, allocatable :: order(:)
    integer :: i, j
    interface
      subroutine sort_rank(x, perm)
        real, intent(in) :: x(:)
        integer, intent(out) :: perm(:)
      end subroutine sort_rank
    end interface

    PROJECT_ALLOCATE(g%blocks(2, 2, n, n + 1), ierr)
    PROJECT_ALLOCATE(g%cells(n, n + 1), ierr)
    g%blocks = 0.0
    do j = 1, size(g%blocks, 4)
      do i = 1, size(g%blocks, 3)
        g%blocks(:, :, i, j) = real(i + j)
        g%blocks(1, 2, i, j) = g%blocks(2, 1, i, j) + g%cells(i, j)%total + size(g%blocks, 1)
      end do
    end do
    PROJECT_ALLOCATE(order(size(v)), ierr)
    call sort_rank(v, order)
    tmp = v(order)
    v(iperm) = tmp
    call move_alloc(tmp, g%spare)
  end subroutine grid_demo

  subroutine setup_solver(lu, n, ierr)
    type(block_lu_type), intent(out) :: lu
    integer, intent(in) :: n
    integer, intent(out) :: ierr
    type(block_lu_type) :: tmp

    PROJECT_CHK_IERR(call lu%init(n, 2, ierr), ierr)
    PROJECT_CHK_IERR(call tmp%init(nblocks=1, bs=n, ierr=ierr), ierr)
    PROJECT_CHK_IERR(call tmp%finalize(ierr), ierr)
  end subroutine setup_solver

  subroutine update_holder(h, x, ierr)
    type(holder_type), intent(inout), target :: h
    real, intent(in) :: x
    integer, intent(out) :: ierr
    type(stats_type), pointer :: s
    real, pointer :: smp(:)

    ierr = 0
    s => h%inner
    PROJECT_CHK_IERR(call s%add(x, ierr), ierr)
    s%total = s%total + 1.0
    if (.not. associated(h%owned)) then
      allocate(h%owned)
    end if
    PROJECT_CHK_IERR(call h%owned%add(x, ierr), ierr)
    smp => s%samples
    smp(1) = 2.0 * smp(1)
  end subroutine update_holder

  pure function clamp_real(x, lo, hi) result(y)
    real, intent(in) :: x, lo, hi
    real :: y
    y = min(max(x, lo), hi)
  end function clamp_real

  pure function clamp_int(x, lo, hi) result(y)
    integer, intent(in) :: x, lo, hi
    integer :: y
    y = min(max(x, lo), hi)
  end function clamp_int

  function mean(x, weights, ierr) result(m)
    real, intent(in) :: x(:)
    real, intent(in), optional :: weights(:)
    integer, intent(out) :: ierr
    real :: m
    ierr = 0
    PROJECT_CHK_ARGUMENT(size(x) > 0, ierr)
    if (present(weights)) then
      m = dot_product(x, weights) / sum(weights)
    else
      m = sum(x) / real(size(x))
    end if
  end function mean

  function norm_inf(x) result(r)
    real, intent(in) :: x(:)
    real :: r
    r = maxval(abs(x))
    if (any(x /= x)) r = huge(r)
  end function norm_inf

  subroutine accumulate(x, y, alpha, scale, ierr)
    real, intent(in) :: x(:)
    real, intent(inout) :: y(:)
    real, intent(in) :: alpha
    real, intent(in), optional :: scale
    integer, intent(out) :: ierr
    real :: w(size(x))
    integer :: i

    ierr = 0
    call_counter = call_counter + 1
    w = alpha * x + 1.0
    w = w * x / (abs(x) + 1.0e-3)
    y = y + sqrt(abs(w)) + exp(-x)**2
    do i = size(x), 2, -1
      y(i) = y(i) + y(i - 1)
    end do
    if (present(scale)) y = scale * y
    PROJECT_CHK_IERR(call fill(w, value=0.0, ierr=ierr), ierr)
    PROJECT_CHK_IERR(call fill(y(2:), 1.0, ierr=ierr), ierr)
    if (ierr /= 0) then
      print *, 'accumulate failed'
      return
    end if
  end subroutine accumulate

  subroutine fill(x, value, first, ierr)
    real, intent(out) :: x(:)
    real, intent(in) :: value
    integer, intent(in), optional :: first
    integer, intent(out) :: ierr
    integer :: i0
    ierr = 0
    i0 = 1
    if (present(first)) i0 = first
    x(i0:) = value
    x(:i0 - 1) = 0.0
  end subroutine fill

  function describe_kind(k, label) result(text)
    integer, intent(in) :: k
    character(len=*), intent(in) :: label
    character(len=64) :: text
    select case (k)
    case (:0)
      text = 'non-positive'
    case (1, 2)
      text = 'small ' // trim(label)
    case (3:max_items)
      text = 'medium ' // str(k) // '/' // str(max_items)
    case default
      text = 'large'
    end select
  end function describe_kind

  subroutine apply_scale(s, factor, ierr)
    class(shape_type), intent(inout) :: s
    real, intent(in) :: factor
    integer, intent(out) :: ierr
    logical :: done
    integer :: iter
    done = .false.
    iter = 0
    do while (.not. done)
      iter = iter + 1
      PROJECT_CHK_IERR(call s%scale(factor, ierr), ierr)
      done = s%area() > 1.0 .or. iter >= max_items
    end do
    do
      iter = iter - 1
      if (iter <= 0) exit
      if (mod(iter, 2) == 0) cycle
    end do
  end subroutine apply_scale

  !> higher precision summation
  function accurate_sum(x) result(s)
    real(kind=8), intent(in) :: x(:)
    real(kind=8) :: s
    real(kind=8) :: c, t, yy
    integer :: i
    s = 0.0d0
    c = 0.0d0
    do i = 1, size(x)
      yy = x(i) - c
      t = s + yy
      c = (t - s) - yy
      s = t
    end do
  end function accurate_sum

  function count_positive(a) result(n)
    real, intent(in) :: a(:, :)
    integer :: n
    integer :: i, j
    real :: b(size(a, 1), size(a, 2))
    n = 0
    b = 0.0
    do j = 1, size(a, 2)
      do i = 1, size(a, 1)
        if (a(i, j) > 0.0 .and. .not. (a(i, j) > 1.0e10)) n = n + 1
      end do
    end do
    where (a > 0.0) b = a
  end function count_positive

  subroutine swap_rows(a, i, j)
    real, intent(inout) :: a(:, :)
    integer, intent(in) :: i, j
    real, allocatable :: tmp(:)
    tmp = a(i, :)
    a(i, :) = a(j, :)
    a(j, :) = tmp
    a(:, 1) = 2.0 * a(:, 1)
    a(2:3, 1) = a(1:2, 2)
    a = transpose(a)
  end subroutine swap_rows

  function shape_ratio(a, b) result(r)
    class(shape_type), intent(in) :: a
    class(shape_type), intent(in), optional :: b
    real :: r
    r = a%scaled_area(factor=2.0)
    if (present(b)) r = r / b%area()
  end function shape_ratio

  subroutine use_legacy(obj, v, ierr)
    type(legacy_type), intent(inout) :: obj
    real, intent(inout) :: v(:)
    integer, intent(out) :: ierr
    PROJECT_CHK_IERR(call obj%update(v, ierr), ierr)
    PROJECT_CHK_IERR(call legacy_helper(obj, v(1), ierr), ierr)
    v(1) = obj%get_value()
  end subroutine use_legacy

  function first_index(v, lo) result(k)
    integer, intent(in) :: lo
    real, intent(in) :: v(lo:)
    integer :: k
    integer :: idx(0:3)
    idx = 0
    idx(0) = lo
    k = idx(0) + int(v(lo)) + nint(v(lo + 1))
  end function first_index

  subroutine stats_add(this, x, ierr)
    class(stats_type), intent(inout) :: this
    real, intent(in) :: x
    integer, intent(out) :: ierr
    ierr = 0
    if (.not. allocated(this%samples)) then
      PROJECT_ALLOCATE(this%samples(max_items), ierr)
    end if
    PROJECT_ASSERT(this%n < max_items, ierr)
    this%n = this%n + 1
    this%samples(this%n) = x
    this%total = this%total + clamp(x, -1.0, 1.0)
    if (allocated(this%owner)) this%total = this%total + this%owner%area()
  end subroutine stats_add

  function stats_mean(this) result(m)
    class(stats_type), intent(in) :: this
    real :: m
    integer :: ierr
    m = mean(this%samples(1:this%n), ierr=ierr)
  end function stats_mean

  function stats_version() result(v)
    integer :: v
    v = 2
  end function stats_version

  subroutine stats_cleanup(this)
    type(stats_type), intent(inout) :: this
    if (allocated(this%samples)) deallocate(this%samples)
  end subroutine stats_cleanup

end module features_mod
