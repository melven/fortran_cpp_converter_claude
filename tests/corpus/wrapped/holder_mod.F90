!> wrapped class with a constant of a type of another module
module holder_mod
  use offsets_mod, only: Offsets_type
  implicit none
  type :: Holder_type
    type(Offsets_type) :: offsets      !< position of the block
    real :: x = 0.0
  contains
    procedure :: get_x => holder_get_x
  end type Holder_type
contains
  real function holder_get_x(this)
    class(Holder_type), intent(in) :: this
    holder_get_x = this%x
  end function holder_get_x
end module holder_mod
