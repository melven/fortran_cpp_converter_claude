!> a wrapped class whose state (class(*)) travels through C++ as a black box
module fstate_mod
  implicit none
  type :: Counter_type
    integer :: n = 0
  contains
    procedure :: get_state => counter_get_state
    procedure :: set_state => counter_set_state
    procedure :: empty_state => counter_empty_state
    procedure :: real_state => counter_real_state
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

  !> no state (unallocated)
  subroutine counter_empty_state(this, state, ierr)
    class(Counter_type), intent(in) :: this
    class(*), allocatable, intent(out) :: state
    integer, intent(out) :: ierr
    ierr = 0
    if (this%n < 0) allocate(state, source=this%n)
  end subroutine counter_empty_state

  !> a state of another kind (rejected by set_state)
  subroutine counter_real_state(this, state, ierr)
    class(Counter_type), intent(in) :: this
    class(*), allocatable, intent(out) :: state
    integer, intent(out) :: ierr
    ierr = 0
    allocate(state, source=real(this%n))
  end subroutine counter_real_state
end module fstate_mod
