!> Stand-in for the Fortran code that creates and initialises the objects used from C++ (test support).
module mysolver_factory
  use, intrinsic :: iso_c_binding
  use mysolver_mod
  implicit none
  type(MySolver_type), target, save :: solver
contains
  function project_MySolver_create(n) result(h) bind(C, name="project_MySolver_create")
    integer(C_INT), value :: n
    type(C_PTR) :: h
    integer :: ierr
    call solver%init(n, ierr)
    h = c_loc(solver)
  end function project_MySolver_create
end module mysolver_factory
