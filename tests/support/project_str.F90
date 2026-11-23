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

  !> new C string buffer (copy of fStr), deleted by deleteString
  function copyString(fStr, ierr) result(cStr)
    character(len=*), intent(in) :: fStr
    integer, intent(out) :: ierr
    character(kind=C_CHAR), pointer, contiguous :: cStr(:)
    integer :: i
    allocate(cStr(len(fStr) + 1), stat=ierr)
    if (ierr /= 0) return
    do i = 1, len(fStr)
      cStr(i) = fStr(i:i)
    end do
    cStr(len(fStr) + 1) = C_NULL_CHAR
  end function copyString

  !> deletes a C string buffer of copyString
  subroutine deleteString(cStr_ptr)
    type(C_PTR), intent(inout) :: cStr_ptr
    character(kind=C_CHAR), pointer :: buf(:)
    integer :: n
    if (.not. c_associated(cStr_ptr)) return
    call c_f_pointer(cStr_ptr, buf, [huge(n)])
    n = 1
    do while (buf(n) /= C_NULL_CHAR)
      n = n + 1
    end do
    call c_f_pointer(cStr_ptr, buf, [n])
    deallocate(buf)
    cStr_ptr = C_NULL_PTR
  end subroutine deleteString
end module project_str
