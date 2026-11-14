!> Stands for a module that is NOT translated because equivalent C++ code already exists
!> (see tests/support/legacy.hpp); it is only parsed for its symbols (-I).
module legacy_mod
  implicit none
  private
  public :: legacy_type, legacy_helper, str

  type :: legacy_type
    real :: value = 0.0
  contains
    procedure :: update => legacy_update
    procedure :: get_value => legacy_get_value
  end type legacy_type

contains

  function str(i) result(s)
    integer, intent(in) :: i
    character(len=12) :: s
    write(s, '(i0)') i
  end function str

  subroutine legacy_update(this, v, ierr)
    class(legacy_type), intent(inout) :: this
    real, intent(in) :: v(:)
    integer, intent(out) :: ierr
    ierr = 0
    this%value = sum(v)
  end subroutine legacy_update

  function legacy_get_value(this) result(v)
    class(legacy_type), intent(in) :: this
    real :: v
    v = this%value
  end function legacy_get_value

  subroutine legacy_helper(obj, x, ierr)
    type(legacy_type), intent(inout) :: obj
    real, intent(in) :: x
    integer, intent(out) :: ierr
    ierr = 0
    obj%value = obj%value + x
  end subroutine legacy_helper

end module legacy_mod
