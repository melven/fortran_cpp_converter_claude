!> models with an internal state (class(*)) implemented in C++: the state stays C++ data (std::any)
module state_model_mod
  implicit none
  private
  public :: StateModel_type

  type, abstract :: StateModel_type
  contains
    procedure(get_iface), deferred :: get_internal_state
    procedure(set_iface), deferred :: set_internal_state
    procedure(step_iface), deferred :: step
    procedure(value_iface), deferred :: value
    procedure(has_iface), deferred :: has_state
  end type StateModel_type

  abstract interface
    !> the internal state (only for set_internal_state of the same kind of model; move_alloc only)
    subroutine get_iface(this, state, ierr)
      import :: StateModel_type
      class(StateModel_type), intent(in) :: this
      class(*), allocatable, intent(out) :: state   !< internal state
      integer, intent(out) :: ierr                  !< error flag
    end subroutine get_iface

    !> restores the internal state
    subroutine set_iface(this, state, ierr)
      import :: StateModel_type
      class(StateModel_type), intent(inout) :: this
      class(*), intent(in) :: state                 !< internal state (from get_internal_state)
      integer, intent(out) :: ierr                  !< error flag
    end subroutine set_iface

    !> one step
    subroutine step_iface(this)
      import :: StateModel_type
      class(StateModel_type), intent(inout) :: this
    end subroutine step_iface

    !> whether a state is given (allocatable: may be unallocated)
    subroutine has_iface(this, state, given)
      import :: StateModel_type
      class(StateModel_type), intent(in) :: this
      class(*), allocatable, intent(in) :: state   !< internal state or nothing
      logical, intent(out) :: given                !< state allocated
    end subroutine has_iface

    !> the current value
    function value_iface(this) result(v)
      import :: StateModel_type
      class(StateModel_type), intent(in) :: this
      integer :: v
    end function value_iface
  end interface

end module state_model_mod
