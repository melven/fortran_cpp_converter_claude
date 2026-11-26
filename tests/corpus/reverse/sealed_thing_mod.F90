!> abstract type implemented in C++ only (interop = "cpp") with a non_overridable binding
module sealed_thing_mod
  implicit none
  private
  public :: SealedThing_type

  type, abstract :: SealedThing_type
  contains
    procedure(value_iface), deferred :: value
    procedure, non_overridable :: twice           !< Fortran implementation (calls the C++ value)
  end type SealedThing_type

  abstract interface
    function value_iface(this, x) result(y)
      import :: SealedThing_type
      class(SealedThing_type), intent(in) :: this
      real, intent(in) :: x
      real :: y
    end function value_iface
  end interface

contains

  function twice(this, x) result(y)
    class(SealedThing_type), intent(in) :: this
    real, intent(in) :: x
    real :: y
    y = 2.0 * this%value(x)
  end function twice

end module sealed_thing_mod
