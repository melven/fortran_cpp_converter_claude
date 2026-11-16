#include "project_macros.h"
!> Small polymorphic hierarchy to exercise OOP translation.
module shape_mod
  implicit none
  private

  public :: shape_type, circle_type, rect_type, total_area, describe, make_shape

  real, parameter :: pi = 3.141592653589793

  type, abstract :: shape_type
    character(len=32) :: name = 'shape'
  contains
    procedure(area_iface), deferred :: area
    procedure :: scale => shape_scale
    procedure :: scaled_area
  end type shape_type

  abstract interface
    function area_iface(this) result(a)
      import :: shape_type
      class(shape_type), intent(in) :: this
      real :: a
    end function area_iface
  end interface

  type, extends(shape_type) :: circle_type
    real :: radius = 1.0
  contains
    procedure :: area => circle_area
    procedure :: scale => circle_scale
  end type circle_type

  type, extends(shape_type) :: rect_type
    real :: width = 1.0
    real :: height = 1.0
  contains
    procedure :: area => rect_area
    procedure :: scale => rect_scale
  end type rect_type

contains

  subroutine shape_scale(this, factor, ierr)
    class(shape_type), intent(inout) :: this
    real, intent(in) :: factor
    integer, intent(out) :: ierr
    ierr = 0
    PROJECT_CHK_ARGUMENT(factor > 0.0, ierr)
  end subroutine shape_scale

  function scaled_area(this, factor) result(a)
    class(shape_type), intent(in) :: this
    real, intent(in), optional :: factor
    real :: a
    a = this%area()
    if (present(factor)) then
      a = a * factor**2
    end if
  end function scaled_area

  function circle_area(this) result(a)
    class(circle_type), intent(in) :: this
    real :: a
    a = pi * this%radius**2
  end function circle_area

  subroutine circle_scale(this, factor, ierr)
    class(circle_type), intent(inout) :: this
    real, intent(in) :: factor
    integer, intent(out) :: ierr
    PROJECT_CHK_IERR(call shape_scale(this, factor, ierr), ierr)
    this%radius = this%radius * factor
  end subroutine circle_scale

  function rect_area(this) result(a)
    class(rect_type), intent(in) :: this
    real :: a
    a = this%width * this%height
  end function rect_area

  subroutine rect_scale(this, factor, ierr)
    class(rect_type), intent(inout) :: this
    real, intent(in) :: factor
    integer, intent(out) :: ierr
    PROJECT_CHK_IERR(call shape_scale(this, factor, ierr), ierr)
    this%width = this%width * factor
    this%height = this%height * factor
  end subroutine rect_scale

  !> factory: kind = 1 circle, kind = 2 rectangle
  subroutine make_shape(kind, s, ierr)
    integer, intent(in) :: kind
    class(shape_type), allocatable, intent(out) :: s
    integer, intent(out) :: ierr

    ierr = 0
    select case (kind)
    case (1)
      allocate(circle_type :: s)
      s%name = 'circle'
    case (2)
      allocate(rect_type :: s)
      s%name = 'rect'
    case default
      PROJECT_CHK_ARGUMENT(.false., ierr)
    end select
  end subroutine make_shape

  function total_area(a, b) result(total)
    class(shape_type), intent(in) :: a, b
    real :: total
    total = a%area() + b%area()
  end function total_area

  !> 1: circle, 2: rectangle, 0: unknown
  function describe(s) result(id)
    class(shape_type), intent(in) :: s
    integer :: id
    select type (s)
    type is (circle_type)
      id = 1
      if (s%radius < 0.0) id = -1
    class is (rect_type)
      id = 2
    class default
      id = 0
    end select
  end function describe

end module shape_mod
