!> @file body_mod.F90
!> @brief Abstract bodies (wrapped class hierarchy, root)
!
! Some licence text.
#include "project_macros.h"
module body_mod
  implicit none
  private

  public :: body_type

  type, abstract :: body_type
    real :: mass = 1.0
  contains
    procedure(volume_iface), deferred :: volume
    procedure :: density => body_density
    procedure :: rescale => body_rescale
  end type body_type

  abstract interface
    function volume_iface(this) result(v)
      import :: body_type
      class(body_type), intent(in) :: this
      real :: v
    end function volume_iface
  end interface

contains

  function body_density(this) result(d)
    class(body_type), intent(in) :: this
    real :: d
    d = this%mass / this%volume()
  end function body_density

  subroutine body_rescale(this, factor, ierr)
    class(body_type), intent(inout) :: this
    real, intent(in) :: factor
    integer, intent(out) :: ierr
    ierr = 0
    PROJECT_CHK_ARGUMENT(factor > 0.0, ierr)
    this%mass = factor * this%mass
  end subroutine body_rescale

end module body_mod
