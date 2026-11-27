!> options: Fortran data with a hand-written C struct (config c_struct: setup helper, wrapper with cPtr())
module options_mod
  use, intrinsic :: iso_c_binding, only: C_PTR, C_NULL_PTR, C_INT, C_CHAR, C_NULL_CHAR, c_loc
  implicit none
  private
  public :: Options_type, CWrapperOptions_type, CWrapperOptions_setup

  type :: Options_type
    character(len=:), allocatable :: name
    integer :: level = 0
  end type Options_type

  !> C struct project_Options
  type, bind(C) :: Options_c
    type(C_PTR) :: name = C_NULL_PTR
    integer(C_INT) :: level = 0
  end type Options_c

  !> owns the C struct of an Options_type object
  type :: CWrapperOptions_type
    type(Options_c), pointer :: c => null()
    character(kind=C_CHAR), allocatable :: name(:)
  contains
    procedure :: cPtr
    final :: cwrapperoptions_final
  end type CWrapperOptions_type

contains

  subroutine CWrapperOptions_setup(this, options, ierr)
    type(CWrapperOptions_type), target, intent(out) :: this
    type(Options_type), intent(in) :: options
    integer, intent(out) :: ierr
    integer :: i, n
    ierr = 0
    n = 0
    if (allocated(options%name)) n = len(options%name)
    allocate(this%name(n + 1))
    do i = 1, n
      this%name(i) = options%name(i:i)
    end do
    this%name(n + 1) = C_NULL_CHAR
    allocate(this%c)
    this%c%name = c_loc(this%name)
    this%c%level = int(options%level, C_INT)
  end subroutine CWrapperOptions_setup

  function cPtr(this) result(p)
    class(CWrapperOptions_type), intent(in) :: this
    type(C_PTR) :: p
    p = c_loc(this%c)
  end function cPtr

  subroutine cwrapperoptions_final(this)
    type(CWrapperOptions_type), intent(inout) :: this
    if (associated(this%c)) deallocate(this%c)
  end subroutine cwrapperoptions_final
end module options_mod
