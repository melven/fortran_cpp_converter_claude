!> non_overridable bindings: final methods in C++
module sealed_mod
  implicit none
  private
  public :: base_type, mid_type, leaf_type

  type :: base_type
    integer :: n = 1
  contains
    procedure :: step => base_step
    procedure, non_overridable :: count => base_count     !< not virtual (nothing overrides it)
  end type base_type

  type, extends(base_type) :: mid_type
  contains
    procedure, non_overridable :: step => mid_step        !< overrides, then sealed: final
  end type mid_type

  type, extends(mid_type) :: leaf_type
    integer :: extra = 0
  end type leaf_type

contains

  subroutine base_step(this)
    class(base_type), intent(inout) :: this
    this%n = this%n + 1
  end subroutine base_step

  integer function base_count(this)
    class(base_type), intent(in) :: this
    base_count = this%n
  end function base_count

  subroutine mid_step(this)
    class(mid_type), intent(inout) :: this
    this%n = this%n + 2
  end subroutine mid_step

end module sealed_mod
