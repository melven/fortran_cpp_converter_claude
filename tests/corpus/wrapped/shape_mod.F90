!> abstract shapes: sizes of array arguments documented (dimension (...))
#include "project_macros.h"
module shape_mod
  implicit none
  private

  public :: shape_type

  type, abstract :: shape_type
    integer :: nSurface = 3   !< number of surfaces
  contains
    procedure(areas_iface), deferred :: areas
    procedure :: normals => shape_normals
  end type shape_type

  abstract interface
    !> areas of the surfaces
    subroutine areas_iface(this, a, ierr)
      import :: shape_type
      class(shape_type), intent(in) :: this
      real, contiguous, target, intent(out) :: a(:)   !< areas, dimension (this\%nSurface)
      integer, intent(out) :: ierr
    end subroutine areas_iface
  end interface

contains

  !> normals of the surfaces (default: none)
  subroutine shape_normals(this, nrm, ierr)
    class(shape_type), intent(in) :: this
    real, intent(out) :: nrm(:, :)   !< normals, dimension (3, this\%nSurface)
    integer, intent(out) :: ierr
    ierr = 0
    nrm = 0.0
  end subroutine shape_normals

end module shape_mod
