module a_mod
  implicit none
  !> generic serialisation base: exists in Fortran and in C++
  type, abstract :: A_type
  contains
    procedure(write_iface), deferred :: write_state
  end type A_type
  abstract interface
    subroutine write_iface(this, ierr)
      import :: A_type
      class(A_type), intent(in) :: this
      integer, intent(out) :: ierr
    end subroutine write_iface
  end interface
end module a_mod
