!> Translated module that uses the wrapped (Fortran) class.
module solver_user_mod
  use mysolver_mod, only: MySolver_type
  implicit none
  private
  public :: run_solver
contains
  function run_solver(s, x) result(r)
    type(MySolver_type), intent(inout) :: s
    real, intent(in) :: x(:)
    real :: r
    real :: y(size(x)), b(size(x), 2), mn, mx
    integer :: ierr, i
    logical :: old

    call s%scale(2.0, ierr)
    call s%apply(x, y, ierr)
    call s%apply_block(s%coupling, b, ierr)
    call s%stats(mn, mx, ierr=ierr)
    r = sum(y) + s%norm(x) + s%n + s%diag(1) + sum(b) + mn + mx
    call s%stats(mn, mx, 0.5, ierr)
    r = r + mx
    call s%set_label('wrapped', ierr)
    r = r + s%label_length()
    s%shift = 0.25
    call s%set_verbose(.true., old)
    if (s%is_verbose() .and. .not. old) r = r + 100.0
    do i = 1, s%n
      r = r + s%coupling(i, 2) * i
    end do
  end function run_solver
end module solver_user_mod
