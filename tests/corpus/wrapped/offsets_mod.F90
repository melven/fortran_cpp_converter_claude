!> offsets of a block (translated; a constant of a wrapped class)
module offsets_mod
  implicit none
  type :: Offsets_type
    integer :: row = 0
    integer :: col = 0
    real :: w(2) = [1.0, 1.0]
  end type Offsets_type
end module offsets_mod
