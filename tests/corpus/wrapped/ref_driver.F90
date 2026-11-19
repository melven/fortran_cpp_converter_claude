program ref_driver
  use mysolver_mod
  use solver_user_mod
  implicit none
  type(MySolver_type) :: s
  integer :: ierr
  real :: x(3), y(3)
  logical :: pos(3)
  call s%init(3, ierr)
  x = [1.0, -2.0, 0.5]
  print '(es24.15)', run_solver(s, x)
  call s%apply(x, y, ierr)
  print '(3es24.15)', y
  print '(es24.15)', s%shift
  print '(3es24.15)', real(s%n), s%diag(3), s%weights(3)
  print '(3es24.15)', real(s%params%maxit), s%params%tol, s%params%w(2)
  print '(es24.15)', s%weighted(x)
  print '(4es24.15)', s%weighted(x, y, [1.0, 0.5, 2.0]), y
  print '(es24.15)', s%weighted(x)
  call s%positive(x, pos)
  print '(3es24.15)', s%masked_sum(x), s%masked_sum(x, pos), merge(1.0, 0.0, pos(3))
end program ref_driver
