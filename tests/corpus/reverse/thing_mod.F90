!> @file thing_mod.F90
!> @brief things evaluated by the Fortran dependents, implemented in C++ (wrap_reverse)
!
! Some licence text.
module thing_mod
  implicit none
  private
  public :: AbstractThing_type

  !> something evaluated
  type, abstract :: AbstractThing_type
  contains
    procedure(evaluate_iface), deferred :: evaluate
    procedure(weight_iface), deferred :: weight
    procedure :: reset => thing_reset          ! default: nothing to do
    procedure :: offset => thing_offset        ! default: 0, overridden in C++
  end type AbstractThing_type

  abstract interface
    !> y = f(x)
    subroutine evaluate_iface(this, x, y, ierr)
      import :: AbstractThing_type
      class(AbstractThing_type), intent(inout) :: this
      real, intent(in) :: x(:)                    !< input
      real, intent(out) :: y(:)                   !< result (size of x)
      integer, intent(out) :: ierr
    end subroutine evaluate_iface
    !> a weight
    function weight_iface(this) result(w)
      import :: AbstractThing_type
      class(AbstractThing_type), intent(in) :: this
      real :: w
    end function weight_iface
  end interface
contains

  !> resets the state (default: nothing)
  subroutine thing_reset(this)
    class(AbstractThing_type), intent(inout) :: this
  end subroutine thing_reset

  !> an offset (default: 0)
  function thing_offset(this) result(o)
    class(AbstractThing_type), intent(in) :: this
    real :: o
    o = 0.0
  end function thing_offset
end module thing_mod
