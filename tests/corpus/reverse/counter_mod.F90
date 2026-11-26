!> wrapped counter, stored by a C++ implementation (reverse/sealed_thing_mod: stored argument)
module counter_mod
  implicit none
  private
  public :: Counter_type

  type :: Counter_type
    integer :: count = 0
  contains
    procedure :: add
  end type Counter_type

contains

  subroutine add(this, n)
    class(Counter_type), intent(inout) :: this
    integer, intent(in) :: n
    this%count = this%count + n
  end subroutine add

end module counter_mod
