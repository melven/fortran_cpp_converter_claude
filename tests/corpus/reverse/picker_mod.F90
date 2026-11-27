! picker_mod: integer arrays of a bidirectional type (std::vector<int> in C++)
module picker_mod
  implicit none
  private
  public :: AbstractPicker_type
  !> picks entries
  type, abstract :: AbstractPicker_type
  contains
    procedure(pick_iface), deferred :: pick
    procedure :: first_n
  end type
  abstract interface
    !> picks entries of idx into marks
    subroutine pick_iface(this, idx, marks, ierr)
      import :: AbstractPicker_type
      class(AbstractPicker_type), intent(inout) :: this
      integer, intent(in) :: idx(:)         !< indices
      integer, intent(inout) :: marks(:)    !< marks
      integer, intent(out) :: ierr          !< error flag
    end subroutine
  end interface
contains
  !> first n numbers
  function first_n(this, n) result(a)
    class(AbstractPicker_type), intent(in) :: this
    integer, intent(in) :: n                !< count
    integer :: a(n), i
    do i = 1, n
      a(i) = i
    end do
  end function
end module
