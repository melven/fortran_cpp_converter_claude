program driver
  use expm_mod
  use block_lu_mod
  use shape_mod
  use poly_mod
  implicit none
  real :: A(3,3), B(3,2), E(3,3), M(3,2), x(6), blk(2,2)
  type(block_lu_type) :: lu
  class(shape_type), allocatable :: s1, s2
  integer :: ierr, i
  type(poly_type) :: pol
  real :: cf(0:3), dd(0:3), tab(3, 0:1), hst(2, 2, 0:1)
  A = reshape([0.1, -0.3, 0.7, 1.2, 0.4, -0.5, 2.0, 0.3, -1.1], [3,3])
  B = reshape([1.0, 0.0, 2.0, -1.0, 0.5, 0.25], [3,2])
  call expm(A, 1.7, E, ierr)
  print '(9es24.15)', E
  call expm_moment(A, B, 0.9, M, ierr)
  print '(6es24.15)', M
  call lu%init(3, 2, ierr)
  do i = 1, 3
    blk = reshape([4.0 + i, 1.0, -1.0, 3.0], [2,2])
    call lu%set_block(i, i, blk, ierr)
    if (i < 3) then
      blk = reshape([0.5, 0.1 * i, 0.2, -0.3], [2,2])
      call lu%set_block(i, i + 1, blk, ierr)
      blk = reshape([-0.4, 0.3, 0.1 * i, 0.6], [2,2])
      call lu%set_block(i + 1, i, blk, ierr)
    end if
  end do
  call lu%factorize(ierr)
  x = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
  call lu%solve(x, ierr)
  print '(6es24.15)', x
  call lu%finalize(ierr)
  call make_shape(1, s1, ierr)
  call make_shape(2, s2, ierr)
  call s1%scale(2.0, ierr)
  call s2%scale(3.0, ierr)
  print '(3es24.15)', total_area(s1, s2), s1%scaled_area(2.0), s2%scaled_area()
  print '(2i4)', describe(s1), describe(s2)
  cf = [1.0, -2.0, 0.5, 0.25]
  call pol%set(cf, ierr)
  call poly_derivative(cf, dd)
  call moments_table(cf, [0.5, 1.5, -1.0], tab, hst)
  print '(3es24.15)', pol%eval(1.3), poly_eval(dd, 0.7), poly_eval(cf(1:), 2.0)
  print '(6es24.15)', tab
  print '(8es24.15)', hst
end program driver
