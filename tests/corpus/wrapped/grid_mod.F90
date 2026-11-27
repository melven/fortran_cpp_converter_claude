! grid_mod: integer arrays in the interface (std::vector<int>; -W accessors: Eigen::Map)
module grid_mod
  implicit none
  private
  public :: grid_type, count_pos, first_n
  type :: grid_type
    integer, allocatable :: idx(:)
    integer :: n = 0
  contains
    procedure :: setup
    procedure :: get
  end type
contains
  subroutine setup(this, idx, ierr)
    class(grid_type), intent(inout) :: this
    integer, intent(in) :: idx(:)
    integer, intent(out) :: ierr
    ierr = 0
    this%idx = idx
    this%n = size(idx)
  end subroutine
  function get(this, k) result(v)
    class(grid_type), intent(in) :: this
    integer, intent(in) :: k
    integer :: v
    v = this%idx(k)
  end function
  function count_pos(a) result(c)
    integer, intent(in) :: a(:)
    integer :: c, i
    c = 0
    do i = 1, size(a)
      if (a(i) > 0) c = c + 1
    end do
  end function
  function first_n(n) result(a)
    integer, intent(in) :: n
    integer :: a(n), i
    do i = 1, n
      a(i) = i
    end do
  end function
end module
