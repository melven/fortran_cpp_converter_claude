!> offsets of a block (translated; a constant of a wrapped class)
module offsets_mod
  implicit none
  type :: Offsets_type
    integer :: row = 0
    integer :: col = 0
    real :: w(2) = [1.0, 1.0]
    real, allocatable :: scale(:)
    integer, allocatable :: idx(:)
    logical :: mask(2) = .true.
    logical, allocatable :: active(:)
    character(len=8) :: tag = ''       ! not needed in C++: excluded
  end type Offsets_type
end module offsets_mod
