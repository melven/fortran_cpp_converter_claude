module b_mod
  use a_mod, only: A_type
  implicit none
  type, extends(A_type) :: B_type
    real :: scale = 1.0
  contains
    procedure :: write_state => b_write_state
    procedure :: rescale => b_rescale
  end type B_type
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
end module b_mod
