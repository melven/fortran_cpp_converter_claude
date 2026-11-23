!> Stand-in for the Fortran code that creates and initialises the objects used from C++ (test support).
module mysolver_factory
  use, intrinsic :: iso_c_binding
  use mysolver_mod
  use mysolver_mod_c_binding, only: project_MySolver_setup, project_MySolver_destroy
  implicit none
  type(MySolver_type), target, save :: solver
contains
  function project_MySolver_create(n) result(h) bind(C, name="project_MySolver_create")
    integer(C_INT), value :: n
    type(C_PTR) :: h
    integer :: ierr
    call solver%init(n, ierr)
    h = project_MySolver_setup(solver)      ! released by project_MySolver_release
  end function project_MySolver_create

  subroutine project_MySolver_release(h) bind(C, name="project_MySolver_release")
    type(C_PTR), value :: h
    type(C_PTR) :: hh
    hh = h
    call project_MySolver_destroy(hh)
  end subroutine project_MySolver_release
end module mysolver_factory
