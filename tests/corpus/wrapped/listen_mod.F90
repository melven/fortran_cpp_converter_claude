!> things with a listener (implemented in Fortran or in C++)
module listen_mod
  implicit none
  private
  public :: Thing_type, AbstractListener_type

  !> notified about state changes of a thing
  type, abstract :: AbstractListener_type
  contains
    procedure(state_changed_iface), deferred :: state_changed
  end type AbstractListener_type

  type :: Thing_type
    real :: value = 0.0
    class(AbstractListener_type), pointer :: listener => null()
  contains
    procedure :: set_listener => thing_set_listener
    procedure :: set_value => thing_set_value
  end type Thing_type

  abstract interface
    !> the state of thing changed
    subroutine state_changed_iface(this, thing, old, history)
      import :: AbstractListener_type, Thing_type
      class(AbstractListener_type), intent(inout) :: this
      class(Thing_type), intent(in) :: thing      !< the changed thing
      real, intent(in) :: old                     !< previous value
      real, intent(in) :: history(:)              !< old and new value
    end subroutine state_changed_iface
  end interface

contains

  subroutine thing_set_listener(this, listener)
    class(Thing_type), intent(inout) :: this
    class(AbstractListener_type), target, optional, intent(inout) :: listener
    if (present(listener)) then
      this%listener => listener
    else
      this%listener => null()
    end if
  end subroutine thing_set_listener

  subroutine thing_set_value(this, v)
    class(Thing_type), intent(inout) :: this
    real, intent(in) :: v
    real :: old
    old = this%value
    this%value = v
    if (associated(this%listener)) call this%listener%state_changed(this, old, [old, v])
  end subroutine thing_set_value
end module listen_mod
