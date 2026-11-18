module b_mod
  use a_mod, only: A_type
  implicit none
  type, extends(A_type) :: B_type
    private
    real :: scale = 1.0
    integer, public :: someInteger = 0
    type(A_type), allocatable, public :: listOfOther(:)
  contains
    procedure :: write_state => b_write_state
    procedure :: rescale => b_rescale
    procedure, nopass :: version => b_version
  end type B_type
  integer, public :: b_count = 0
contains
  subroutine b_write_state(this, ierr)
    class(B_type), intent(in) :: this
    integer, intent(out) :: ierr
    ierr = 0
  end subroutine b_write_state
  subroutine b_rescale(this, f)
    class(B_type), intent(inout) :: this
    real, intent(in) :: f
    this%scale = f * this%scale
  end subroutine b_rescale
  integer function b_version()
    b_version = 1
  end function b_version
end module b_mod
