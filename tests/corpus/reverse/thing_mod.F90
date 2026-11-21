!> things evaluated by the Fortran dependents, implemented in C++ (wrap_reverse)
module thing_mod
  implicit none
  private
  public :: AbstractThing_type

  !> something evaluated
  type, abstract :: AbstractThing_type
  contains
    procedure(evaluate_iface), deferred :: evaluate
    procedure(weight_iface), deferred :: weight
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
end module thing_mod
