!> Stand-in for the Fortran code that creates objects of a wrapped class hierarchy (test support).
module body_factory
  use, intrinsic :: iso_c_binding
  use sphere_mod, only: sphere_type
  use body_mod_c_binding, only: bodyWrapper_type, body_c
  implicit none
  type(sphere_type), target, save :: the_sphere
  type(bodyWrapper_type), target, save :: wrapper
  type(body_c), target, save :: wrapper_c
contains
  function project_body_create_sphere(r, m) result(h) bind(C, name="project_body_create_sphere")
    real(C_DOUBLE), value :: r, m
    type(C_PTR) :: h
    the_sphere%radius = r
    the_sphere%mass = m
    wrapper%ptr => the_sphere
    wrapper_c%p_impl = c_loc(wrapper)
    h = c_loc(wrapper_c)
  end function project_body_create_sphere
end module body_factory
