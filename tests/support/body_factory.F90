!> Stand-in for the Fortran code that creates objects of a wrapped class hierarchy (test support).
module body_factory
  use, intrinsic :: iso_c_binding
  use sphere_mod, only: sphere_type
  use body_mod_c_binding, only: project_body_setup, project_body_destroy
  implicit none
  type(sphere_type), target, save :: the_sphere
contains
  function project_body_create_sphere(r, m) result(h) bind(C, name="project_body_create_sphere")
    real(C_DOUBLE), value :: r, m
    type(C_PTR) :: h
    the_sphere%radius = r
    the_sphere%mass = m
    h = project_body_setup(the_sphere)       ! released by project_body_release
  end function project_body_create_sphere

  subroutine project_body_release(h) bind(C, name="project_body_release")
    type(C_PTR), value :: h
    type(C_PTR) :: hh
    hh = h
    call project_body_destroy(hh)
  end subroutine project_body_release
end module body_factory
