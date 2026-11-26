! dead_mod: assignments whose value is never read
module dead_mod
  implicit none
  type :: pair_type
    real :: x = 0.0, y = 0.0
  contains
    procedure :: norm1
  end type
contains
  !> sum of the absolute components
  function norm1(this) result(r)
    class(pair_type), intent(in) :: this    !< pair
    real :: r
    r = abs(this%x) + abs(this%y)
  end function norm1

  !> block sums; some assignments are never read
  subroutine block_sums(offs, x, p, q, s)
    integer, intent(in) :: offs(:)          !< block offsets (size nb + 1)
    real, intent(in) :: x(:)                !< values
    type(pair_type), intent(in) :: p, q     !< pairs
    real, intent(out) :: s                  !< result
    integer :: i, j, rb, re, unused
    real :: t, u, w
    type(pair_type) :: pq
    s = 0.0
    unused = size(x) + 1                    ! never read, no side effects: removed
    do i = 1, size(offs) - 1
      rb = offs(i)
      re = offs(i + 1) - 1                  ! not read in this loop: removed
      do j = rb, rb + 1
        s = s + x(j)
      end do
    end do
    do i = 1, size(offs) - 1
      rb = offs(i)
      re = offs(i + 1) - 1                  ! read here: kept
      s = s + sum(x(rb:re))
    end do
    t = p%norm1()                           ! type-bound function: kept (review note)
    pq = p
    pq%y = q%y
    u = pq%x * 2.0                          ! no overloaded operator applies to reals: removed
    w = abs(p%x) + q%y                      ! components of intrinsic type: removed
    s = s + pq%y
  end subroutine block_sums
end module dead_mod
