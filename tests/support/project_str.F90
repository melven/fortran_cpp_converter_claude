!> Stand-in for the project's string helpers of the C bindings (test support only).
module project_str
  use, intrinsic :: iso_c_binding
  implicit none
  integer, parameter :: label_len = 64
contains
  impure elemental function str_array_ptr_to_str(str_a_ptr) result(str)
    type(C_PTR), intent(in) :: str_a_ptr
    character(len=label_len) :: str
    character(kind=C_CHAR), pointer :: chars(:)
    integer :: i
    str = ''
    if (.not. c_associated(str_a_ptr)) return
    call c_f_pointer(str_a_ptr, chars, [label_len])
    do i = 1, label_len
      if (chars(i) == C_NULL_CHAR) exit
      str(i:i) = chars(i)
    end do
  end function str_array_ptr_to_str
end module project_str
