!> a wrapped class whose state (class(*)) travels through C++ as a black box
module fstate_mod
  implicit none
  type :: Counter_type
    integer :: n = 0
  contains
    procedure :: get_state => counter_get_state
    procedure :: set_state => counter_set_state
  end type Counter_type
contains
  subroutine counter_get_state(this, state, ierr)
    class(Counter_type), intent(in) :: this
    class(*), allocatable, intent(out) :: state
    integer, intent(out) :: ierr
    ierr = 0
    allocate(state, source=this%n)
  end subroutine counter_get_state

  subroutine counter_set_state(this, state, ierr)
    class(Counter_type), intent(inout) :: this
    class(*), allocatable, intent(in) :: state
    integer, intent(out) :: ierr
    ierr = -1
    if (.not. allocated(state)) return
    select type (state)
    type is (integer)
      this%n = state
      ierr = 0
    end select
  end subroutine counter_set_state
end module fstate_mod
