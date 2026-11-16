#include "project_macros.h"
!> LU decomposition of a block-tridiagonal matrix with dense blocks.
module block_lu_mod
  implicit none
  private

  public :: block_lu_type

  type :: block_lu_type
    private
    integer :: nblocks = 0
    integer :: bs = 0          !< block size
    logical :: factorized = .false.
    real, allocatable :: diag(:, :, :)    !< diagonal blocks (bs, bs, nblocks)
    real, allocatable :: lower(:, :, :)   !< sub-diagonal blocks, block i couples i+1 -> i
    real, allocatable :: upper(:, :, :)   !< super-diagonal blocks
    integer, allocatable :: ipiv(:, :)
  contains
    procedure :: init => block_lu_init
    procedure :: set_block => block_lu_set_block
    procedure :: factorize => block_lu_factorize
    procedure :: solve => block_lu_solve
    procedure :: get_size => block_lu_get_size
    procedure :: finalize => block_lu_finalize
  end type block_lu_type

contains

  subroutine block_lu_init(this, nblocks, bs, ierr)
    class(block_lu_type), intent(out) :: this
    integer, intent(in) :: nblocks, bs
    integer, intent(out) :: ierr

    ierr = 0
    PROJECT_CHK_ARGUMENT(nblocks > 0 .and. bs > 0, ierr)
    this%nblocks = nblocks
    this%bs = bs
    PROJECT_ALLOCATE(this%diag(bs, bs, nblocks), ierr)
    PROJECT_ALLOCATE(this%lower(bs, bs, nblocks - 1), ierr)
    PROJECT_ALLOCATE(this%upper(bs, bs, nblocks - 1), ierr)
    PROJECT_ALLOCATE(this%ipiv(bs, nblocks), ierr)
    this%diag = 0.0
    this%lower = 0.0
    this%upper = 0.0
    this%factorized = .false.
  end subroutine block_lu_init

  subroutine block_lu_finalize(this, ierr)
    class(block_lu_type), intent(inout) :: this
    integer, intent(out) :: ierr

    ierr = 0
    if (allocated(this%diag)) deallocate(this%diag, this%lower, this%upper, this%ipiv)
    this%nblocks = 0
  end subroutine block_lu_finalize

  pure function block_lu_get_size(this) result(n)
    class(block_lu_type), intent(in) :: this
    integer :: n
    n = this%nblocks * this%bs
  end function block_lu_get_size

  !> set block (i, j) with |i - j| <= 1
  subroutine block_lu_set_block(this, i, j, blk, ierr)
    class(block_lu_type), intent(inout) :: this
    integer, intent(in) :: i, j      !< block row and column
    real, intent(in) :: blk(:, :)    !< dense block,
                                     !< copied
    integer, intent(out) :: ierr     !< error flag

    ierr = 0
    PROJECT_CHK_ARGUMENT(abs(i - j) <= 1, ierr)
    PROJECT_CHK_ARGUMENT(.not. this%factorized, ierr)
    if (i == j) then
      this%diag(:, :, i) = blk
    else if (i == j + 1) then
      this%lower(:, :, j) = blk
    else
      this%upper(:, :, i) = blk
    end if
  end subroutine block_lu_set_block

  subroutine block_lu_factorize(this, ierr)
    class(block_lu_type), intent(inout) :: this
    integer, intent(out) :: ierr

    integer :: i, info, bs

    ierr = 0
    bs = this%bs
    do i = 1, this%nblocks
      if (i > 1) then
        ! D_i <- D_i - L_{i-1} * (D_{i-1}^{-1} U_{i-1}); upper already holds D^{-1} U
        this%diag(:, :, i) = this%diag(:, :, i) &
          &  - matmul(this%lower(:, :, i - 1), this%upper(:, :, i - 1))
      end if
      call dgetrf(bs, bs, this%diag(:, :, i), bs, this%ipiv(:, i), info)
      PROJECT_ASSERT(info == 0, ierr)
      if (i < this%nblocks) then
        call dgetrs('N', bs, bs, this%diag(:, :, i), bs, this%ipiv(:, i), this%upper(:, :, i), bs, info)
        PROJECT_ASSERT(info == 0, ierr)
      end if
    end do
    this%factorized = .true.
  end subroutine block_lu_factorize

  !> solve in place, x contains the right-hand side on entry
  subroutine block_lu_solve(this, x, ierr)
    class(block_lu_type), intent(in) :: this
    real, intent(inout) :: x(:)
    integer, intent(out) :: ierr

    integer :: i, info, bs, lo, hi

    ierr = 0
    PROJECT_CHK_ARGUMENT(size(x) == this%get_size(), ierr)
    PROJECT_ASSERT(this%factorized, ierr)
    bs = this%bs
    ! forward substitution
    do i = 1, this%nblocks
      lo = (i - 1) * bs + 1
      hi = i * bs
      if (i > 1) then
        x(lo:hi) = x(lo:hi) - matmul(this%lower(:, :, i - 1), x(lo - bs:hi - bs))
      end if
      call dgetrs('N', bs, 1, this%diag(:, :, i), bs, this%ipiv(:, i), x(lo:hi), bs, info)
      PROJECT_ASSERT(info == 0, ierr)
    end do
    ! backward substitution
    do i = this%nblocks - 1, 1, -1
      lo = (i - 1) * bs + 1
      hi = i * bs
      x(lo:hi) = x(lo:hi) - matmul(this%upper(:, :, i), x(lo + bs:hi + bs))
    end do
  end subroutine block_lu_solve

end module block_lu_mod
