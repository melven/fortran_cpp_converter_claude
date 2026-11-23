!> a Fortran implementation (stays Fortran), used in C++ through AbstractThing_private::CWrapperThing
module const_thing_mod
  use thing_mod, only: AbstractThing_type
  implicit none
  private
  public :: ConstThing_type

  type, extends(AbstractThing_type) :: ConstThing_type
    real :: c = 0.5
  contains
    procedure :: evaluate => const_evaluate
    procedure :: weight => const_weight
  end type ConstThing_type
contains
  subroutine const_evaluate(this, x, y, ierr)
    class(ConstThing_type), intent(inout) :: this
    real, intent(in) :: x(:)
    real, intent(out) :: y(:)
    integer, intent(out) :: ierr
    ierr = 0
    if (size(y) /= size(x)) then
      ierr = 1
      return
    end if
    y = x + this%c
  end subroutine const_evaluate

  function const_weight(this) result(w)
    class(ConstThing_type), intent(in) :: this
    real :: w
    w = 10.0 * this%c
  end function const_weight
end module const_thing_mod
