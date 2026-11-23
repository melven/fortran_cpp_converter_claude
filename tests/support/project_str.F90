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

  !> new C string (copy of s), freed by c_str_free
  function str_to_c_str(s) result(p)
    character(len=*), intent(in) :: s
    type(C_PTR) :: p
    character(kind=C_CHAR), pointer :: buf(:)
    integer :: i
    allocate(buf(len(s) + 1))
    do i = 1, len(s)
      buf(i) = s(i:i)
    end do
    buf(len(s) + 1) = C_NULL_CHAR
    p = c_loc(buf)
  end function str_to_c_str

  !> frees a C string of str_to_c_str
  subroutine c_str_free(p)
    type(C_PTR), intent(inout) :: p
    character(kind=C_CHAR), pointer :: buf(:)
    integer :: n
    if (.not. c_associated(p)) return
    call c_f_pointer(p, buf, [huge(n)])
    n = 1
    do while (buf(n) /= C_NULL_CHAR)
      n = n + 1
    end do
    call c_f_pointer(p, buf, [n])
    deallocate(buf)
    p = C_NULL_PTR
  end subroutine c_str_free
end module project_str
