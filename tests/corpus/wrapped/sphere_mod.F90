!> @file sphere_mod.F90
!> @brief Spheres (wrapped class derived from a wrapped class)
#include "project_macros.h"
module sphere_mod
  use body_mod, only: body_type
  implicit none
  private

  public :: sphere_type

  real, parameter :: pi = 3.141592653589793

  type, extends(body_type) :: sphere_type
    real :: radius = 1.0
  contains
    procedure :: volume => sphere_volume
    procedure :: surface => sphere_surface
    procedure :: grow => sphere_grow
  end type sphere_type

contains

  function sphere_volume(this) result(v)
    class(sphere_type), intent(in) :: this
    real :: v
    v = 4.0 / 3.0 * pi * this%radius**3
  end function sphere_volume

  function sphere_surface(this) result(a)
    class(sphere_type), intent(in) :: this
    real :: a
    a = 4.0 * pi * this%radius**2
  end function sphere_surface

  subroutine sphere_grow(this, dr, ierr)
    class(sphere_type), intent(inout) :: this
    real, intent(in) :: dr
    integer, intent(out) :: ierr
    ierr = 0
    PROJECT_CHK_ARGUMENT(this%radius + dr > 0.0, ierr)
    this%radius = this%radius + dr
  end subroutine sphere_grow

end module sphere_mod
