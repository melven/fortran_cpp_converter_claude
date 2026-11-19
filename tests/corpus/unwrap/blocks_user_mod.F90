module blocks_user_mod
  use blocks_mod, only: Blocks_type
  implicit none
contains
  integer function sum_offsets(b)
    type(Blocks_type), intent(in) :: b
    integer :: i
    sum_offsets = 0
    do i = 1, size(b%arr)
      sum_offsets = sum_offsets + b%arr(i)%offset1
    end do
  end function sum_offsets
end module blocks_user_mod
