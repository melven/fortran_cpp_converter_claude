module c_mod
  use b_mod, only: B_type
  implicit none
  type, extends(B_type) :: C_type
    integer :: k = 0
  contains
    procedure :: write_state => c_write_state
    procedure :: bump => c_bump
  end type C_type
contains
  subroutine c_write_state(this, ierr)
    class(C_type), intent(in) :: this
    integer, intent(out) :: ierr
    ierr = 0
  end subroutine c_write_state
  subroutine c_bump(this)
    class(C_type), intent(inout) :: this
    this%k = this%k + 1
  end subroutine c_bump
end module c_mod
