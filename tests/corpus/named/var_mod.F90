module var_mod
  implicit none
  type :: Variable_type
    character(len=:), allocatable :: name
    character(len=:), allocatable :: comment
    integer :: rank = 0
    integer, allocatable :: dim(:)
    character(len=16), allocatable :: dimComment(:)
  end type Variable_type
  type :: Model_type
    integer :: n = 0
  contains
    procedure :: assignVariables => model_assign
  end type Model_type
contains
  subroutine model_assign(this, params, inputs, outputs, ierr)
    class(Model_type), intent(inout) :: this
    type(Variable_type), intent(in) :: params(:), inputs(:), outputs(:)
    integer, intent(out) :: ierr
    ierr = 0
    this%n = size(params) + size(inputs) + size(outputs)
  end subroutine model_assign
end module var_mod
