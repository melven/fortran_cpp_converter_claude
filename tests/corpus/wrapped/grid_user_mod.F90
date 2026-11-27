! grid_user_mod: user of grid_mod (the same C++ for grid_mod translated, -I, -W, -w)
module grid_user_mod
  use grid_mod
  implicit none
contains
  function use_grid(n) result(s)
    integer, intent(in) :: n
    integer :: s, ierr, i
    type(grid_type) :: g
    integer :: v(n)
    do i = 1, n
      v(i) = 2 * i
    end do
    call g%setup(v, ierr)
    s = g%get(1) + count_pos(v) + g%idx(2)
    v = first_n(n)
    s = s + v(n)
  end function
end module
