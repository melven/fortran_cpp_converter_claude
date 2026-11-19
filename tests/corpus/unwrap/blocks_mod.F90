!> blocks with offsets (unwrapped multi-component type)
module blocks_mod
  implicit none
  private
  public :: Op_type, Scale_type, Block_type, Blocks_type

  type, abstract :: Op_type
  contains
    procedure(apply_iface), deferred :: apply
  end type Op_type

  abstract interface
    real function apply_iface(this, x)
      import :: Op_type
      class(Op_type), intent(in) :: this
      real, intent(in) :: x
    end function apply_iface
  end interface

  type, extends(Op_type) :: Scale_type
    real :: f = 2.0
  contains
    procedure :: apply => scale_apply
  end type Scale_type

  !> one block: operator and its position
  type :: Block_type
    class(Op_type), allocatable :: p   !< the operator
    integer :: offset1 = 0             !< first row
    !> first column
    integer :: offset2 = 0
  end type Block_type

  type :: Blocks_type
    type(Block_type), allocatable :: arr(:)
  contains
    procedure :: init => blocks_init
    procedure :: total => blocks_total
  end type Blocks_type

contains

  real function scale_apply(this, x)
    class(Scale_type), intent(in) :: this
    real, intent(in) :: x
    scale_apply = this%f * x
  end function scale_apply

  subroutine blocks_init(this, n)
    class(Blocks_type), intent(inout) :: this
    integer, intent(in) :: n
    integer :: i
    allocate(this%arr(n))
    do i = 1, n
      allocate(Scale_type :: this%arr(i)%p)
      this%arr(i)%offset1 = 10 * i
      this%arr(i)%offset2 = this%arr(i)%offset1 + 1
    end do
  end subroutine blocks_init

  real function blocks_total(this)
    class(Blocks_type), intent(in) :: this
    integer :: i
    blocks_total = 0.0
    do i = 1, size(this%arr)
      blocks_total = blocks_total + this%arr(i)%p%apply(real(this%arr(i)%offset2))
    end do
  end function blocks_total
end module blocks_mod
