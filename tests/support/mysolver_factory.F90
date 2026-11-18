!> Stand-in for the Fortran code that creates and initialises the objects used from C++ (test support).
module mysolver_factory
  use, intrinsic :: iso_c_binding
  use mysolver_mod
  use mysolver_mod_c_binding, only: MySolver_c
  implicit none
  type(MySolver_type), target, save :: solver
  type(MySolver_c), target, save :: solver_c
contains
  function project_MySolver_create(n) result(h) bind(C, name="project_MySolver_create")
    integer(C_INT), value :: n
    type(C_PTR) :: h
    integer :: ierr
    call solver%init(n, ierr)
    solver_c%p_impl = c_loc(solver)
    h = c_loc(solver_c)
  end function project_MySolver_create
end module mysolver_factory
