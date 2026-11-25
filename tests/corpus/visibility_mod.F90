!> Private type-bound procedures: C++ visibility by their callers.
module visibility_mod
  implicit none
  private

  public :: counter_type, scaled_counter_type, reset_all

  type :: counter_type
    integer :: total = 0
  contains
    procedure :: add
    procedure, private :: check          !< own methods only: private
    procedure, private :: bump           !< also derived types: protected
    procedure, private :: clear          !< also a module procedure: public
  end type counter_type

  type, extends(counter_type) :: scaled_counter_type
    integer :: factor = 2
  contains
    private                              ! default for the following bindings
    procedure :: scaled_add              !< own methods only: private
    procedure, public :: add_scaled
  end type scaled_counter_type

contains

  !================================================================================
  !> adds n if it is valid (separator lines of the project style: not in C++)
  subroutine add(this, n)
    class(counter_type), intent(inout) :: this
    integer, intent(in) :: n
    !----------------------------------------------------------------------

    if (this%check(n)) call this%bump(n)
  end subroutine add

  logical function check(this, n)
    class(counter_type), intent(in) :: this
    integer, intent(in) :: n
    check = n > 0 .and. this%total >= 0
  end function check

  subroutine bump(this, n)
    class(counter_type), intent(inout) :: this
    integer, intent(in) :: n
    this%total = this%total + n
  end subroutine bump

  subroutine clear(this)
    class(counter_type), intent(inout) :: this
    this%total = 0
  end subroutine clear

  subroutine scaled_add(this, n)
    class(scaled_counter_type), intent(inout) :: this
    integer, intent(in) :: n
    call this%bump(this%factor * n)
  end subroutine scaled_add

  subroutine add_scaled(this, n)
    class(scaled_counter_type), intent(inout) :: this
    integer, intent(in) :: n
    call this%scaled_add(n)
  end subroutine add_scaled

  subroutine reset_all(counters)
    type(counter_type), intent(inout) :: counters(:)
    integer :: i
    do i = 1, size(counters)
      call counters(i)%clear()
    end do
  end subroutine reset_all

end module visibility_mod
