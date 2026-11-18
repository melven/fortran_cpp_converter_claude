program ref_driver
  use mysolver_mod
  use solver_user_mod
  implicit none
  type(MySolver_type) :: s
  integer :: ierr
  real :: x(3), y(3)
  call s%init(3, ierr)
  x = [1.0, -2.0, 0.5]
  print '(es24.15)', run_solver(s, x)
  call s%apply(x, y, ierr)
  print '(3es24.15)', y
  print '(es24.15)', s%shift
end program ref_driver
